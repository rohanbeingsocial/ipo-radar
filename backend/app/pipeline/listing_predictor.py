"""Listing-day and hype-cycle forecast from RHP-only features.

Predicts, before listing and without any market data (no GMP, no subscription
numbers — those don't exist inside an RHP):
  1. listing-open premium vs offer price (point + range),
  2. whether/when the stock falls below the offer price after listing hype,
  3. the likely bottom window ("when does it stop falling") — the user-facing
     "optimal entry" heuristic, anchored on SEBI lock-in expiries,
  4. when it recovers above the offer and listing prices.

Three engines:
  rules — transparent bands from the score/valuation/risk/OFS profile plus
          the market-structure calendar (anchor lock-ins end at 30 and 90
          days ≈ sessions 20 and 60; pre-IPO/promoter lock-in at 6 months
          ≈ session 125).
  llm   — same features, anonymized (no names), sent to the AI layer.
  ml    — optional ridge model (tools/train_listing_model.py) if trained
          coefficients exist.

All outputs are research heuristics with wide error bars, not trade advice.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from . import llm_layer

MODEL_PATH = Path(__file__).resolve().parent.parent / "listing_model.json"
SIGNALS_MODEL_PATH = Path(__file__).resolve().parent.parent / "listing_model_signals.json"
HORIZON_MODEL_PATH = Path(__file__).resolve().parent.parent / "horizon_model.pkl"
_horizon_cache: dict = {}

# app sector bucket -> training-sheet sector dummy
_SHEET_SECTOR = {"insurance": "sec_Financials", "asset_management": "sec_Financials",
                 "fin_services": "sec_Financials", "consumer": "sec_Consumer Discretionary",
                 "pharma_health": "sec_Healthcare", "energy_infra": "sec_Energy",
                 "industrial": "sec_Industrials", "workspace_realty": "sec_Real Estate",
                 "tech_platform": "sec_Technology"}

SECTOR_BUCKETS = [
    ("insurance", r"insurance|policyholder"),
    ("asset_management", r"asset management|mutual fund|aum"),
    ("fin_services", r"broking|lending|nbfc|fintech|financial products|investment platform"),
    ("consumer", r"consumer|retail|food|beverage|fmcg|apparel|eyewear|appliances|electronics"),
    ("pharma_health", r"pharma|drug|formulation|clinical|hospital|diagnostic"),
    ("energy_infra", r"solar|renewable|power|energy|epc|infrastructure|construction"),
    ("industrial", r"manufactur|engineering|granite|mining|chemical|textile|packaging"),
    ("workspace_realty", r"workspace|real estate|leasing|coworking"),
    ("tech_platform", r"platform|technology|software|digital|e-commerce|online"),
]


def features_from_report(report: dict) -> dict:
    """RHP-only, identity-free feature vector."""
    sc = report.get("scoring") or {}
    cats = sc.get("categories") or {}
    snap = report.get("snapshot") or {}
    risk = report.get("risk") or {}
    val = report.get("valuation") or {}
    forensic = report.get("forensic") or {}

    fresh = snap.get("fresh_issue_cr") or 0
    ofs = snap.get("ofs_cr") or 0
    total = snap.get("total_issue_cr") or (fresh + ofs) or None
    ofs_share = round(ofs / (fresh + ofs), 2) if (fresh + ofs) > 0 else None

    excerpt = ((report.get("industry") or {}).get("excerpt") or "").lower()
    sector = next((name for name, pat in SECTOR_BUCKETS if re.search(pat, excerpt)), "other")

    def cat_score(k):
        v = (cats.get(k) or {}).get("score")
        return round(v, 1) if isinstance(v, (int, float)) else None

    return {
        "overall_score": sc.get("overall"),
        "growth_score": cat_score("growth"),
        "cash_generation_score": cat_score("cash_generation"),
        "financial_health_score": cat_score("financial_health"),
        "risk_score": risk.get("score"),
        "forensic_flag_count": len(forensic.get("flags") or []),
        "valuation_relative_pe": val.get("relative"),  # issuer PE / peer median, often None
        "valuation_call": val.get("call"),
        "ofs_share": ofs_share,
        "issue_size_cr_log": round(math.log10(total), 2) if total else None,
        "sector": sector,
        "loss_making": (cat_score("growth") or 50) < 40 and (cat_score("cash_generation") or 50) < 40,
        "listing_window": "H2-2025/H1-2026 Indian primary market",
    }


def rule_forecast(f: dict) -> dict:
    """Transparent heuristic bands. Session ≈ trading day (~21/month)."""
    score = f.get("overall_score") or 50
    rel = f.get("valuation_relative_pe")
    overvalued = (f.get("valuation_call") in ("overvalued", "fairly_valued_expensive")) or (rel or 0) > 1.3
    big_ofs = (f.get("ofs_share") or 0) >= 0.7
    mega = (f.get("issue_size_cr_log") or 0) >= 3.7  # ≥ ~₹5,000 cr

    # 1 — listing premium: hype pays for quality, discounts stretched pricing
    prem = 6.0
    prem += (score - 60) * 0.45          # fundamentals signal
    prem -= 8.0 if overvalued else 0.0
    prem -= 4.0 if big_ofs else 0.0      # pure-exit offers get less hype
    prem -= 3.0 if mega else 0.0         # large float absorbs demand
    prem = max(-8.0, min(35.0, prem))

    # 2 — hype decay: does it break the offer price, and when
    if score >= 72 and not overvalued:
        below = {"probability": 0.25, "window_sessions": [1, 10],
                 "expected_depth_pct": -5,
                 "note": "strong docs usually only lose the offer price briefly, if at all"}
        bottom = {"window_sessions": [0, 12], "depth_vs_offer_pct": [-8, 5],
                  "note": "any bottom forms in the first fortnight; weakness is shallow"}
        recover = {"above_offer_sessions": [1, 20], "above_listing_sessions": [5, 60]}
    elif score >= 60 and not overvalued:
        below = {"probability": 0.6, "window_sessions": [5, 45],
                 "expected_depth_pct": -15,
                 "note": "fade typically starts once anchor lock-in ends (~session 20)"}
        bottom = {"window_sessions": [40, 90], "depth_vs_offer_pct": [-25, -5],
                  "note": "second anchor unlock (~session 60) is the usual capitulation zone"}
        recover = {"above_offer_sessions": [70, 160], "above_listing_sessions": [90, 250]}
    else:
        below = {"probability": 0.85, "window_sessions": [0, 25],
                 "expected_depth_pct": -30,
                 "note": "weak/overpriced docs lose the offer price quickly once hype allocation churns"}
        bottom = {"window_sessions": [60, 130], "depth_vs_offer_pct": [-55, -15],
                  "note": "slide usually runs into the 6-month pre-IPO lock-in expiry (~session 125)"}
        recover = {"above_offer_sessions": [150, 400], "above_listing_sessions": None,
                   "note": "may not reclaim the listing price within the first year"}

    return {"engine": "rules",
            "listing_open_premium_pct": {"point": round(prem, 1),
                                         "range": [round(prem - 8, 1), round(prem + 10, 1)]},
            "falls_below_offer": below, "bottom": bottom, "recovery": recover}


LLM_SYSTEM = (
    "You are a quantitative analyst of Indian IPO market microstructure. You are given an "
    "ANONYMIZED feature vector extracted from a red herring prospectus — you do not know "
    "which company it is, and you must not try to guess or use knowledge of specific IPOs. "
    "Reason only from base rates you know about Indian primary markets (listing-pop hype, "
    "anchor lock-in expiries at 30/90 days, pre-IPO lock-in at 6 months, OFS overhang, "
    "segment effects) and from the features given. Output STRICT JSON only, no prose, "
    "matching exactly this schema: "
    '{"listing_open_premium_pct": {"point": float, "range": [lo, hi]}, '
    '"falls_below_offer": {"probability": 0..1, "window_sessions": [lo, hi], "expected_depth_pct": float}, '
    '"bottom": {"window_sessions": [lo, hi], "depth_vs_offer_pct": [lo, hi]}, '
    '"recovery": {"above_offer_sessions": [lo, hi] or null, "above_listing_sessions": [lo, hi] or null}, '
    '"reasoning": "one short sentence"}. Sessions are trading days after listing (~21/month).'
)


def llm_forecast(f: dict) -> dict | None:
    if not llm_layer.llm_available():
        return None
    text = llm_layer._ask(LLM_SYSTEM, json.dumps(f, ensure_ascii=False), max_tokens=800)
    if not text:
        return None
    try:
        m = re.search(r"\{.*\}", text, re.S)
        out = json.loads(m.group(0) if m else text)
        out["engine"] = "llm"
        return out
    except (json.JSONDecodeError, AttributeError):
        return None


def ml_forecast(f: dict) -> dict | None:
    """Linear model trained by tools/train_listing_model.py (if present)."""
    if not MODEL_PATH.exists():
        return None
    model = json.loads(MODEL_PATH.read_text())
    x = _ml_vector(f, model["feature_names"])
    out = {"engine": "ml", "trained_on_n": model.get("n"), "loo_mae": model.get("loo_mae")}
    for target, coefs in model["targets"].items():
        out[target] = round(sum(c * v for c, v in zip(coefs, x)), 1)
    return out


def _ml_vector(f: dict, names: list[str]) -> list[float]:
    base = {
        "bias": 1.0,
        "overall_score": (f.get("overall_score") or 50) / 100,
        "risk_score": (f.get("risk_score") or 50) / 100,
        "ofs_share": f.get("ofs_share") if f.get("ofs_share") is not None else 0.5,
        "issue_size_cr_log": (f.get("issue_size_cr_log") or 3.0) / 4,
        "overvalued": 1.0 if f.get("valuation_call") in ("overvalued", "fairly_valued_expensive") else 0.0,
        "forensic_flag_count": min(f.get("forensic_flag_count") or 0, 4) / 4,
    }
    return [base.get(n, 0.0) for n in names]


def signals_forecast(report: dict, signals: dict | None = None) -> dict | None:
    """LTP-gain forecast from the ridge trained on the user's 125-IPO sheet
    (GMP + subscription multiples + fundamentals + sector). Missing inputs
    fall back to training medians, so it degrades gracefully — but it is only
    worth reading once real market signals have been posted."""
    if not SIGNALS_MODEL_PATH.exists():
        return None
    model = json.loads(SIGNALS_MODEL_PATH.read_text())
    x = dict(model["medians"])
    used = []
    s = signals or report.get("market_signals") or {}
    snap = report.get("snapshot") or {}
    band = snap.get("price_band") or [None, None]
    if s.get("gmp") and band[1]:
        x["gmp_prem"] = max(-30.0, min(150.0, s["gmp"] / band[1] * 100))
        used.append("gmp")
    for src, feat in (("sub_qib", "log_QIB"), ("sub_nii", "log_bNII"), ("sub_rii", "log_Retail")):
        if s.get(src) is not None:
            x[feat] = math.log1p(max(s[src], 0))
            used.append(src)
    ratios = (report.get("financials") or {}).get("ratios") or {}
    if ratios.get("roe") is not None:
        x["ROE"] = ratios["roe"] * 100
        used.append("roe")
    if ratios.get("net_margin") is not None:
        x["PAT Margin"] = ratios["net_margin"] * 100
        used.append("net_margin")
    pe = (report.get("valuation") or {}).get("issuer_pe")
    if pe:
        x["Post IPO P/E"] = pe
        used.append("issuer_pe")
    sector = features_from_report(report).get("sector")
    for k in x:
        if k.startswith("sec_"):
            x[k] = 0.0
    if _SHEET_SECTOR.get(sector) in x:
        x[_SHEET_SECTOR[sector]] = 1.0
    pred = model["intercept"] + sum(model["coef"][k] * v for k, v in x.items())
    return {"engine": "ml_signals", "forecast_ltp_gain_pct_vs_offer": round(pred, 1),
            "trained_on_n": model["n"], "cv_mae_pp": round(model["cv_mae"], 1),
            "cv_direction_acc": round(model["cv_direction_acc"], 2),
            "inputs_used": used or ["training medians only — post market-signals to sharpen"],
            "note": "Direction (above/below offer) is the reliable read; the point "
                    "estimate carries the CV MAE shown."}


def _load_horizon_model():
    if "m" not in _horizon_cache:
        if not HORIZON_MODEL_PATH.exists():
            _horizon_cache["m"] = None
        else:
            import joblib
            _horizon_cache["m"] = joblib.load(HORIZON_MODEL_PATH)
    return _horizon_cache["m"]


def horizon_forecast(report: dict, signals: dict | None = None) -> dict | None:
    """6m/12m/24m return + entry/exit forecast from the GBMs trained on the
    20-year expanded dataset (tools/train_horizon_model.py). Uses subscription
    multiples, GMP, listing-day gain (post the market-signals for these) plus
    this report's RHP features; anything missing falls back to training
    medians and is excluded from inputs_used."""
    model = _load_horizon_model()
    if not model:
        return None
    s = signals or report.get("market_signals") or {}
    f = features_from_report(report)
    sc = report.get("scoring") or {}
    cats = sc.get("categories") or {}

    def cat(k):
        v = (cats.get(k) or {}).get("score")
        return v if isinstance(v, (int, float)) else None

    x = dict(model["medians"])
    used = []
    for src, feat in (("sub_qib", "log_QIB"), ("sub_bnii", "log_bNII"),
                      ("sub_snii", "log_sNII"), ("sub_nii", "log_NII"),
                      ("sub_rii", "log_Retail")):
        if s.get(src) is not None:
            x[feat] = math.log1p(max(s[src], 0))
            used.append(src)
    if s.get("sub_nii") is None and s.get("sub_bnii") is not None and s.get("sub_snii") is not None:
        x["log_NII"] = math.log1p(max((s["sub_bnii"] + s["sub_snii"]) / 2, 0))
    band = (report.get("snapshot") or {}).get("price_band") or [None, None]
    if s.get("gmp") is not None and band[1]:
        x["gmp_prem"] = max(-30.0, min(150.0, s["gmp"] / band[1] * 100))
        x["has_gmp"] = 1.0
        used.append("gmp")
    else:
        x["has_gmp"] = 0.0
    if s.get("day1_gain") is not None:
        x["listing_gain_day1"] = max(-80.0, min(400.0, s["day1_gain"]))
        used.append("day1_gain")
    snap = report.get("snapshot") or {}
    total = snap.get("total_issue_cr") or ((snap.get("fresh_issue_cr") or 0) +
                                           (snap.get("ofs_cr") or 0)) or None
    if total:
        x["issue_size_log"] = math.log10(max(total, 1))
        used.append("issue_size")
    if f.get("ofs_share") is not None:
        x["ofs_share"] = f["ofs_share"]
    rhp_map = {"rhp_overall": sc.get("overall"), "rhp_growth": cat("growth"),
               "rhp_cash": cat("cash_generation"), "rhp_finhealth": cat("financial_health"),
               "rhp_governance": cat("governance"), "rhp_promoter": cat("promoter_quality"),
               "rhp_risk": (report.get("risk") or {}).get("score"),
               "rhp_forensic_flags": len((report.get("forensic") or {}).get("flags") or [])}
    x["has_rhp"] = 1.0
    for k, v in rhp_map.items():
        if v is not None:
            x[k] = float(v)
    used.append("rhp_features")
    for sec in model.get("sectors", []):
        x[f"sec_{sec}"] = 1.0 if f.get("sector") == sec else 0.0

    import pandas as pd
    vec = pd.DataFrame([[x.get(name, 0.0) for name in model["feature_names"]]],
                       columns=model["feature_names"])
    out = {"engine": "ml_horizons", "inputs_used": used, "horizons": {}, "cv": model["cv"]}
    preds = {}
    for target, mod in model["models"].items():
        p = float(mod.predict(vec)[0])
        rb = (model.get("residual_base") or {}).get(target)
        if rb:                       # return models predict the correction to day-1 carry
            p += float(x.get(rb, 0.0))
        preds[target] = p
    for target, clf in model.get("classifiers", {}).items():
        preds[target + "_p_pos"] = float(clf.predict_proba(vec)[0][1])
    for h, label in (("ret_6m", "6m"), ("ret_12m", "12m"), ("ret_24m", "24m")):
        if h in preds:
            out["horizons"][label] = {
                "ret_pct_vs_offer": round(preds[h], 1),
                "p_above_offer": round(preds.get(h + "_p_pos", 0.5), 2),
                "cv_mae_pp": model["cv"].get(h, {}).get("mae")}
    entry = {}
    if "sessions_to_bottom" in preds:
        sess = max(0, int(round(preds["sessions_to_bottom"])))
        entry = {"expected_bottom_session": sess,
                 "expected_bottom_depth_pct_vs_offer": round(preds.get("bottom_12m_pct", 0), 1),
                 "read": ("dips are bought fast; waiting rarely improves entry" if sess <= 10
                          else f"patience pays: model expects the low around session {sess} "
                               f"(~{round(sess / 21, 1)} months in)")}
    exit_ = {}
    ret_opts = {k: v for k, v in (("6m", preds.get("ret_6m")), ("12m", preds.get("ret_12m")),
                                  ("24m", preds.get("ret_24m"))) if v is not None}
    if ret_opts:
        best = max(ret_opts, key=ret_opts.get)
        if all(v <= 0 for v in ret_opts.values()):
            exit_ = {"call": "avoid", "note": "no profitable horizon predicted within 2 years"}
        else:
            exit_ = {"call": f"hold to ~{best}", "expected_ret_pct": round(ret_opts[best], 1)}
        if "peak_24m_pct" in preds and "sessions_to_peak" in preds:
            exit_["expected_peak_pct_vs_offer"] = round(preds["peak_24m_pct"], 1)
            exit_["expected_peak_session"] = max(0, int(round(preds["sessions_to_peak"])))
    out["entry"] = entry
    out["exit"] = exit_
    out["note"] = ("Trained on ~20 years of NSE/BSE mainboard IPOs. Point estimates carry the "
                   "CV MAE shown; direction and entry-timing bands are the reliable read. "
                   "Long-horizon training data skews to survivors.")
    return out


def forecast(report: dict, use_llm: bool = False, signals: dict | None = None) -> dict:
    f = features_from_report(report)
    out = {"features": f, "rules": rule_forecast(f),
           "disclaimer": "Heuristic research output with wide error bars; not investment advice."}
    ml = ml_forecast(f)
    if ml:
        out["ml"] = ml
    sig = signals_forecast(report, signals)
    if sig:
        out["ml_signals"] = sig
    hor = horizon_forecast(report, signals)
    if hor:
        out["ml_horizons"] = hor
    if use_llm:
        out["llm"] = llm_forecast(f)
    return out
