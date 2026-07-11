"""Run the full analysis pipeline on a PDF without a server or database.

Mirrors orchestrator.run_analysis stage-for-stage but returns the report dict
instead of persisting — used by the GitHub Actions automation, and handy for
one-off local runs:

    python tools/analyze_standalone.py path/to/rhp.pdf > report.json
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.pipeline import (  # noqa: E402
    entity_extractor as ent,
    financial_extractor as fin,
    forensic,
    llm_layer,
    pdf_processor,
    promoter_analyzer,
    report_builder,
    risk_analyzer,
    scoring,
    section_extractor,
    valuation,
)

log = logging.getLogger("pipeline")


def _safe(fn, default):
    try:
        return fn()
    except Exception as exc:
        log.warning("pipeline sub-stage failed: %s", exc)
        return default


def analyze_pdf(pdf_path: str, doc_type: str = "RHP", use_llm: bool = False) -> dict:
    """Full deterministic pipeline -> report dict (same JSON the API serves)."""
    ctx: dict = {"doc_type": doc_type}
    pdf = pdf_processor.process_pdf(pdf_path)
    ctx.update(page_count=pdf["page_count"], readable_ratio=round(pdf["readable_ratio"], 3))
    pages = pdf["pages"]

    sections = section_extractor.extract_sections(pages, pdf["toc"])
    ctx["sections"] = sections
    ctx["section_hit_rate"] = round(section_extractor.section_hit_rate(sections), 2)

    ctx["financials"] = _safe(lambda: fin.extract_financials(pdf_path, pages, sections),
                              {"series": {}, "fiscal_order": [], "unit": "crores",
                               "source_pages": {}, "confidence": {}, "anchor_pages": []})

    company = _safe(lambda: ent.extract_company_name(pages), None)
    issue = _safe(lambda: ent.extract_issue_details(pages, sections), {"source_pages": {}})
    issue["peers_json"] = _safe(lambda: ent.extract_peers(pdf_path, pages, sections,
                                                          company_name=company), [])
    issue["objects_json"] = _safe(lambda: ent.extract_objects(pages, sections), [])
    entities = {
        "litigation": _safe(lambda: ent.extract_litigation(pages, sections), {"found": False, "counts": {}}),
        "rpt": _safe(lambda: ent.extract_rpt(pages, sections), {"found": False}),
        "contingent": _safe(lambda: ent.extract_contingent_liabilities(pages, sections), {"found": False}),
        "dividend": _safe(lambda: ent.extract_dividend(pages, sections), {"found": False}),
        "pledging": _safe(lambda: ent.detect_pledging(pages, sections), {"pledged": False}),
        "auditor": _safe(lambda: ent.detect_auditor_flags(pages, sections), {}),
    }
    ctx["issue"], ctx["entities"], ctx["company_name"] = issue, entities, company

    ctx["risks"] = _safe(lambda: risk_analyzer.analyze_risks(pages, sections, ctx["financials"], entities),
                         {"findings": [], "risk_score": 50, "boilerplate": {}, "heatmap": []})

    ctx["ratios"] = _safe(lambda: valuation.compute_ratios(ctx["financials"]), {"margin_series": []})
    issuer_pe, pe_page = _safe(lambda: ent.extract_issuer_pe(pages, sections), (None, None))
    ctx["valuation"] = _safe(lambda: valuation.valuation_call(issuer_pe, issue.get("peers_json") or [],
                                                              ctx["ratios"], price_high=issue.get("price_band_high")),
                             {"call": "indeterminate", "reasoning": []})
    ctx["valuation"]["issuer_pe_page"] = pe_page

    ctx["forensic"] = _safe(lambda: forensic.run_forensics(ctx["financials"]),
                            {"flags": [], "checks": [], "strength_score": {"passed": 0, "total": 0},
                             "cap_triggered": False})
    ctx["forensic"]["consistency"] = []

    ctx["promoter"] = _safe(
        lambda: promoter_analyzer.analyze_promoters(pages, sections, entities, issue),
        {"names": [], "experience_claims": [], "board": {}, "group_company_conflicts": False,
         "past_ventures_mentioned": False, "pre_issue_pct": None, "post_issue_pct": None,
         "pledging": {"pledged": False}, "source_pages": {}})

    ctx["scoring"] = scoring.score_all(ctx)
    report = report_builder.build_report(ctx)
    if use_llm:
        report = _safe(lambda: llm_layer.enhance_report(report), report)
    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    if len(sys.argv) < 2:
        sys.exit("usage: python tools/analyze_standalone.py <pdf> [out.json]")
    rep = analyze_pdf(sys.argv[1])
    out = json.dumps(rep, ensure_ascii=False, indent=1)
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(out, encoding="utf-8")
        print(f"saved -> {sys.argv[2]}")
    else:
        print(out)
