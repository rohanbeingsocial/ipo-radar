"""Train the 6m/12m/24m horizon + entry/exit models on the 20-year expanded
IPO dataset (built by the scratchpad pipeline from Chittorgarh + Yahoo), joined
with this app's RHP-derived features.

Inputs available "in the first 2-3 days of listing": subscription multiples,
GMP (recent IPOs only), listing-day close gain, issue structure, RHP scores.

Targets: ret_6m / ret_12m / ret_24m (vs offer), bottom_12m_pct +
sessions_to_bottom (optimal entry), peak_24m_pct + sessions_to_peak (exit).

Usage:  python tools/train_horizon_model.py [expanded.xlsx] [manifest.json]
Writes: app/horizon_model.pkl  (+ prints CV table vs baselines)
"""
import json
import math
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
from sklearn.model_selection import KFold

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
from app.pipeline.listing_predictor import features_from_report  # noqa: E402

API = "http://localhost:8001/api"
SEED = 42
XLSX = Path(sys.argv[1]) if len(sys.argv) > 1 else \
    BACKEND.parent / "ipodata" / "finalipodata_expanded_20yr.xlsx"
MANIFEST = Path(sys.argv[2]) if len(sys.argv) > 2 else None

RET_TARGETS = ["ret_6m", "ret_12m", "ret_24m"]
AUX_TARGETS = ["bottom_12m_pct", "sessions_to_bottom", "peak_24m_pct", "sessions_to_peak"]
RHP_FEATS = ["rhp_overall", "rhp_growth", "rhp_cash", "rhp_finhealth", "rhp_governance",
             "rhp_promoter", "rhp_risk", "rhp_forensic_flags"]
SECTORS = ["insurance", "asset_management", "fin_services", "consumer", "pharma_health",
           "energy_infra", "industrial", "workspace_realty", "tech_platform", "other"]


def http_json(url):
    with urllib.request.urlopen(url, timeout=120) as r:
        return json.load(r)


def norm(name):
    s = re.sub(r"[^a-z0-9 ]", " ", str(name).lower())
    drop = {"limited", "ltd", "private", "pvt", "the", "and", "of", "india"}
    return " ".join(w for w in s.split() if w and w not in drop)


def rhp_features_by_key():
    """normalized company -> RHP feature dict, via live reports."""
    out = {}
    for a in http_json(API + "/analyses"):
        if a["status"] != "completed" or not a.get("company_name") or a.get("is_demo"):
            continue
        key = norm(a["company_name"])
        if key in out:
            continue
        try:
            rep = http_json(f"{API}/analyses/{a['id']}/report")
        except Exception:
            continue
        f = features_from_report(rep)
        sc = rep.get("scoring") or {}
        cats = sc.get("categories") or {}

        def cat(k):
            v = (cats.get(k) or {}).get("score")
            return v if isinstance(v, (int, float)) else np.nan
        out[key] = {
            "rhp_overall": sc.get("overall"), "rhp_growth": cat("growth"),
            "rhp_cash": cat("cash_generation"), "rhp_finhealth": cat("financial_health"),
            "rhp_governance": cat("governance"), "rhp_promoter": cat("promoter_quality"),
            "rhp_risk": (rep.get("risk") or {}).get("score"),
            "rhp_forensic_flags": len((rep.get("forensic") or {}).get("flags") or []),
            "sector": f.get("sector") or "other",
        }
    return out


def build_frame():
    df = pd.read_excel(XLSX, sheet_name="Expanded")
    df["key"] = df["Name"].map(norm)
    rhp = rhp_features_by_key()
    # manifest gives exact cg-company -> analysis joins for the SEBI batch
    manifest_names = {}
    if MANIFEST and MANIFEST.exists():
        for k, v in json.loads(MANIFEST.read_text()).items():
            if v.get("status") == "uploaded":
                manifest_names[norm(v["company"])] = v["company"]

    feats = pd.DataFrame([{"key": k, **v} for k, v in rhp.items()])
    m = df.merge(feats, on="key", how="left")
    m["has_rhp"] = m["rhp_overall"].notna().astype(float)
    m["sector"] = m["sector"].fillna("other")
    print(f"rows: {len(m)}   with RHP features: {int(m['has_rhp'].sum())}")

    m["log_QIB"] = np.log1p(pd.to_numeric(m["QIB"], errors="coerce").clip(lower=0))
    m["log_bNII"] = np.log1p(pd.to_numeric(m["bNII"], errors="coerce").clip(lower=0))
    m["log_sNII"] = np.log1p(pd.to_numeric(m["sNII"], errors="coerce").clip(lower=0))
    m["log_NII"] = np.log1p(pd.to_numeric(m["NII"], errors="coerce").clip(lower=0))
    m["log_Retail"] = np.log1p(pd.to_numeric(m["Retail"], errors="coerce").clip(lower=0))
    m["log_Total"] = np.log1p(pd.to_numeric(m["Total Sub"], errors="coerce").clip(lower=0))
    gmp = pd.to_numeric(m.get("GMP"), errors="coerce")
    offer = pd.to_numeric(m["Offer Price"], errors="coerce")
    m["gmp_prem"] = (gmp / offer * 100).clip(-30, 150)
    m["has_gmp"] = m["gmp_prem"].notna().astype(float)
    m["listing_gain_day1"] = pd.to_numeric(m["Listing Gain"], errors="coerce").clip(-80, 400)
    m["issue_size_log"] = np.log10(pd.to_numeric(m["Issue Size (cr)"], errors="coerce").clip(lower=1))
    fresh = pd.to_numeric(m["Fresh (cr)"], errors="coerce").fillna(0)
    ofs = pd.to_numeric(m["OFS (cr)"], errors="coerce").fillna(0)
    m["ofs_share"] = (ofs / (fresh + ofs).replace(0, np.nan)).clip(0, 1)
    m["list_year"] = pd.to_datetime(m["List Date"], format="%d-%b-%Y", errors="coerce").dt.year
    for c in ["Ret 6m %", "Ret 12m %", "Ret 24m %", "Bottom 12m %", "Sessions to Bottom",
              "Peak 24m %", "Sessions to Peak"]:
        m[c] = pd.to_numeric(m[c], errors="coerce")
    m = m.rename(columns={"Ret 6m %": "ret_6m", "Ret 12m %": "ret_12m", "Ret 24m %": "ret_24m",
                          "Bottom 12m %": "bottom_12m_pct", "Sessions to Bottom": "sessions_to_bottom",
                          "Peak 24m %": "peak_24m_pct", "Sessions to Peak": "sessions_to_peak"})
    # extreme outliers dominate MAE and teach nothing; clip targets, not features
    for t in RET_TARGETS + ["bottom_12m_pct", "peak_24m_pct"]:
        m[t] = m[t].clip(-95, 400)
    return m


def feature_matrix(m):
    cols = ["log_QIB", "log_bNII", "log_sNII", "log_NII", "log_Retail", "log_Total",
            "gmp_prem", "has_gmp", "listing_gain_day1", "issue_size_log", "ofs_share",
            "has_rhp"] + RHP_FEATS
    X = m[cols].apply(pd.to_numeric, errors="coerce")
    medians = X.median(numeric_only=True).to_dict()
    X = X.fillna(pd.Series(medians)).fillna(0.0)
    for sec in SECTORS:
        X[f"sec_{sec}"] = (m["sector"] == sec).astype(float)
    return X, medians


def gbm():
    return GradientBoostingRegressor(n_estimators=300, max_depth=3, learning_rate=0.05,
                                     subsample=0.8, random_state=SEED)


def gbc():
    return GradientBoostingClassifier(n_estimators=200, max_depth=3, learning_rate=0.05,
                                      subsample=0.8, random_state=SEED)


def main():
    m = build_frame()
    X_all, medians = feature_matrix(m)
    model = {"feature_names": list(X_all.columns), "medians": medians,
             "sectors": SECTORS, "trained_at": datetime.utcnow().isoformat(),
             "models": {}, "classifiers": {}, "cv": {}, "n": {}}

    model["residual_base"] = {}
    for target in RET_TARGETS + AUX_TARGETS:
        mask = m[target].notna()
        if mask.sum() < 60:
            print(f"{target:20} skipped (n={mask.sum()})")
            continue
        X, y = X_all[mask].reset_index(drop=True), m.loc[mask, target].astype(float).reset_index(drop=True)
        # return targets: model the correction to day-1 carry, not the level —
        # trees can't extrapolate the identity map that carry-forward embodies
        if target in RET_TARGETS:
            base = X["listing_gain_day1"].values
            model["residual_base"][target] = "listing_gain_day1"
        else:
            base = np.zeros(len(y))
        preds = np.zeros(len(y))
        for tr, te in KFold(5, shuffle=True, random_state=SEED).split(X):
            mod = gbm()
            mod.fit(X.iloc[tr], y.iloc[tr] - base[tr])
            preds[te] = mod.predict(X.iloc[te]) + base[te]
        mae = float(np.mean(np.abs(preds - y)))
        cv = {"n": int(len(y)), "mae": round(mae, 1),
              "baseline_mean_mae": round(float(np.mean(np.abs(y.mean() - y))), 1)}
        if target in RET_TARGETS:
            cv["direction_acc"] = round(float(np.mean((preds > 0) == (y > 0))), 3)
            carry = m.loc[mask, "listing_gain_day1"].fillna(0).values
            cv["carry_day1_mae"] = round(float(np.mean(np.abs(carry - y))), 1)
            cv["carry_day1_direction"] = round(float(np.mean((carry > 0) == (y > 0))), 3)
            clf_preds = np.zeros(len(y))
            for tr, te in KFold(5, shuffle=True, random_state=SEED).split(X):
                c = gbc()
                c.fit(X.iloc[tr], (y.iloc[tr] > 0).astype(int))
                clf_preds[te] = c.predict_proba(X.iloc[te])[:, 1]
            cv["clf_acc"] = round(float(np.mean((clf_preds > 0.5) == (y > 0))), 3)
            final_clf = gbc()
            final_clf.fit(X, (y > 0).astype(int))
            model["classifiers"][target] = final_clf
        final = gbm()
        final.fit(X, y - base)
        model["models"][target] = final
        model["cv"][target] = cv
        model["n"][target] = int(len(y))
        extra = (f"  dir {cv.get('direction_acc', ''):>5}  clf {cv.get('clf_acc', ''):>5}  "
                 f"carry-mae {cv.get('carry_day1_mae', '-'):>6}  carry-dir {cv.get('carry_day1_direction', '')}"
                 if target in RET_TARGETS else "")
        print(f"{target:20} n={len(y):4}  mae {mae:6.1f}  (mean-baseline {cv['baseline_mean_mae']}){extra}")

    imp = sorted(zip(model["feature_names"], model["models"]["ret_12m"].feature_importances_),
                 key=lambda x: -x[1])[:12]
    print("\ntop features (ret_12m):")
    for f, v in imp:
        print(f"  {f:22} {v:.3f}")

    out = BACKEND / "app" / "horizon_model.pkl"
    joblib.dump(model, out, compress=3)
    print(f"\nsaved -> {out}")


if __name__ == "__main__":
    main()
