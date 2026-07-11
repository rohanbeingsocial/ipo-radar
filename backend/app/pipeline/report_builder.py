"""Stage 9: assemble the full report payload the dashboard renders.

Everything here is deterministic templating over extracted, cited facts.
The optional LLM layer may later polish narrative fields, but it can only
rephrase what exists — it cannot add claims.
"""
from __future__ import annotations

from ..config import DISCLAIMER
from .financial_extractor import get_metric

CALL_LABELS = {"undervalued": "Undervalued vs peers", "fairly_valued": "Fairly valued vs peers",
               "fairly_valued_expensive": "Expensive side of fair", "overvalued": "Overvalued vs peers",
               "indeterminate": "Valuation indeterminate"}


def _quality_tier(score: float) -> str:
    if score >= 75:
        return "Strong fundamentals"
    if score >= 60:
        return "Solid fundamentals"
    if score >= 45:
        return "Mixed fundamentals"
    return "Weak fundamentals"


def _risk_tier(risk_score: float) -> str:
    if risk_score >= 75:
        return "low risk profile"
    if risk_score >= 55:
        return "moderate risk profile"
    if risk_score >= 35:
        return "elevated risk profile"
    return "high risk profile"


def _fmt_cr(v) -> str:
    return f"₹{v:,.0f} cr" if isinstance(v, (int, float)) else "n/a"


def _rule_statement(r: dict) -> dict:
    return {"text": r["evidence"] or r["rule"].replace("_", " "),
            "rule": r["rule"], "category": r["category"],
            "source_pages": r.get("source_pages", []),
            "confidence": r.get("confidence", 0.9)}


def build_report(ctx: dict) -> dict:
    fin, ratios, issue = ctx["financials"], ctx["ratios"], ctx["issue"]
    risks, val, scoring = ctx["risks"], ctx["valuation"], ctx["scoring"]
    forensic, promoter = ctx["forensic"], ctx["promoter"]
    sections = ctx["sections"]

    all_rules = [r for c in scoring["categories"].values() for r in c["rules"]]
    included = [r for r in all_rules if r["included"]]
    strong = sorted((r for r in included if r["max_points"] >= 5 and r["points"] / r["max_points"] >= 0.72),
                    key=lambda r: -(r["points"] / r["max_points"]) * r["max_points"])
    weak = sorted((r for r in included if r["max_points"] >= 5 and r["points"] / r["max_points"] <= 0.35),
                  key=lambda r: (r["points"] / r["max_points"]) - r["max_points"] / 30)
    unknown = [r for r in all_rules if not r["included"]]

    bull = [_rule_statement(r) for r in strong[:7]]
    bear = [_rule_statement(r) for r in weak[:7]]
    for f in risks.get("findings", []):
        if f["severity"] in ("critical", "high") and len(bear) < 9:
            bear.append({"text": f["title"] + (f" — {f['detail']}" if f.get("detail") else ""),
                         "rule": f["risk_type"], "category": "risk",
                         "source_pages": [f.get("source_page")], "confidence": 0.85})
    for f in forensic.get("flags", []):
        if len(bear) < 10:
            bear.append({"text": f["detail"], "rule": f["rule"], "category": "forensic",
                         "source_pages": [f.get("source_page")], "confidence": 0.8})
    neutral = [{"text": f"{r['rule'].replace('_', ' ').capitalize()}: {r['evidence'] or 'not determinable from the document'}",
                "rule": r["rule"], "category": r["category"], "source_pages": [], "confidence": 0.5}
               for r in unknown[:8]]

    green_flags = bull[:8]
    red_flags = bear[:10]

    overall = scoring["overall"]
    risk_cat = scoring["categories"].get("risk_level", {}).get("score", 50)
    verdict = f"{_quality_tier(overall)} · {CALL_LABELS.get(val.get('call'), 'Valuation indeterminate')} · {_risk_tier(risk_cat)}"

    order = fin.get("fiscal_order") or []
    fin_series = [{"fy": fy, **{m: fin["series"][fy].get(m) for m in
                   ("revenue", "ebitda", "pat", "net_worth", "total_debt", "cfo", "capex",
                    "current_assets", "current_liabilities", "receivables", "total_assets")}}
                  for fy in reversed(order)]  # oldest→latest for charts

    exec_summary = _exec_summary(ctx, verdict)
    questions = _questions(ctx, unknown)

    return {
        "meta": {"company_name": ctx.get("company_name"), "page_count": ctx.get("page_count"),
                 "doc_type": ctx.get("doc_type", "RHP"), "confidence": scoring["confidence"],
                 "coverage": scoring["coverage"], "readable_ratio": ctx.get("readable_ratio"),
                 "section_hit_rate": ctx.get("section_hit_rate"), "llm_enhanced": False,
                 "disclaimer": DISCLAIMER},
        "executive_summary": exec_summary,
        "snapshot": {
            "price_band": [issue.get("price_band_low"), issue.get("price_band_high")],
            "face_value": issue.get("face_value"), "lot_size": issue.get("lot_size"),
            "fresh_issue_cr": issue.get("fresh_issue_cr"), "ofs_cr": issue.get("ofs_cr"),
            "total_issue_cr": issue.get("total_issue_cr"), "listing_at": issue.get("listing_at"),
            "pre_issue_promoter_pct": issue.get("pre_issue_promoter_pct"),
            "post_issue_promoter_pct": issue.get("post_issue_promoter_pct"),
            "objects": issue.get("objects_json") or [],
            "source_pages": issue.get("source_pages", {}),
        },
        "financials": {"series": fin_series, "unit_note": "All values normalized to ₹ crore.",
                       "source_pages": fin.get("source_pages", {}),
                       "ratios": {k: v for k, v in ratios.items() if k != "margin_series"},
                       "margin_series": ratios.get("margin_series", [])},
        "valuation": {**val, "peers": issue.get("peers_json") or [],
                      "call_label": CALL_LABELS.get(val.get("call"), "Indeterminate")},
        "risk": {"score": risks.get("risk_score"), "heatmap": risks.get("heatmap"),
                 "findings": risks.get("findings"), "boilerplate": risks.get("boilerplate")},
        "forensic": forensic,
        "promoter": promoter,
        "industry": {"excerpt": (sections.get("industry_overview") or {}).get("text", "")[:1200].strip(),
                     "source_page": (sections.get("industry_overview") or {}).get("page_start")},
        "scoring": {"overall": overall, "lens": scoring["lens"], "weights": scoring["weights"],
                    "cap_note": scoring.get("cap_note"),
                    "categories": {k: {"score": v["score"], "weight": v["weight"],
                                       "coverage": v["coverage"], "rules": v["rules"]}
                                   for k, v in scoring["categories"].items()}},
        "verdict": verdict,
        "cases": {"bull": bull, "bear": bear, "neutral": neutral},
        "flags": {"green": green_flags, "red": red_flags},
        "questions": questions,
        "sections_index": [{"key": k, "title": s.get("title"), "found": s.get("found"),
                            "page_start": s.get("page_start"), "page_end": s.get("page_end"),
                            "method": s.get("method")} for k, s in sections.items()],
    }


def _exec_summary(ctx: dict, verdict: str) -> list[dict]:
    fin, ratios, issue, val = ctx["financials"], ctx["ratios"], ctx["issue"], ctx["valuation"]
    sp = fin.get("source_pages", {})
    out: list[dict] = []

    def para(text: str, pages: list | None = None):
        out.append({"text": text, "source_pages": [p for p in (pages or []) if p]})

    name = ctx.get("company_name") or "The issuer"
    rev, pat = get_metric(fin, "revenue"), get_metric(fin, "pat")
    order = fin.get("fiscal_order") or []
    if rev is not None:
        growth = ratios.get("revenue_cagr")
        para(f"{name} reported revenue of {_fmt_cr(rev)} and profit after tax of {_fmt_cr(pat)} in its "
             f"latest restated fiscal year ({order[0] if order else 'n/a'})"
             + (f", with revenue compounding at {growth * 100:.0f}% across the reported period" if growth is not None else "")
             + ".", [sp.get("revenue"), sp.get("pat")])
    total = issue.get("total_issue_cr")
    if total:
        fresh, ofs = issue.get("fresh_issue_cr") or 0, issue.get("ofs_cr") or 0
        para(f"The offer totals {_fmt_cr(total)} — {_fmt_cr(fresh)} fresh issue and {_fmt_cr(ofs)} offer for sale "
             f"({(ofs / total * 100):.0f}% of the offer is existing holders selling).",
             [issue.get("source_pages", {}).get("fresh_issue"), issue.get("source_pages", {}).get("ofs")])
    if val.get("issuer_pe") and val.get("peer_pe_median"):
        para(f"At the upper price band the issue is priced at {val['issuer_pe']:.1f}x earnings against a "
             f"listed-peer median of {val['peer_pe_median']:.1f}x ({CALL_LABELS.get(val.get('call'), '')}).")
    high_risks = [f for f in ctx["risks"].get("findings", []) if f["severity"] in ("critical", "high")]
    if high_risks:
        para("Principal risks identified: " + "; ".join(f["title"] for f in high_risks[:4]) + ".",
             [f.get("source_page") for f in high_risks[:2]])
    para(f"Composite assessment: {verdict}. Overall score {ctx['scoring']['overall']:.0f}/100 "
         f"(confidence: {ctx['scoring']['confidence']}). Every component is traceable in the Score tab.")
    return out


def _questions(ctx: dict, unknown_rules: list[dict]) -> list[str]:
    qs: list[str] = []
    issue, risks = ctx["issue"], ctx["risks"]
    ofs, fresh = issue.get("ofs_cr"), issue.get("fresh_issue_cr")
    if ofs and (fresh or 0) < ofs:
        qs.append("Why is the offer predominantly an exit for existing shareholders rather than fresh capital for growth?")
    for obj in (issue.get("objects_json") or []):
        if obj["category"] == "debt_repayment":
            qs.append("How was the debt now being repaid from IPO proceeds originally deployed, and what returns did it generate?")
        if obj["category"] == "general_corporate":
            qs.append("What specifically will 'general corporate purposes' funds be used for?")
    for f in risks.get("findings", []):
        if f["risk_type"] == "customer_concentration" and f["severity"] in ("high", "critical"):
            qs.append("What contractual protection exists with the top customers, and what is the renewal calendar?")
        if f["risk_type"] == "negative_cash_flow":
            qs.append("When does management expect operating cash flow to turn (and stay) positive?")
        if f["risk_type"] == "promoter_pledging":
            qs.append("What is the pledge-release plan for promoter shares after listing?")
    for r in unknown_rules[:5]:
        pretty = r["rule"].replace("_", " ")
        qs.append(f"The document did not yield a clear answer on {pretty} — ask management directly.")
    bp = risks.get("boilerplate") or {}
    if bp.get("specificity_ratio") is not None and bp["specificity_ratio"] < 0.35:
        qs.append("Most risk factors are generic boilerplate — which five risks does management actually lose sleep over?")
    seen: set[str] = set()
    return [q for q in qs if not (q in seen or seen.add(q))][:10]
