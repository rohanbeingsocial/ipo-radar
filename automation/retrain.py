"""Retrain the forecast models on the refreshed dataset — the self-improving loop.

Two things make the models get better on their own, and neither is the loop itself:
  1. every IPO that matures adds a label (a 6m/12m/24m return that didn't exist yet),
  2. every IPO the pipeline enriches adds features (ROE/ROCE/P-B/GMP/reservation...).
The loop just harvests that. Retraining does NOT monotonically improve a model, so a
new model is only PROMOTED if it beats the incumbent on cross-validated MAE. Otherwise
the incumbent is kept and the attempt is still recorded, so a regression is visible
rather than silently shipped.

Reads only committed data (the expanded workbook + docs/data/reports/*.json), so it
runs in CI with no backend and no database.

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

SEED = 42
MIN_ROWS = 60                     # below this a CV score is noise, not a signal
RIDGE_LAMBDA = 1.0

FUNDAMENTALS = ["ROE", "ROCE", "D/E", "PAT Margin", "P/B", "Post IPO P/E", "EBITA Margin"]
RESERVATION = ["% QIB", "% Retail", "% anchor"]
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

def rhp_features():
    """RHP-derived features straight from the committed report JSONs, keyed by
    cg_ipo_id. The old trainers pulled these from a live API on localhost, which is
    exactly why they could never run in CI."""
    out = {}
    for p in REPORTS.glob("*.json"):
        try:
            rep = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
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
            # the horizon model's sector one-hots must be the RHP buckets, because at
            # serve time that is the only sector available (the company isn't listed
            # yet, so there is no Yahoo profile). Training on the dataset's Sector
            # instead would leave every one-hot dead at inference.
            "sector_rhp": features_from_report(rep).get("sector") or "other",
        }
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
    m["sector_rhp"] = m["cg_ipo_id"].map(
        lambda i: (rhp.get(i) or {}).get("sector_rhp")).fillna("other")

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
    for t in RET_TARGETS + ["bottom_12m_pct", "peak_24m_pct", "ltp_gain"]:
        m[t] = m[t].clip(-95, 400)     # outliers dominate MAE and teach nothing
    m["sector"] = m["Sector"].fillna("Other")
    return m


# ───────────────────────────── signals ridge ─────────────────────────────

def fit_signals(m):
    """Ridge on the market-signal + fundamentals columns -> LTP gain vs offer.
    This is the model that lives or dies on the 14 enriched columns."""
    sectors = sorted(s for s in m["sector"].dropna().unique() if s != "Other")
    feats = (["gmp_prem", "log_QIB", "log_bNII", "log_sNII", "log_Retail"]
             + RESERVATION + ["smallcap", "nifty_chg"] + FUNDAMENTALS)
    d = m[m["ltp_gain"].notna()].copy()
    X = d[feats].apply(pd.to_numeric, errors="coerce")
    medians = X.median(numeric_only=True).to_dict()
    X = X.fillna(pd.Series(medians)).fillna(0.0)
    for s in sectors:
        X[f"sec_{s}"] = (d["sector"] == s).astype(float)
    y = d["ltp_gain"].astype(float)
    if len(d) < MIN_ROWS:
        return None, {"n": len(d), "skipped": "too few rows"}

    names = list(X.columns)
    Xv, yv = X.values.astype(float), y.values.astype(float)
    mu, sd = Xv.mean(0), Xv.std(0)
    sd[sd == 0] = 1.0

    def ridge(Xt, yt):
        Z = (Xt - mu) / sd
        Z = np.hstack([np.ones((len(Z), 1)), Z])
        A = Z.T @ Z + RIDGE_LAMBDA * np.eye(Z.shape[1])
        A[0, 0] -= RIDGE_LAMBDA                     # never penalise the intercept
        return np.linalg.solve(A, Z.T @ yt)

    def predict(w, Xt):
        Z = (Xt - mu) / sd
        return np.hstack([np.ones((len(Z), 1)), Z]) @ w

    rng = np.random.default_rng(SEED)
    order = rng.permutation(len(Xv))
    preds = np.zeros(len(Xv))
    for f in range(5):                              # 5-fold CV
        te = order[f::5]
        tr = np.setdiff1d(order, te)
        preds[te] = predict(ridge(Xv[tr], yv[tr]), Xv[te])
    mae = float(np.mean(np.abs(preds - yv)))
    base = float(np.mean(np.abs(yv.mean() - yv)))
    dir_acc = float(np.mean((preds > 0) == (yv > 0)))

    w = ridge(Xv, yv)
    coef = {n: float(c) for n, c in zip(names, w[1:] / sd)}
    intercept = float(w[0] - float(np.sum(w[1:] * mu / sd)))
    model = {"kind": "signals_ridge", "n": int(len(d)), "target": "ltp_gain_pct_vs_offer",
             "feature_names": names, "medians": {k: float(v) for k, v in medians.items()},
             "coef": coef, "intercept": intercept, "cv_mae": mae,
             "cv_direction_acc": dir_acc, "baseline_mae": base,
             "trained_at": datetime.now(timezone.utc).isoformat()}
    for s in sectors:
        model["medians"].setdefault(f"sec_{s}", 0.0)
    return model, {"n": int(len(d)), "cv_mae": round(mae, 1), "baseline_mae": round(base, 1),
                   "cv_direction_acc": round(dir_acc, 3)}


# ───────────────────────────── horizon GBMs ─────────────────────────────

def fit_horizon(m):
    from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
    from sklearn.model_selection import KFold

    # RHP buckets, not the dataset's Sector — see rhp_features()
    sectors = sorted(s for s in m["sector_rhp"].dropna().unique() if s != "other")
    cols = (["log_QIB", "log_bNII", "log_sNII", "log_NII", "log_Retail", "log_Total",
             "gmp_prem", "has_gmp", "listing_gain_day1", "issue_size_log", "ofs_share",
             "has_rhp", "nifty_chg"] + RHP_FEATS + FUNDAMENTALS + RESERVATION)
    X_all = m[cols].apply(pd.to_numeric, errors="coerce")
    medians = X_all.median(numeric_only=True).to_dict()
    X_all = X_all.fillna(pd.Series(medians)).fillna(0.0)
    for s in sectors:
        X_all[f"sec_{s}"] = (m["sector_rhp"] == s).astype(float)

    model = {"feature_names": list(X_all.columns), "medians": medians, "sectors": sectors,
             "trained_at": datetime.now(timezone.utc).isoformat(),
             "models": {}, "classifiers": {}, "cv": {}, "n": {}, "residual_base": {}}
    summary = {}
    for target in RET_TARGETS + AUX_TARGETS:
        mask = m[target].notna()
        if mask.sum() < MIN_ROWS:
            continue
        X = X_all[mask].reset_index(drop=True)
        y = m.loc[mask, target].astype(float).reset_index(drop=True)
        if target in RET_TARGETS:                   # learn the correction to day-1 carry
            base = X["listing_gain_day1"].values
            model["residual_base"][target] = "listing_gain_day1"
        else:
            base = np.zeros(len(y))
        preds = np.zeros(len(y))
        for tr, te in KFold(5, shuffle=True, random_state=SEED).split(X):
            g = GradientBoostingRegressor(n_estimators=300, max_depth=3, learning_rate=0.05,
                                          subsample=0.8, random_state=SEED)
            g.fit(X.iloc[tr], y.iloc[tr] - base[tr])
            preds[te] = g.predict(X.iloc[te]) + base[te]
        mae = float(np.mean(np.abs(preds - y)))
        cv = {"n": int(len(y)), "mae": round(mae, 1),
              "baseline_mean_mae": round(float(np.mean(np.abs(y.mean() - y))), 1)}
        if target in RET_TARGETS:
            cv["direction_acc"] = round(float(np.mean((preds > 0) == (y > 0))), 3)
            c = GradientBoostingClassifier(n_estimators=200, max_depth=3, learning_rate=0.05,
                                           subsample=0.8, random_state=SEED)
            c.fit(X, (y > 0).astype(int))
            model["classifiers"][target] = c
        final = GradientBoostingRegressor(n_estimators=300, max_depth=3, learning_rate=0.05,
                                          subsample=0.8, random_state=SEED)
        final.fit(X, y - base)
        model["models"][target] = final
        model["cv"][target] = cv
        model["n"][target] = int(len(y))
        summary[target] = cv
        print(f"  {target:20} n={len(y):4}  mae {mae:6.1f}  (mean-baseline {cv['baseline_mean_mae']})")
    return (model, summary) if model["models"] else (None, {})


# ───────────────────────────── promotion gate ─────────────────────────────

def incumbent_signals_mae():
    if not SIGNALS_PATH.exists():
        return None
    try:
        return float(json.loads(SIGNALS_PATH.read_text())["cv_mae"])
    except (ValueError, OSError, KeyError):
        return None


def incumbent_horizon_mae():
    if not HORIZON_PATH.exists():
        return None
    try:
        import joblib
        cv = joblib.load(HORIZON_PATH).get("cv") or {}
        maes = [v["mae"] for v in cv.values() if isinstance(v, dict) and "mae" in v]
        return float(np.mean(maes)) if maes else None
    except Exception:                                # noqa: BLE001
        return None


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
          f"with fundamentals: {int(m['ROE'].notna().sum())}")

    entry = {"at": datetime.now(timezone.utc).isoformat(), "rows": int(len(m))}

    print("\nsignals ridge (-> LTP gain vs offer)")
    sig, sig_cv = fit_signals(m)
    old = incumbent_signals_mae()
    entry["signals"] = {**sig_cv, "incumbent_mae": round(old, 1) if old else None}
    if sig is None:
        print("  skipped:", sig_cv)
        entry["signals"]["promoted"] = False
    else:
        print(f"  n={sig['n']}  cv_mae {sig['cv_mae']:.1f}  (mean-baseline {sig['baseline_mae']:.1f})  "
              f"dir {sig['cv_direction_acc']:.2f}   incumbent {old if old else 'none'}")
        better = force or old is None or sig["cv_mae"] < old
        entry["signals"]["promoted"] = bool(better)
        if better:
            SIGNALS_PATH.write_text(json.dumps(sig, indent=1), encoding="utf-8")
            print("  PROMOTED ->", SIGNALS_PATH.name)
        else:
            print(f"  kept incumbent (new {sig['cv_mae']:.1f} does not beat {old:.1f})")

    print("\nhorizon GBMs (-> 6m/12m/24m returns, entry, exit)")
    hor, hor_cv = fit_horizon(m)
    old_h = incumbent_horizon_mae()
    if hor is None:
        entry["horizon"] = {"promoted": False, "skipped": "too few labelled rows"}
        print("  skipped (too few labelled rows)")
    else:
        new_h = float(np.mean([v["mae"] for v in hor_cv.values()]))
        better = force or old_h is None or new_h < old_h
        entry["horizon"] = {"mean_mae": round(new_h, 1),
                            "incumbent_mean_mae": round(old_h, 1) if old_h else None,
                            "n": {k: v["n"] for k, v in hor_cv.items()},
                            "promoted": bool(better)}
        print(f"  mean mae {new_h:.1f}   incumbent {round(old_h, 1) if old_h else 'none'}")
        if better:
            import joblib
            joblib.dump(hor, HORIZON_PATH, compress=3)
            print("  PROMOTED ->", HORIZON_PATH.name)
        else:
            print(f"  kept incumbent (new {new_h:.1f} does not beat {old_h:.1f})")

    record(entry)
    print(f"\nhistory -> {HISTORY.relative_to(ROOT)}  ({entry['signals'].get('promoted')} / "
          f"{entry['horizon'].get('promoted')} promoted)")


if __name__ == "__main__":
    main()
