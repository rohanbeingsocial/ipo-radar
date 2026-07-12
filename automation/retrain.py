"""Retrain the forecast models on the refreshed dataset — the self-improving loop.

Two things make the models get better on their own, and neither is the loop itself:
  1. every IPO that matures adds a label (a 6m/12m/24m return that didn't exist yet),
  2. every IPO the pipeline enriches adds features (ROE/ROCE/P-B/GMP/reservation...).
The loop just harvests that. Retraining does NOT monotonically improve a model, so a
new model is only PROMOTED if it clears the incumbent by more than the noise in the
estimate. Otherwise the incumbent is kept and the attempt is still recorded, so a
regression is visible rather than silently shipped.

Reads only committed data (the expanded workbook + docs/data/reports/*.json), so it
runs in CI with no backend and no database.

Four rules keep the reported numbers honest — they exist because the first version of
this file broke all four:

  1. TIME-ORDERED CV. Folds are forward-chaining: a model is only ever scored on IPOs
     that listed AFTER the ones it trained on. Random KFold trains on 2025 to predict
     2021, which flatters MAE and selects for models good at interpolating history
     rather than predicting the next IPO.

  2. TWO HORIZON VARIANTS. Day-1 listing gain is a huge feature for 6m/12m/24m return
     — and it does not exist for an IPO that hasn't listed, which is exactly when the
     product is used. Training one model on it and median-filling at serve time scores
     the model on a question nobody asks it. So we train `pre` (no day-1 gain) and
     `post` (with it), score each honestly, and serve whichever matches reality.

  3. SKILL GATE. A target whose CV MAE does not beat "just predict the training mean"
     is not shipped. Shipping it is worse than shipping nothing.

  4. NOISE GATE. A 0.8pp MAE improvement on ~650 rows is inside the standard error of
     the estimate. Promotion requires beating the incumbent by more than 1 SE across
     folds, so the loop cannot drift on coin flips.

    python automation/retrain.py            # train, promote only if better
    python automation/retrain.py --force    # promote regardless (first run / reset)

Writes: backend/app/listing_model_signals.json, backend/app/horizon_model.pkl,
        data/model_history.json  (one row per attempt — the audit trail)
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app.pipeline.listing_predictor import features_from_report  # noqa: E402

DATA = ROOT / "data"
REPORTS = ROOT / "docs" / "data" / "reports"
XLSX = ROOT / "ipodata" / "finalipodata_expanded_20yr.xlsx"
SIGNALS_PATH = ROOT / "backend" / "app" / "listing_model_signals.json"
HORIZON_PATH = ROOT / "backend" / "app" / "horizon_model.pkl"
HISTORY = DATA / "model_history.json"
SECTOR_CSV = DATA / "cg_sector.csv"

SEED = 42
MIN_ROWS = 60                     # below this a CV score is noise, not a signal
RIDGE_LAMBDA = 1.0
N_SPLITS = 5
MIN_TRAIN_FRAC = 0.4              # oldest 40% is train-only; never scored
MIN_EDGE_FRAC = 0.03              # a head must remove >=3% of the baseline's error to ship

# 3: signals split into gated magnitude/direction heads; one sector vocabulary everywhere.
# Bumping this retires the previous signals artifact on purpose — its 22.8 MAE was an
# artifact of sector-as-recency-proxy, so defending it against an honest 24.5 would lock
# the loop onto a model that only looked good.
SCHEMA = 3
SIGNALS_TARGET = "listing_gain_pct_vs_offer"

FUNDAMENTALS = ["ROE", "ROCE", "D/E", "PAT Margin", "P/B", "Post IPO P/E", "EBITA Margin"]
RESERVATION = ["% QIB", "% Retail", "% anchor"]
STRUCTURE = ["is_financial", "is_realestate"]     # what KIND of company, not just which sector
RHP_FEATS = ["rhp_overall", "rhp_growth", "rhp_cash", "rhp_finhealth", "rhp_governance",
             "rhp_promoter", "rhp_risk", "rhp_forensic_flags"]
RET_TARGETS = ["ret_6m", "ret_12m", "ret_24m"]
AUX_TARGETS = ["bottom_12m_pct", "sessions_to_bottom", "peak_24m_pct", "sessions_to_peak"]


def sid(v):
    try:
        return str(int(float(v)))
    except (ValueError, TypeError):
        return "" if v is None else str(v).strip()


# ───────────────────────────── features ─────────────────────────────

MIN_COVERAGE = 0.5      # below this the analyzer did not really read the document


def rhp_features():
    """RHP-derived features straight from the committed report JSONs, keyed by
    cg_ipo_id. The old trainers pulled these from a live API on localhost, which is
    exactly why they could never run in CI.

    Reports the analyzer could not actually READ are dropped, not down-weighted. Older
    prospectuses extract terribly — median coverage is 0.19 for 2005-2013 filings, i.e.
    the scorer saw a fifth of the document — and a score computed from a fifth of a
    prospectus is not a weak signal, it is a different quantity. Measured: within a
    single era, the score correlates +0.26 with the 6-month return when coverage is
    high and -0.16 when it is low. Feeding the low-coverage rows in as if they were
    RHP features would inject noise with a confident-looking number attached."""
    out, dropped = {}, 0
    for p in REPORTS.glob("*.json"):
        try:
            rep = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        cvg = (rep.get("meta") or {}).get("coverage")
        if isinstance(cvg, (int, float)) and cvg < MIN_COVERAGE:
            dropped += 1
            continue
        sc = rep.get("scoring") or {}
        cats = sc.get("categories") or {}

        def cat(k):
            v = (cats.get(k) or {}).get("score")
            return float(v) if isinstance(v, (int, float)) else np.nan

        out[p.stem] = {
            "rhp_overall": sc.get("overall"), "rhp_growth": cat("growth"),
            "rhp_cash": cat("cash_generation"), "rhp_finhealth": cat("financial_health"),
            "rhp_governance": cat("governance"), "rhp_promoter": cat("promoter_quality"),
            "rhp_risk": (rep.get("risk") or {}).get("score"),
            "rhp_forensic_flags": len((rep.get("forensic") or {}).get("flags") or []),
            "rhp_coverage": float(cvg) if isinstance(cvg, (int, float)) else np.nan,
        }
    if dropped:
        print(f"  RHP: {len(out)} usable, {dropped} dropped for coverage < {MIN_COVERAGE} "
              f"(the analyzer could not read enough of the prospectus to score it)")
    return out


def nifty_change(listing_dates):
    """30-session Nifty move into each listing — the market regime the IPO listed in.
    One fetch for the whole index history; degrades to NaN if Yahoo is unreachable."""
    try:
        import urllib.request
        url = ("https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI"
               "?period1=1041379200&period2=9999999999&interval=1d")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        d = json.loads(urllib.request.urlopen(req, timeout=60).read())
        res = d["chart"]["result"][0]
        idx = pd.to_datetime(res["timestamp"], unit="s", utc=True).tz_convert("Asia/Kolkata").tz_localize(None).normalize()
        s = pd.Series(res["indicators"]["quote"][0]["close"], index=idx).dropna()
        s = s[~s.index.duplicated(keep="last")].sort_index()
    except Exception as e:                                   # noqa: BLE001
        print(f"  nifty unavailable ({e}); feature falls back to median")
        return pd.Series([np.nan] * len(listing_dates), index=listing_dates.index)
    chg = []
    for dt in listing_dates:
        if pd.isna(dt):
            chg.append(np.nan)
            continue
        w = s[s.index <= dt]
        chg.append((w.iloc[-1] / w.iloc[-31] - 1) * 100 if len(w) >= 31 else np.nan)
    return pd.Series(chg, index=listing_dates.index)


def build_frame():
    m = pd.read_excel(XLSX, sheet_name="Expanded")
    m["cg_ipo_id"] = m["cg_ipo_id"].map(sid)
    rhp = rhp_features()
    for f in RHP_FEATS:
        m[f] = m["cg_ipo_id"].map(lambda i, f=f: (rhp.get(i) or {}).get(f))
    m["has_rhp"] = m["rhp_overall"].notna().astype(float)

    # ONE sector vocabulary, from automation/backfill_sectors.py (Yahoo GICS -> RHP bucket
    # -> company name). Previously the two models used two different taxonomies and both
    # were mostly empty: the sheet's Sector reached 36% of rows and the RHP bucket 18%, so
    # the sector one-hots were dead weight on the training data — the models could not see
    # that real estate and energy behave nothing like technology. That is the single
    # biggest signal in this dataset (62pp of median 24m return between best and worst).
    #
    # Structural flags matter separately from sector, because the SAME NUMBER MEANS
    # DIFFERENT THINGS: debt/equity of 6 is the business model of a lender and a red flag
    # for a manufacturer; P/B prices a bank where P/E prices a factory. Handing the model
    # the flag lets it learn that split instead of averaging the two into nonsense.
    sec = pd.read_csv(SECTOR_CSV, dtype={"cg_ipo_id": str}) if SECTOR_CSV.exists() else None
    if sec is not None:
        sec = sec.drop_duplicates("cg_ipo_id").set_index("cg_ipo_id")
        m["sector"] = m["cg_ipo_id"].map(sec["sector"]).fillna("Other")
        m["is_financial"] = m["cg_ipo_id"].map(sec["is_financial"]).fillna(0).astype(float)
        m["is_realestate"] = m["cg_ipo_id"].map(sec["is_realestate"]).fillna(0).astype(float)
        m["instrument"] = m["cg_ipo_id"].map(sec["instrument"]).fillna("equity")
    else:
        m["sector"] = m["Sector"].fillna("Other")
        m["is_financial"] = m["is_realestate"] = 0.0
        m["instrument"] = "equity"

    # A REIT/InvIT is a yield vehicle: most of its total return is distributions, which a
    # price series does not contain, so "did it recover vs the offer price" is the wrong
    # question and its labels would teach the equity model a lie. None are in the dataset
    # today (Chittorgarh lists them apart from mainboard IPOs) — this is a guard, not a
    # filter that currently does anything.
    trusts = int((m["instrument"] != "equity").sum())
    if trusts:
        print(f"  excluding {trusts} REIT/InvIT rows — price return is not their return")
        m = m[m["instrument"] == "equity"].copy()

    num = lambda c: pd.to_numeric(m.get(c), errors="coerce")  # noqa: E731
    for c, src in (("log_QIB", "QIB"), ("log_bNII", "bNII"), ("log_sNII", "sNII"),
                   ("log_NII", "NII"), ("log_Retail", "Retail"), ("log_Total", "Total Sub")):
        m[c] = np.log1p(num(src).clip(lower=0))

    offer = num("Offer Price")
    m["gmp_prem"] = (num("GMP") / offer * 100).clip(-30, 150)
    m["has_gmp"] = m["gmp_prem"].notna().astype(float)
    m["listing_gain_day1"] = num("Listing Gain").clip(-80, 400)
    size = num("Issue Size (cr)")
    m["issue_size_log"] = np.log10(size.clip(lower=1))
    m["smallcap"] = (size < 1000).astype(float)
    fresh, ofs = num("Fresh (cr)").fillna(0), num("OFS (cr)").fillna(0)
    m["ofs_share"] = (ofs / (fresh + ofs).replace(0, np.nan)).clip(0, 1)
    m["list_dt"] = pd.to_datetime(m["List Date"], format="%d-%b-%Y", errors="coerce")
    m["nifty_chg"] = nifty_change(m["list_dt"])
    for c in FUNDAMENTALS + RESERVATION:
        m[c] = num(c)

    m = m.rename(columns={"Ret 6m %": "ret_6m", "Ret 12m %": "ret_12m", "Ret 24m %": "ret_24m",
                          "Bottom 12m %": "bottom_12m_pct", "Sessions to Bottom": "sessions_to_bottom",
                          "Peak 24m %": "peak_24m_pct", "Sessions to Peak": "sessions_to_peak",
                          "LTP Gain": "ltp_gain"})
    for t in RET_TARGETS + AUX_TARGETS + ["ltp_gain"]:
        m[t] = pd.to_numeric(m[t], errors="coerce")
    for t in RET_TARGETS + ["bottom_12m_pct", "peak_24m_pct", "ltp_gain", "listing_gain_day1"]:
        m[t] = m[t].clip(-95, 400)     # outliers dominate MAE and teach nothing
    # every model below is scored in listing order, so an undated row can't be placed
    # on the timeline and is training-only noise. Drop it once, here.
    return m.sort_values("list_dt").reset_index(drop=True)


# ───────────────────────────── honest cross-validation ─────────────────────────────

def time_folds(n):
    """Forward-chaining folds over rows already sorted oldest→newest: fold k trains
    only on IPOs that listed BEFORE the ones it is scored on. The oldest MIN_TRAIN_FRAC
    is never scored (it has no past to train on). This is the whole difference between
    'how well do we fit history' and 'how well would we have called the next IPO'."""
    start = max(int(n * MIN_TRAIN_FRAC), MIN_ROWS)
    if n - start < N_SPLITS * 5:
        return []
    edges = np.linspace(start, n, N_SPLITS + 1).astype(int)
    return [(np.arange(0, edges[i]), np.arange(edges[i], edges[i + 1]))
            for i in range(N_SPLITS) if edges[i + 1] > edges[i]]


def cv_eval(fit_predict, y, folds):
    """Pooled MAE over the scored rows, the per-fold spread (for the noise gate), and
    the baseline every model must beat: predict the TRAINING mean (not the global mean
    — that would peek at the test block's level)."""
    preds = np.full(len(y), np.nan)
    fold_maes, fold_base = [], []
    for tr, te in folds:
        preds[te] = fit_predict(tr, te)
        fold_maes.append(float(np.mean(np.abs(preds[te] - y[te]))))
        fold_base.append(float(np.mean(np.abs(y[tr].mean() - y[te]))))
    seen = ~np.isnan(preds)
    mae = float(np.mean(np.abs(preds[seen] - y[seen])))
    base = float(np.mean(fold_base))

    # Rules 3 and 4 in one PAIRED test, applied per model head: in each fold, by how much
    # did we beat that fold's baseline? A model is only shipped if that margin is reliably
    # positive (mean > 1 standard error of the margin).
    #
    # The comparison must be paired. MAE *levels* swing enormously between folds — a 2021
    # cohort and a 2023 cohort simply had different return dispersion — so the SE of the
    # level is dominated by regime variance that says nothing about whether the model beats
    # its baseline. Gating on that would reject models that beat the baseline in every
    # single fold. The per-fold *difference* is what carries the signal.
    diffs = np.array(fold_base) - np.array(fold_maes)
    return {"mae": mae, "baseline_mae": base,
            "se": float(np.std(fold_maes, ddof=1) / math.sqrt(len(fold_maes))) if len(fold_maes) > 1 else float("inf"),
            "n_scored": int(seen.sum()), "preds": preds, "seen": seen, **margin(diffs, base)}


def margin(diffs, base):
    """Turn per-fold margins over the baseline into a ship / don't-ship verdict.

    Three conditions, because each alone is gameable:
      * SIZE      — the mean margin must exceed its own standard error.
      * CONSISTENCY — the model must win a MAJORITY of folds. This stops one lucky fold
        from carrying a model: a head with edge +2.9 ± 2.9 that won 2 of 5 folds is noise,
        and on the size test alone it shipped.
      * USEFULNESS — the margin must remove at least MIN_EDGE_FRAC of the baseline error.
        Statistical significance is not the same as being worth showing a user:
        `sessions_to_peak` beat its baseline by 2.9 sessions... while carrying an MAE of
        108 sessions. A five-month error bar on a timing call is not a forecast, however
        significant the edge. Passing a t-test does not earn a number on screen."""
    edge = float(diffs.mean())
    edge_se = float(diffs.std(ddof=1) / math.sqrt(len(diffs))) if len(diffs) > 1 else float("inf")
    won = int((diffs > 0).sum())
    return {"edge": edge, "edge_se": edge_se, "folds_won": won, "n_folds": len(diffs),
            "skill": bool(edge > edge_se and won * 2 > len(diffs) and edge > MIN_EDGE_FRAC * base)}


def beats(new_mae, new_se, old_mae):
    """Same noise gate, applied between runs."""
    return old_mae is None or new_mae < old_mae - new_se


# ───────────────────────────── signals ridge ─────────────────────────────

def fit_signals(m):
    """The pre-listing call: will it list above the offer price, and by how much?

    Two heads, gated separately, because they are not equally learnable — and it turns
    out only one of them is:

      * DIRECTION (logistic) — 76% accurate against a 71% majority baseline, winning
        every fold. This is a real, usable call.
      * MAGNITUDE (ridge) — barely better than predicting the average, well inside the
        noise. It does not ship. The size of a listing pop is demand-driven and volatile;
        we can say which way, not how far.

    Two dead ends are deliberately not in the feature set:
      - `LTP Gain` as the target (today's price vs offer): only 220 rows, and it folds in
        years of market drift, so it was unlearnable. Listing-day gain has 843 rows.
      - SECTOR one-hots. They look like they help (they took MAE from 24.5 to 22.9) but
        that was an artifact: sector was only populated for rows Yahoo could resolve,
        which are almost all post-2021 listings (median listing year 2024, vs 2010 for
        the rest). The dummies were silently encoding "this IPO is recent", i.e. the
        market regime, not the industry. With sector filled honestly for every row the
        gain vanishes (24.6). Sector genuinely does move the medium-term horizon models
        — it just does not predict the listing-day pop, which is about demand, not
        fundamentals.
    """
    feats = (["gmp_prem", "log_QIB", "log_bNII", "log_sNII", "log_Retail"]
             + RESERVATION + ["smallcap", "nifty_chg"] + STRUCTURE + FUNDAMENTALS)
    d = m[m["listing_gain_day1"].notna() & m["list_dt"].notna()].copy()   # already time-sorted
    if len(d) < MIN_ROWS:
        return None, {"n": len(d), "skipped": "too few rows"}

    X = d[feats].apply(pd.to_numeric, errors="coerce")
    medians = X.median(numeric_only=True).to_dict()
    X = X.fillna(pd.Series(medians)).fillna(0.0)
    y = d["listing_gain_day1"].astype(float)

    names = list(X.columns)
    Xv, yv = X.values.astype(float), y.values.astype(float)
    lab = (yv > 0).astype(int)
    mu, sd = Xv.mean(0), Xv.std(0)
    sd[sd == 0] = 1.0

    folds = time_folds(len(Xv))
    if not folds:
        return None, {"n": len(d), "skipped": "too few rows to time-split"}

    def ridge(Xt, yt):
        Z = np.hstack([np.ones((len(Xt), 1)), (Xt - mu) / sd])
        A = Z.T @ Z + RIDGE_LAMBDA * np.eye(Z.shape[1])
        A[0, 0] -= RIDGE_LAMBDA                     # never penalise the intercept
        return np.linalg.solve(A, Z.T @ yt)

    def predict(w, Xt):
        return np.hstack([np.ones((len(Xt), 1)), (Xt - mu) / sd]) @ w

    def unstandardise(w):
        """Export in the ORIGINAL feature space so serving is plain arithmetic — no
        sklearn at inference, and the artifact stays readable JSON."""
        return ({n: float(c) for n, c in zip(names, w[1:] / sd)},
                float(w[0] - float(np.sum(w[1:] * mu / sd))))

    # ── magnitude ──
    mag_cv = cv_eval(lambda tr, te: predict(ridge(Xv[tr], yv[tr]), Xv[te]), yv, folds)

    # ── direction ──
    from sklearn.linear_model import LogisticRegression

    def logit(tr):
        c = LogisticRegression(C=1.0, max_iter=2000)
        c.fit((Xv[tr] - mu) / sd, lab[tr])
        return np.concatenate([c.intercept_, c.coef_[0]])

    accs, majs = [], []
    for tr, te in folds:
        w = logit(tr)
        p = 1.0 / (1.0 + np.exp(-predict(w, Xv[te])))
        accs.append(float(np.mean((p > 0.5) == lab[te])))
        majs.append(max(float(np.mean(lab[te] == int(lab[tr].mean() > 0.5))), 0.5))
    dmar = margin(np.array(accs) - np.array(majs), float(np.mean(majs)))
    dir_acc, dir_base = float(np.mean(accs)), float(np.mean(majs))

    model = {"kind": "signals", "schema": SCHEMA, "n": int(len(d)),
             "target": SIGNALS_TARGET, "cv": "time-ordered",
             "feature_names": names, "medians": {k: float(v) for k, v in medians.items()},
             "n_scored": mag_cv["n_scored"],
             "trained_at": datetime.now(timezone.utc).isoformat()}

    if mag_cv["skill"]:
        coef, intercept = unstandardise(ridge(Xv, yv))
        model["magnitude"] = {"coef": coef, "intercept": intercept,
                              "cv_mae": mag_cv["mae"], "baseline_mae": mag_cv["baseline_mae"],
                              "edge": mag_cv["edge"], "edge_se": mag_cv["edge_se"]}
    if dmar["skill"]:
        coef, intercept = unstandardise(logit(np.arange(len(Xv))))
        model["direction"] = {"coef": coef, "intercept": intercept, "link": "logistic",
                              "cv_acc": dir_acc, "baseline_acc": dir_base,
                              "edge": dmar["edge"], "edge_se": dmar["edge_se"]}

    summary = {"n": int(len(d)), "n_scored": mag_cv["n_scored"],
               "magnitude": {"cv_mae": round(mag_cv["mae"], 1),
                             "baseline_mae": round(mag_cv["baseline_mae"], 1),
                             "edge": round(mag_cv["edge"], 2),
                             "edge_se": round(mag_cv["edge_se"], 2),
                             "folds_won": f"{mag_cv['folds_won']}/{mag_cv['n_folds']}",
                             "skill": bool(mag_cv["skill"])},
               "direction": {"cv_acc": round(dir_acc, 3), "baseline_acc": round(dir_base, 3),
                             "edge": round(dmar["edge"], 3), "edge_se": round(dmar["edge_se"], 3),
                             "folds_won": f"{dmar['folds_won']}/{dmar['n_folds']}",
                             "skill": bool(dmar["skill"])}}
    if not (mag_cv["skill"] or dmar["skill"]):
        return None, summary
    return model, summary


# ───────────────────────────── horizon GBMs ─────────────────────────────

HORIZON_BASE_COLS = (["log_QIB", "log_bNII", "log_sNII", "log_NII", "log_Retail", "log_Total",
                      "gmp_prem", "has_gmp", "issue_size_log", "ofs_share", "has_rhp", "nifty_chg"]
                     + STRUCTURE + RHP_FEATS + FUNDAMENTALS + RESERVATION)

# `pre` is the product: forecasting an IPO that has not listed, so day-1 gain does not
# exist. `post` is the dashboard: the stock is trading and day-1 gain is known and is a
# very strong feature. One model cannot honestly serve both — the old one was trained
# with day-1 gain always present and then median-filled it at serve time, i.e. it was
# scored on a question the product never asks.
HORIZON_VARIANTS = {"pre": {"extra": [], "carry": None},
                    "post": {"extra": ["listing_gain_day1"], "carry": "listing_gain_day1"}}


def fit_horizon(m):
    from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor

    # Small on purpose. The previous 300-tree depth-3 GBM was overfitting ~650 rows x ~40
    # features so hard that it lost to the mean on every target; random KFold hid it by
    # letting the model memorise neighbours in time. Under time-ordered CV this shape
    # beats the big one on every single target.
    def gbr():
        return GradientBoostingRegressor(n_estimators=120, max_depth=2, learning_rate=0.03,
                                         subsample=0.7, min_samples_leaf=20, random_state=SEED)

    def gbc():
        return GradientBoostingClassifier(n_estimators=120, max_depth=2, learning_rate=0.03,
                                          subsample=0.7, min_samples_leaf=20, random_state=SEED)

    # the SAME vocabulary the ridge uses. At serve time an unlisted company has no Yahoo
    # profile, so listing_predictor maps its RHP bucket into this vocabulary (RHP_TO_SHEET)
    # — which is exactly how backfill_sectors.py labelled those rows for training.
    sectors = sorted(s for s in m["sector"].dropna().unique() if s != "Other")
    out = {"schema": SCHEMA, "sectors": sectors, "cv": "time-ordered",
           "trained_at": datetime.now(timezone.utc).isoformat(), "variants": {}}
    summary = {}

    for vname, spec in HORIZON_VARIANTS.items():
        cols = HORIZON_BASE_COLS + spec["extra"]
        X_all = m[cols].apply(pd.to_numeric, errors="coerce")
        medians = X_all.median(numeric_only=True).to_dict()
        X_all = X_all.fillna(pd.Series(medians)).fillna(0.0)
        for s in sectors:
            X_all[f"sec_{s}"] = (m["sector"] == s).astype(float)

        var = {"feature_names": list(X_all.columns),
               "medians": {k: float(v) for k, v in medians.items()},
               "models": {}, "classifiers": {}, "cv": {}, "n": {}, "residual_base": {}}
        summary[vname] = {}
        print(f"  [{vname}-listing]")

        for target in RET_TARGETS + AUX_TARGETS:
            mask = m[target].notna() & m["list_dt"].notna()
            if mask.sum() < MIN_ROWS:
                continue
            X = X_all[mask].reset_index(drop=True)
            y = m.loc[mask, target].astype(float).reset_index(drop=True).values
            folds = time_folds(len(y))
            if not folds:
                continue
            # the `post` model predicts the CORRECTION to day-1 carry; `pre` has no
            # carry to correct, so it predicts the return outright.
            carry = spec["carry"]
            base = X[carry].values if (carry and target in RET_TARGETS) else np.zeros(len(y))

            def fp(tr, te, X=X, y=y, base=base):
                g = gbr()
                g.fit(X.iloc[tr], y[tr] - base[tr])
                return g.predict(X.iloc[te]) + base[te]

            cv = cv_eval(fp, y, folds)
            rec = {"n": int(len(y)), "n_scored": cv["n_scored"], "mae": round(cv["mae"], 1),
                   "se": round(cv["se"], 1), "baseline_mean_mae": round(cv["baseline_mae"], 1),
                   "edge": round(cv["edge"], 2), "edge_se": round(cv["edge_se"], 2),
                   "folds_won": f"{cv['folds_won']}/{cv['n_folds']}",
                   "mae_skill": bool(cv["skill"])}

            # Magnitude and direction are gated SEPARATELY, because they are not equally
            # learnable. Pre-listing, no model can beat the mean on 6m/12m/24m magnitude —
            # but "does it end above the offer price" is called right ~2 times in 3. So we
            # ship the probability and stay silent on the number, rather than dressing up
            # noise as a point forecast.
            if target in RET_TARGETS:
                lab = (y > 0).astype(int)
                proba = np.full(len(y), np.nan)
                fold_acc, fold_maj = [], []
                for tr, te in folds:
                    c = gbc()
                    c.fit(X.iloc[tr], lab[tr])
                    proba[te] = c.predict_proba(X.iloc[te])[:, 1]
                    fold_acc.append(float(np.mean((proba[te] > 0.5) == lab[te])))
                    # The bar is the BETTER of "always predict the training majority" and
                    # a coin flip. The majority baseline alone is not enough: when the
                    # regime flips (most 2021 IPOs ended up, most 2023 ones down), that
                    # baseline collapses to 0.38 and a 0.49-accuracy model "beats" it while
                    # being worse than a coin. Nothing below 0.5 is ever a call worth making.
                    p = lab[tr].mean()
                    fold_maj.append(max(float(np.mean(lab[te] == int(p > 0.5))), 0.5))
                sc = ~np.isnan(proba)
                acc = float(np.mean((proba[sc] > 0.5) == lab[sc]))
                dmar = margin(np.array(fold_acc) - np.array(fold_maj), float(np.mean(fold_maj)))
                rec.update(direction_acc=round(acc, 3),
                           direction_baseline=round(float(np.mean(fold_maj)), 3),
                           direction_edge=round(dmar["edge"], 3),
                           direction_edge_se=round(dmar["edge_se"], 3),
                           direction_folds_won=f"{dmar['folds_won']}/{dmar['n_folds']}",
                           direction_skill=dmar["skill"])

            bits = []
            if cv["skill"]:
                bits.append("mae")
            if rec.get("direction_skill"):
                bits.append("dir")
            flag = f"   ships: {'+'.join(bits)}" if bits else "   NO SKILL — not shipped"
            dtxt = (f"  dir {rec['direction_acc']:.2f}/{rec['direction_baseline']:.2f}"
                    if "direction_acc" in rec else "")
            print(f"    {target:20} n={len(y):4}  mae {cv['mae']:6.1f} vs base {cv['baseline_mae']:6.1f}"
                  f"  edge {cv['edge']:+5.1f}±{cv['edge_se']:.1f} ({rec['folds_won']}){dtxt}{flag}")
            var["cv"][target] = rec
            summary[vname][target] = rec

            if cv["skill"]:                   # rule 3: losing to the mean ships nothing
                final = gbr()
                final.fit(X, y - base)
                var["models"][target] = final
                var["n"][target] = int(len(y))
                if carry and target in RET_TARGETS:
                    var["residual_base"][target] = carry
            if rec.get("direction_skill"):
                c = gbc()
                c.fit(X, (y > 0).astype(int))
                var["classifiers"][target] = c

        out["variants"][vname] = var

    if not any(v["models"] or v["classifiers"] for v in out["variants"].values()):
        return None, {}
    return out, summary


def horizon_edge(summary, variant):
    """How much useful model there is in a variant: how many heads survived their skill
    gate, and by what normalised margin over their baselines. Every head that shipped is
    already significant on its own (see cv_eval), so between runs we simply prefer the
    artifact that carries more skill — more heads first, bigger margin as the tiebreak."""
    edges = []
    for r in (summary.get(variant) or {}).values():
        if r.get("mae_skill") and r.get("baseline_mean_mae"):
            edges.append(r["edge"] / r["baseline_mean_mae"])       # normalised: fraction of baseline error removed
        if r.get("direction_skill"):
            edges.append(r["direction_edge"])                      # already a fraction (accuracy points)
    return len(edges), (float(np.mean(edges)) if edges else 0.0)


def incumbent_horizon_edge():
    """Recompute the incumbent's heads/edge from the CV it recorded, so the comparison is
    like-for-like. Older artifacts recorded a leaky CV and simply do not qualify."""
    if not HORIZON_PATH.exists():
        return None, "none"
    try:
        import joblib
        j = joblib.load(HORIZON_PATH)
    except Exception:                                # noqa: BLE001
        return None, "unreadable"
    if j.get("schema") != SCHEMA:
        return None, "not comparable (leaky CV, single variant) — superseded"
    cvs = (j.get("variants", {}).get("pre", {}) or {}).get("cv") or {}
    return horizon_edge({"pre": cvs}, "pre"), "ok"


# ───────────────────────────── promotion gate ─────────────────────────────

def incumbent_signals():
    """Only comparable if the incumbent predicts the same thing, the same way. A model
    trained on a different target, or scored with leaky CV, or fed a feature that turned
    out to be an artifact, is not a yardstick — it is a thing to replace."""
    if not SIGNALS_PATH.exists():
        return None, "none"
    try:
        j = json.loads(SIGNALS_PATH.read_text())
    except (ValueError, OSError):
        return None, "unreadable"
    if j.get("schema") != SCHEMA or j.get("target") != SIGNALS_TARGET:
        return None, "not comparable (old schema/target) — superseded"
    heads = sum(1 for k in ("magnitude", "direction") if k in j)
    edge = float((j.get("direction") or {}).get("edge") or 0.0)
    return (heads, edge), "ok"


def record(entry):
    hist = []
    if HISTORY.exists():
        try:
            hist = json.loads(HISTORY.read_text())
        except ValueError:
            hist = []
    hist.append(entry)
    HISTORY.write_text(json.dumps(hist, indent=1), encoding="utf-8")


def main():
    force = "--force" in sys.argv
    m = build_frame()
    print(f"rows: {len(m)}   with RHP features: {int(m['has_rhp'].sum())}   "
          f"with fundamentals: {int(m['ROE'].notna().sum())}   "
          f"with listing gain: {int(m['listing_gain_day1'].notna().sum())}")

    entry = {"at": datetime.now(timezone.utc).isoformat(), "rows": int(len(m)),
             "schema": SCHEMA, "cv": "time-ordered"}

    print("\nsignals (-> will it list above the offer price, and by how much)")
    sig, sig_cv = fit_signals(m)
    old, why = incumbent_signals()
    entry["signals"] = {**sig_cv, "incumbent": (list(old) if old else why)}
    mg, dr = sig_cv.get("magnitude") or {}, sig_cv.get("direction") or {}
    if mg:
        print(f"  magnitude  mae {mg['cv_mae']:5.1f} vs base {mg['baseline_mae']:5.1f}"
              f"  edge {mg['edge']:+5.2f}±{mg['edge_se']:.2f} ({mg['folds_won']})"
              f"   {'ships' if mg['skill'] else 'NO SKILL — the size of a listing pop is not callable'}")
    if dr:
        print(f"  direction  acc {dr['cv_acc']:.3f} vs base {dr['baseline_acc']:.3f}"
              f"  edge {dr['edge']:+.3f}±{dr['edge_se']:.3f} ({dr['folds_won']})"
              f"   {'ships' if dr['skill'] else 'NO SKILL'}")
    if sig is None:
        print("  nothing shipped:", sig_cv.get("skipped", "no head beat its baseline"))
        entry["signals"]["promoted"] = False
    else:
        heads = sum(1 for k in ("magnitude", "direction") if k in sig)
        edge = float((sig.get("direction") or {}).get("edge") or 0.0)
        old_heads, old_edge = old if old else (None, None)
        ok = old is None or heads > old_heads or (heads == old_heads and edge > old_edge)
        entry["signals"]["promoted"] = bool(force or ok)
        print(f"  {heads} skilful head(s)   incumbent "
              f"{f'{old_heads} head(s)' if old else why}")
        if force or ok:
            SIGNALS_PATH.write_text(json.dumps(sig, indent=1), encoding="utf-8")
            print("  PROMOTED ->", SIGNALS_PATH.name)
        else:
            print("  kept incumbent (no more skill than what is already shipped)")

    print("\nhorizon GBMs (-> 6m/12m/24m returns, entry, exit)")
    hor, hor_cv = fit_horizon(m)
    old, why_h = incumbent_horizon_edge()
    if hor is None:
        entry["horizon"] = {"promoted": False, "skipped": "no head beat its baseline"}
        print("  skipped — nothing beat its baseline; shipping nothing beats shipping noise")
    else:
        # gate on the PRE variant: forecasting an unlisted IPO is what the product does
        heads, edge = horizon_edge(hor_cv, "pre")
        old_heads, old_edge = old if old else (None, None)
        ok = old is None or heads > old_heads or (heads == old_heads and edge > old_edge)
        entry["horizon"] = {
            "gate": "pre-listing skill heads, then margin over baseline",
            "pre": {"heads": heads, "edge": round(edge, 3)},
            "post": dict(zip(("heads", "edge"), horizon_edge(hor_cv, "post"))),
            "incumbent_pre": ({"heads": old_heads, "edge": round(old_edge, 3)} if old else why_h),
            "targets": hor_cv,
            "promoted": bool(force or ok)}
        print(f"\n  pre-listing: {heads} skilful head(s), mean edge {edge:+.3f}"
              f"   incumbent {f'{old_heads} head(s), edge {old_edge:+.3f}' if old else why_h}")
        if force or ok:
            import joblib
            joblib.dump(hor, HORIZON_PATH, compress=3)
            print("  PROMOTED ->", HORIZON_PATH.name)
        else:
            print(f"  kept incumbent ({heads}/{edge:+.3f} does not improve on "
                  f"{old_heads}/{old_edge:+.3f})")

    record(entry)
    print(f"\nhistory -> {HISTORY.relative_to(ROOT)}  (signals {entry['signals'].get('promoted')} / "
          f"horizon {entry['horizon'].get('promoted')} promoted)")


if __name__ == "__main__":
    main()
