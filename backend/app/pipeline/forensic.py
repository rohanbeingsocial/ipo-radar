"""Stage 7: earnings-quality screen inspired by Beneish M-Score components and
the Piotroski F-Score checklist, restricted to what 3 years of restated
statements support. Output: red flags + strength checks, all explainable.

References: Beneish (1999) 'The Detection of Earnings Manipulation';
Piotroski (2000). We compute component-style ratios, not the full fitted
M-Score (an IPO's restated 3-year series doesn't match the model's inputs
one-for-one), and say so in the report.
"""
from __future__ import annotations

from .financial_extractor import get_metric


def _yoy(fin: dict, metric: str) -> tuple[float | None, float | None]:
    return get_metric(fin, metric, 0), get_metric(fin, metric, 1)


def run_forensics(fin: dict) -> dict:
    flags: list[dict] = []
    checks: list[dict] = []
    pages = fin.get("source_pages", {})

    rev_t, rev_p = _yoy(fin, "revenue")
    rec_t, rec_p = _yoy(fin, "receivables")
    pat_t, pat_p = _yoy(fin, "pat")
    cfo_t, cfo_p = _yoy(fin, "cfo")
    ta_t = get_metric(fin, "total_assets")
    nw_t, nw_p = _yoy(fin, "net_worth")
    debt_t, debt_p = _yoy(fin, "total_debt")
    ca_t, cl_t = get_metric(fin, "current_assets"), get_metric(fin, "current_liabilities")
    ca_p, cl_p = get_metric(fin, "current_assets", 1), get_metric(fin, "current_liabilities", 1)

    def flag(rule: str, severity: str, detail: str, value: float | None, page_key: str = "revenue"):
        flags.append({"rule": rule, "severity": severity, "detail": detail,
                      "value": round(value, 3) if value is not None else None,
                      "source_page": pages.get(page_key)})

    # --- DSRI: receivables growing much faster than sales (channel stuffing) ---
    if all(v not in (None, 0) for v in (rev_t, rev_p, rec_t, rec_p)):
        dsri = (rec_t / rev_t) / (rec_p / rev_p)
        if dsri > 1.4:
            flag("receivables_outpace_sales", "high",
                 f"Receivables/sales ratio grew {dsri:.2f}x year-over-year (Beneish DSRI-style signal; "
                 f">1.4 is a classic manipulation marker).", dsri, "receivables")
        checks.append({"check": "receivables_in_line_with_sales", "passed": dsri <= 1.4,
                       "value": round(dsri, 2)})

    # --- Accrual gap: profit far ahead of operating cash ---
    if pat_t is not None and cfo_t is not None and ta_t:
        accrual_ratio = (pat_t - cfo_t) / ta_t
        if accrual_ratio > 0.10:
            flag("high_accruals", "high",
                 f"(PAT − CFO)/Total assets = {accrual_ratio:.1%}; reported profit substantially "
                 f"exceeds operating cash (strongest single manipulation predictor in Beneish's work).",
                 accrual_ratio, "cfo")
        checks.append({"check": "cfo_exceeds_pat", "passed": cfo_t >= pat_t,
                       "value": round(cfo_t - pat_t, 1)})

    # --- Piotroski-style strength checks (adapted; equity-issue check omitted — it's an IPO) ---
    if pat_t is not None and ta_t:
        checks.append({"check": "positive_roa", "passed": pat_t > 0, "value": round(pat_t / ta_t, 3)})
    if cfo_t is not None:
        checks.append({"check": "positive_cfo", "passed": cfo_t > 0, "value": cfo_t})
    if pat_t is not None and pat_p is not None and nw_t and nw_p:
        checks.append({"check": "improving_roe", "passed": (pat_t / nw_t) > (pat_p / nw_p),
                       "value": round(pat_t / nw_t - pat_p / nw_p, 3)})
    if debt_t is not None and debt_p is not None and nw_t and nw_p:
        lev_t, lev_p = debt_t / nw_t, debt_p / nw_p
        checks.append({"check": "leverage_not_rising", "passed": lev_t <= lev_p + 0.05,
                       "value": round(lev_t - lev_p, 2)})
        if lev_t > lev_p + 0.5:
            flag("leverage_spike", "medium",
                 f"Debt/equity rose from {lev_p:.2f} to {lev_t:.2f} in the latest year.", lev_t - lev_p, "total_debt")
    if all(v not in (None, 0) for v in (ca_t, cl_t, ca_p, cl_p)):
        checks.append({"check": "improving_liquidity", "passed": (ca_t / cl_t) >= (ca_p / cl_p),
                       "value": round(ca_t / cl_t - ca_p / cl_p, 2)})
    if rev_t and rev_p:
        sgi = rev_t / rev_p
        if sgi > 1.6:
            flag("extreme_sales_growth", "medium",
                 f"Sales grew {sgi:.2f}x in the latest year; Beneish notes extreme growth firms face "
                 f"pressure that predicts manipulation. Verify sustainability.", sgi)

    # --- Pre-IPO profit spurt: PAT growth far outpacing revenue growth ---
    if all(v not in (None, 0) for v in (pat_t, pat_p, rev_t, rev_p)) and pat_p > 0:
        pat_g, rev_g = pat_t / pat_p - 1, rev_t / rev_p - 1
        if pat_g > rev_g + 0.5 and pat_g > 0.5:
            flag("pre_ipo_profit_spurt", "medium",
                 f"PAT grew {pat_g:.0%} vs revenue {rev_g:.0%} in the latest pre-IPO year — margin "
                 f"expansion this sharp just before listing warrants scrutiny (dressing-up risk).",
                 pat_g - rev_g)

    passed = sum(1 for c in checks if c["passed"])
    return {"flags": flags, "checks": checks,
            "strength_score": {"passed": passed, "total": len(checks)},
            "cap_triggered": any(f["severity"] == "high" for f in flags)}
