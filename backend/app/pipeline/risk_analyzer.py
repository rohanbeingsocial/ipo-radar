"""Stage 5: risk-factor taxonomy, quantified severities, boilerplate detection.

13-class taxonomy. Where the RHP quantifies a risk (e.g. 'top 10 customers
contributed 62%'), severity comes from numeric thresholds; otherwise from
pattern strength. Output feeds the heatmap, risk score and red flags.
"""
from __future__ import annotations

import re

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _snippet(text: str, match: re.Match, width: int = 220) -> str:
    s = max(0, match.start() - width // 2)
    return re.sub(r"\s+", " ", text[s:s + width + len(match.group(0))]).strip()


def _pct_severity(pct: float, bands: tuple[float, float, float]) -> str:
    lo, mid, hi = bands
    if pct >= hi:
        return "critical"
    if pct >= mid:
        return "high"
    if pct >= lo:
        return "medium"
    return "low"


def analyze_risks(pages: list[dict], sections: dict, fin: dict, entities: dict) -> dict:
    findings: list[dict] = []
    rf = sections.get("risk_factors") or {}
    biz = sections.get("our_business") or {}
    rf_text = rf.get("text", "")
    combined = rf_text + "\n" + biz.get("text", "")
    rf_page = rf.get("page_start")

    def add(risk_type: str, severity: str, title: str, detail: str = "",
            evidence: str = "", page: int | None = None, quantified: dict | None = None):
        findings.append({"risk_type": risk_type, "severity": severity, "title": title,
                         "detail": detail, "evidence_text": evidence[:400],
                         "source_page": page or rf_page, "quantified": quantified})

    # -- customer / supplier concentration (quantified when % disclosed) -----
    for kind, noun, label, bands in [("customer_concentration", "customers?", "customer", (30, 50, 70)),
                                     ("supplier_concentration", "suppliers?|vendors?", "supplier", (40, 60, 80))]:
        m = re.search(rf"top\s+(?:ten|five|10|5|three|3)?\s*(?:{noun})[^.\n]{{0,200}}?([\d]{{1,2}}(?:\.\d+)?)\s*%", combined, re.I)
        if m:
            pct = float(m.group(1))
            add(kind, _pct_severity(pct, bands), f"Top {label} concentration: {pct:.0f}%",
                f"Concentration of {pct:.0f}% disclosed in the prospectus.",
                _snippet(combined, m), quantified={"metric": f"top_{label}_share_pct", "value": pct})
        elif re.search(rf"depend\w*\s+on\s+(?:a\s+)?(?:limited|few|small)\s+(?:number\s+of\s+)?(?:{noun})", combined, re.I):
            add(kind, "medium", f"Dependence on limited {label}s (unquantified)")

    # -- debt burden (quantified from extracted financials) -------------------
    from .financial_extractor import get_metric
    debt, nw = get_metric(fin, "total_debt"), get_metric(fin, "net_worth")
    if debt is not None and nw and nw > 0:
        de = debt / nw
        if de >= 0.75:
            add("debt_burden", "critical" if de >= 2 else "high" if de >= 1.25 else "medium",
                f"Debt/Equity {de:.2f}",
                f"Total borrowings ₹{debt:,.0f} cr against net worth ₹{nw:,.0f} cr.",
                quantified={"metric": "debt_equity", "value": round(de, 2)},
                page=fin.get("source_pages", {}).get("total_debt"))

    # -- working capital stress -----------------------------------------------
    ca, cl = get_metric(fin, "current_assets"), get_metric(fin, "current_liabilities")
    if ca is not None and cl and cl > 0:
        cr = ca / cl
        if cr < 1.1:
            add("working_capital", "high" if cr < 0.9 else "medium", f"Current ratio {cr:.2f}",
                "Current liabilities nearly cover or exceed current assets.",
                quantified={"metric": "current_ratio", "value": round(cr, 2)},
                page=fin.get("source_pages", {}).get("current_assets"))
    m = re.search(r"[^.\n]{0,120}negative\s+working\s+capital[^.\n]{0,120}", combined, re.I)
    if m and not any(f["risk_type"] == "working_capital" for f in findings):
        add("working_capital", "medium", "Negative working capital disclosed", evidence=m.group(0))

    # -- negative operating cash flow -----------------------------------------
    order = fin.get("fiscal_order") or []
    neg_years = [fy for fy in order if (fin["series"].get(fy, {}).get("cfo") or 0) < 0]
    if neg_years:
        sev = "high" if len(neg_years) >= 2 else "medium"
        add("negative_cash_flow", sev, f"Negative operating cash flow in {', '.join(neg_years)}",
            quantified={"metric": "cfo_negative_years", "value": len(neg_years)},
            page=fin.get("source_pages", {}).get("cfo"))
    elif re.search(r"negative\s+cash\s+flows?", rf_text, re.I):
        add("negative_cash_flow", "medium", "Company discloses history of negative cash flows")

    # -- litigation ------------------------------------------------------------
    lit = entities.get("litigation") or {}
    counts = lit.get("counts") or {}
    if counts.get("criminal"):
        add("litigation", "high" if counts["criminal"] > 2 else "medium",
            f"{counts['criminal']} criminal proceeding(s) outstanding",
            page=lit.get("source_page"), quantified={"metric": "criminal_cases", "value": counts["criminal"]})
    total_cases = sum(counts.values())
    if total_cases > 20:
        add("litigation", "medium", f"{total_cases} total legal proceedings outstanding", page=lit.get("source_page"))

    # -- promoter pledging -------------------------------------------------------
    pledge = entities.get("pledging") or {}
    if pledge.get("pledged"):
        add("promoter_pledging", "high", "Promoter share pledging disclosed",
            evidence=pledge.get("evidence") or "", page=pledge.get("source_page"))

    # -- pattern-based classes ---------------------------------------------------
    pattern_risks = [
        ("regulatory", r"[^.\n]{0,120}(?:revocation|non[- ]renewal|failure\s+to\s+(?:obtain|maintain|renew))\s+[^.\n]{0,60}(?:licen[cs]es?|approvals?|permits?)[^.\n]{0,100}", "medium",
         "Business depends on regulatory licences/approvals"),
        ("industry_cyclicality", r"[^.\n]{0,120}cyclical[^.\n]{0,120}", "medium", "Industry described as cyclical"),
        ("forex_exposure", r"[^.\n]{0,120}(?:foreign\s+(?:currency|exchange)\s+(?:risk|fluctuations?)|exchange\s+rate\s+fluctuations?)[^.\n]{0,120}", "low",
         "Foreign-currency exposure disclosed"),
        ("key_personnel", r"[^.\n]{0,120}(?:depend\w*\s+(?:heavily\s+|significantly\s+)?on\s+(?:our\s+)?(?:promoters?|key\s+manage|senior\s+manage))[^.\n]{0,120}", "medium",
         "Dependence on promoters / key personnel"),
        ("government_dependency", r"[^.\n]{0,120}(?:government\s+(?:contracts?|tenders?|customers?)|public\s+sector\s+(?:undertakings?|customers?))[^.\n]{0,120}", "medium",
         "Revenue dependence on government/PSU clients"),
        ("one_time_revenue", r"[^.\n]{0,120}(?:non[- ]recurring|one[- ]time)\s+(?:revenue|income|gains?)[^.\n]{0,120}", "medium",
         "One-time / non-recurring revenue flagged"),
        ("competition", r"[^.\n]{0,120}(?:highly|intensely)\s+competitive[^.\n]{0,120}", "low", "Highly competitive industry"),
    ]
    for risk_type, pattern, severity, title in pattern_risks:
        m = re.search(pattern, rf_text, re.I)
        if m:
            add(risk_type, severity, title, evidence=_snippet(rf_text, m))

    # -- forex quantified export share ------------------------------------------
    m = re.search(r"exports?[^.\n]{0,140}?([\d]{1,2}(?:\.\d+)?)\s*%[^.\n]{0,80}(?:revenue|turnover)", combined, re.I)
    if m and float(m.group(1)) >= 30:
        add("forex_exposure", "medium", f"Exports ≈ {m.group(1)}% of revenue", evidence=_snippet(combined, m),
            quantified={"metric": "export_share_pct", "value": float(m.group(1))})

    findings = _dedupe(findings)
    return {"findings": findings,
            "risk_score": compute_risk_score(findings),
            "boilerplate": boilerplate_stats(rf_text),
            "heatmap": heatmap(findings)}


def _dedupe(findings: list[dict]) -> list[dict]:
    best: dict[tuple, dict] = {}
    for f in findings:
        k = (f["risk_type"], f["title"][:40])
        if k not in best or SEVERITY_ORDER[f["severity"]] > SEVERITY_ORDER[best[k]["severity"]]:
            best[k] = f
    return sorted(best.values(), key=lambda f: -SEVERITY_ORDER[f["severity"]])


def compute_risk_score(findings: list[dict]) -> float:
    """100 = clean. Subtract per finding by severity."""
    penalty = {"critical": 18, "high": 10, "medium": 4, "low": 1}
    return max(0.0, 100.0 - sum(penalty[f["severity"]] for f in findings))


def boilerplate_stats(rf_text: str) -> dict:
    """Specific (quantified) risk factors are informative; generic ones are noise.
    Heuristic: share of risk paragraphs containing a number or %."""
    paras = [p for p in re.split(r"\n\s*\n|\n(?=\d{1,3}\.\s)", rf_text) if len(p.strip()) > 200]
    if not paras:
        return {"total_factors": 0, "specific": 0, "specificity_ratio": None}
    specific = sum(1 for p in paras if re.search(r"\d+(?:\.\d+)?\s*%|₹\s*[\d,]+", p))
    return {"total_factors": len(paras), "specific": specific,
            "specificity_ratio": round(specific / len(paras), 2)}


RISK_CLASSES = ["customer_concentration", "supplier_concentration", "litigation", "debt_burden",
                "working_capital", "negative_cash_flow", "promoter_pledging", "regulatory",
                "industry_cyclicality", "forex_exposure", "key_personnel", "one_time_revenue",
                "government_dependency", "competition"]


def heatmap(findings: list[dict]) -> list[dict]:
    cells = []
    for rc in RISK_CLASSES:
        matches = [f for f in findings if f["risk_type"] == rc]
        sev = max((SEVERITY_ORDER[f["severity"]] for f in matches), default=-1)
        cells.append({"risk_type": rc,
                      "severity": {-1: "none", 0: "low", 1: "medium", 2: "high", 3: "critical"}[sev],
                      "count": len(matches)})
    return cells
