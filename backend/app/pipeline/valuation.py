"""Stage 6: ratios + peer-relative valuation call.

The issuer P/E comes from the Basis-for-Offer-Price chapter (printed at the
price band); peers come from the mandated listed-peer table in the same
chapter. The valuation call is banded vs the peer median and reported
separately from quality scores — a good business can still be overpriced.
"""
from __future__ import annotations

from statistics import median

from .financial_extractor import cagr, get_metric


def _safe_div(a: float | None, b: float | None) -> float | None:
    if a is None or b in (None, 0):
        return None
    return a / b


def compute_ratios(fin: dict) -> dict:
    """All classic ratios, latest fiscal year, with the raw inputs kept for evidence."""
    r: dict = {}
    rev, pat, ebitda = get_metric(fin, "revenue"), get_metric(fin, "pat"), get_metric(fin, "ebitda")
    nw, debt = get_metric(fin, "net_worth"), get_metric(fin, "total_debt")
    ca, cl = get_metric(fin, "current_assets"), get_metric(fin, "current_liabilities")
    cfo, pbt, fc = get_metric(fin, "cfo"), get_metric(fin, "pbt"), get_metric(fin, "finance_costs")
    ta = get_metric(fin, "total_assets")

    r["operating_margin"] = _safe_div(ebitda, rev)
    r["net_margin"] = _safe_div(pat, rev)
    r["roe"] = _safe_div(pat, nw)
    ebit = (pbt + fc) if (pbt is not None and fc is not None) else None
    r["roce"] = _safe_div(ebit, (nw + debt) if (nw is not None and debt is not None) else None)
    r["debt_equity"] = _safe_div(debt, nw)
    r["current_ratio"] = _safe_div(ca, cl)
    r["asset_turnover"] = _safe_div(rev, ta)
    r["cfo_to_pat"] = _safe_div(cfo, pat) if (pat or 0) > 0 else None
    r["cfo_to_ebitda"] = _safe_div(cfo, ebitda) if (ebitda or 0) > 0 else None
    r["interest_cover"] = _safe_div(ebit, fc) if (fc or 0) > 0 else None
    r["revenue_cagr"] = cagr(fin, "revenue")
    r["pat_cagr"] = cagr(fin, "pat")

    order = fin.get("fiscal_order") or []
    r["margin_series"] = [
        {"fy": fy,
         "net_margin": _safe_div(fin["series"][fy].get("pat"), fin["series"][fy].get("revenue")),
         "operating_margin": _safe_div(fin["series"][fy].get("ebitda"), fin["series"][fy].get("revenue"))}
        for fy in order
    ]
    return {k: v for k, v in r.items() if v is not None or k in ("margin_series",)}


def valuation_call(issuer_pe: float | None, peers: list[dict], ratios: dict,
                   price_high: float | None = None, issuer_eps: float | None = None) -> dict:
    """Bands vs peer-median P/E: <0.7x undervalued · 0.7–1.1 fair ·
    1.1–1.5 expensive-side · >1.5 overvalued."""
    listed = [p for p in peers if not p.get("is_issuer")]
    peer_pes = [p["pe"] for p in listed if isinstance(p.get("pe"), (int, float)) and 0 < p["pe"] < 400]
    out: dict = {"issuer_pe": issuer_pe, "peer_pe_median": None, "relative": None,
                 "call": "indeterminate", "reasoning": []}

    if issuer_pe is None and price_high:
        # Most documents do not print the issue P/E as one figure — a DRHP literally
        # prints "[●]" because the price is not set yet — so derive it. Prefer the
        # weighted-average EPS table the document is required to carry; fall back to the
        # issuer's own row in the peer table, which is often simply absent.
        eps = issuer_eps if (isinstance(issuer_eps, (int, float)) and issuer_eps > 0) else None
        src = "the weighted-average EPS table"
        if eps is None:
            eps = next((p["eps"] for p in peers if p.get("is_issuer")
                        and isinstance(p.get("eps"), (int, float)) and p["eps"] > 0), None)
            src = "the peer-comparison table"
        if eps:
            issuer_pe = round(price_high / eps, 2)
            out["issuer_pe"] = issuer_pe
            out["issuer_pe_derived"] = True
            out["reasoning"].append(
                f"Issue P/E derived as offer price ₹{price_high:g} ÷ issuer EPS ₹{eps:g} "
                f"from {src} (the document does not print it as a single figure).")

    if not peer_pes:
        out["reasoning"].append("No usable listed-peer P/E table could be extracted; peer-relative valuation is indeterminate.")
    if issuer_pe is None:
        out["reasoning"].append("Issuer P/E at the price band could not be extracted from the Basis for Offer Price chapter.")

    if peer_pes:
        out["peer_pe_median"] = round(median(peer_pes), 1)

    if peer_pes and issuer_pe:
        med = median(peer_pes)
        rel = issuer_pe / med
        out.update({"peer_pe_median": round(med, 1), "relative": round(rel, 2)})
        if rel < 0.7:
            out["call"] = "undervalued"
        elif rel <= 1.1:
            out["call"] = "fairly_valued"
        elif rel <= 1.5:
            out["call"] = "fairly_valued_expensive"
        else:
            out["call"] = "overvalued"
        out["reasoning"].append(
            f"Issue P/E {issuer_pe:.1f}x vs listed-peer median {med:.1f}x → {rel:.2f}x relative. "
            f"Bands: <0.7x undervalued, 0.7–1.1x fair, 1.1–1.5x expensive side of fair, >1.5x overvalued.")
        growth = ratios.get("pat_cagr")
        if growth is not None and rel > 1.1 and growth > 0.30:
            out["reasoning"].append(
                f"Premium is partly supported by {growth * 100:.0f}% profit CAGR (growth-adjusted view).")
        note = ("Peer set is issuer-chosen (disclosed in the RHP) and may be flattering; "
                "treat the relative call as a starting point, not a fair-value estimate.")
        out["reasoning"].append(note)
    return out
