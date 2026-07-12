"""Runs the full analysis pipeline for one document and persists everything.

Stage failures degrade gracefully: the error is recorded, dependent outputs
score neutral with reduced confidence, and the run still completes.
"""
from __future__ import annotations

import logging
import traceback
from datetime import datetime
from pathlib import Path

from ..config import UPLOAD_DIR
from ..db import SessionLocal
from ..models import (
    Analysis, Document, Financial, IssueDetails, Report, RiskFinding, Score, Section,
)
from . import (
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

STAGES = ["document_processing", "section_extraction", "financial_extraction",
          "entity_extraction", "risk_analysis", "valuation", "forensic",
          "promoter_analysis", "scoring", "report"]


def run_analysis(analysis_id: str) -> None:
    db = SessionLocal()
    try:
        analysis = db.get(Analysis, analysis_id)
        if not analysis:
            return
        document = db.get(Document, analysis.document_id)
        analysis.status = "processing"
        analysis.started_at = datetime.utcnow()
        db.commit()

        def stage(name: str, progress: float):
            analysis.stage = name
            analysis.progress = progress
            db.commit()

        ctx: dict = {"doc_type": document.doc_type}

        # 1 ─ PDF processing
        stage("document_processing", 0.05)
        pdf = pdf_processor.process_pdf(document.stored_path)
        document.page_count = pdf["page_count"]
        ctx.update(page_count=pdf["page_count"], readable_ratio=round(pdf["readable_ratio"], 3))
        pages = pdf["pages"]

        # 2 ─ Sections
        stage("section_extraction", 0.18)
        sections = section_extractor.extract_sections(pages, pdf["toc"])
        ctx["sections"] = sections
        ctx["section_hit_rate"] = round(section_extractor.section_hit_rate(sections), 2)
        _persist_sections(db, analysis_id, sections)

        # 3 ─ Financials
        stage("financial_extraction", 0.35)
        ctx["financials"] = _safe(lambda: fin.extract_financials(document.stored_path, pages, sections),
                                  {"series": {}, "fiscal_order": [], "unit": "crores",
                                   "source_pages": {}, "confidence": {}, "anchor_pages": []})
        _persist_financials(db, analysis_id, ctx["financials"])

        # 4 ─ Entities
        stage("entity_extraction", 0.5)
        company = _safe(lambda: ent.extract_company_name(pages), None)
        issue = _safe(lambda: ent.extract_issue_details(pages, sections, document.stored_path), {"source_pages": {}})
        issue["peers_json"] = _safe(lambda: ent.extract_peers(document.stored_path, pages, sections,
                                                              company_name=company), [])
        issue["objects_json"] = _safe(lambda: ent.extract_objects(pages, sections), [])
        entities = {
            "litigation": _safe(lambda: ent.extract_litigation(pages, sections), {"found": False, "counts": {}}),
            "rpt": _safe(lambda: ent.extract_rpt(pages, sections, document.stored_path), {"found": False}),
            "contingent": _safe(lambda: ent.extract_contingent_liabilities(pages, sections, document.stored_path), {"found": False}),
            "dividend": _safe(lambda: ent.extract_dividend(pages, sections), {"found": False}),
            "pledging": _safe(lambda: ent.detect_pledging(pages, sections), {"pledged": False}),
            "auditor": _safe(lambda: ent.detect_auditor_flags(pages, sections), {}),
        }
        ctx["issue"], ctx["entities"], ctx["company_name"] = issue, entities, company
        analysis.company_name = company
        _persist_issue(db, analysis_id, issue)

        # 5 ─ Risks
        stage("risk_analysis", 0.62)
        ctx["risks"] = _safe(lambda: risk_analyzer.analyze_risks(pages, sections, ctx["financials"], entities),
                             {"findings": [], "risk_score": 50, "boilerplate": {}, "heatmap": []})
        _persist_risks(db, analysis_id, ctx["risks"])

        # 6 ─ Valuation
        stage("valuation", 0.72)
        ctx["ratios"] = _safe(lambda: valuation.compute_ratios(ctx["financials"]), {"margin_series": []})
        issuer_pe, pe_page = _safe(lambda: ent.extract_issuer_pe(pages, sections), (None, None))
        issuer_eps = _safe(lambda: ent.extract_issuer_eps(document.stored_path, sections), None)
        ctx["valuation"] = _safe(lambda: valuation.valuation_call(issuer_pe, issue.get("peers_json") or [],
                                                                  ctx["ratios"], price_high=issue.get("price_band_high"),
                                                                  issuer_eps=issuer_eps),
                                 {"call": "indeterminate", "reasoning": []})
        ctx["valuation"]["issuer_pe_page"] = pe_page

        # 7 ─ Forensic
        stage("forensic", 0.78)
        ctx["forensic"] = _safe(lambda: forensic.run_forensics(ctx["financials"]),
                                {"flags": [], "checks": [], "strength_score": {"passed": 0, "total": 0},
                                 "cap_triggered": False})
        mdna_text = (sections.get("mdna") or {}).get("text", "")
        ctx["forensic"]["consistency"] = _safe(
            lambda: llm_layer.consistency_check(mdna_text, {
                "revenue_cagr": ctx["ratios"].get("revenue_cagr"),
                "pat_cagr": ctx["ratios"].get("pat_cagr"),
                "net_margin": ctx["ratios"].get("net_margin"),
                "latest": ctx["financials"]["series"].get((ctx["financials"].get("fiscal_order") or [""])[0], {}),
            }), [])

        # 8 ─ Promoters
        stage("promoter_analysis", 0.84)
        ctx["promoter"] = _safe(
            lambda: promoter_analyzer.analyze_promoters(pages, sections, entities, issue),
            {"names": [], "experience_claims": [], "board": {}, "group_company_conflicts": False,
             "past_ventures_mentioned": False, "pre_issue_pct": None, "post_issue_pct": None,
             "pledging": {"pledged": False}, "source_pages": {}})

        # 9 ─ Scoring
        stage("scoring", 0.9)
        ctx["scoring"] = scoring.score_all(ctx)
        _persist_scores(db, analysis_id, ctx["scoring"])

        # 10 ─ Report
        stage("report", 0.96)
        report = report_builder.build_report(ctx)
        report = _safe(lambda: llm_layer.enhance_report(report), report)
        _persist_report(db, analysis_id, report, ctx)

        analysis.status = "completed"
        analysis.stage = "done"
        analysis.progress = 1.0
        analysis.confidence = ctx["scoring"]["confidence"]
        analysis.completed_at = datetime.utcnow()
        db.commit()
    except Exception as exc:  # pipeline-level failure
        log.error("analysis %s failed: %s\n%s", analysis_id, exc, traceback.format_exc())
        db.rollback()
        analysis = db.get(Analysis, analysis_id)
        if analysis:
            analysis.status = "failed"
            analysis.error = f"{type(exc).__name__}: {exc}"
            db.commit()
    finally:
        db.close()


def _safe(fn, default):
    try:
        return fn()
    except Exception as exc:
        log.warning("pipeline sub-stage failed: %s", exc)
        return default


def _persist_sections(db, analysis_id: str, sections: dict) -> None:
    text_dir = Path(UPLOAD_DIR) / analysis_id / "sections"
    text_dir.mkdir(parents=True, exist_ok=True)
    for key, sec in sections.items():
        full_path = None
        if sec.get("text"):
            full_path = str(text_dir / f"{key}.txt")
            Path(full_path).write_text(sec["text"], encoding="utf-8", errors="ignore")
        db.add(Section(analysis_id=analysis_id, key=key, title=sec.get("title"),
                       page_start=sec.get("page_start"), page_end=sec.get("page_end"),
                       char_count=len(sec.get("text") or ""),
                       text_excerpt=(sec.get("text") or "")[:1500] or None,
                       full_text_path=full_path, found=bool(sec.get("found")),
                       method=sec.get("method")))
    db.commit()


def _persist_financials(db, analysis_id: str, fin_data: dict) -> None:
    order = fin_data.get("fiscal_order") or []
    for rank, label in enumerate(order):
        for metric, value in fin_data["series"].get(label, {}).items():
            db.add(Financial(analysis_id=analysis_id, fiscal_label=label, fiscal_rank=rank,
                             metric=metric, value=value, unit_source=fin_data.get("unit"),
                             source_page=fin_data.get("source_pages", {}).get(metric),
                             confidence=fin_data.get("confidence", {}).get(metric)))
    db.commit()


def _persist_issue(db, analysis_id: str, issue: dict) -> None:
    db.add(IssueDetails(
        analysis_id=analysis_id,
        price_band_low=issue.get("price_band_low"), price_band_high=issue.get("price_band_high"),
        face_value=issue.get("face_value"), fresh_issue_cr=issue.get("fresh_issue_cr"),
        ofs_cr=issue.get("ofs_cr"), total_issue_cr=issue.get("total_issue_cr"),
        lot_size=issue.get("lot_size"), listing_at=issue.get("listing_at"),
        pre_issue_promoter_pct=issue.get("pre_issue_promoter_pct"),
        post_issue_promoter_pct=issue.get("post_issue_promoter_pct"),
        objects_json=issue.get("objects_json"), peers_json=issue.get("peers_json"),
        source_pages=issue.get("source_pages")))
    db.commit()


def _persist_risks(db, analysis_id: str, risks: dict) -> None:
    for f in risks.get("findings", []):
        db.add(RiskFinding(analysis_id=analysis_id, risk_type=f["risk_type"],
                           severity=f["severity"], title=f["title"][:500],
                           detail=f.get("detail"), evidence_text=f.get("evidence_text"),
                           source_page=f.get("source_page"), quantified=f.get("quantified")))
    db.commit()


def _persist_scores(db, analysis_id: str, scoring_out: dict) -> None:
    for cat, data in scoring_out["categories"].items():
        db.add(Score(analysis_id=analysis_id, category=cat, score=data["score"],
                     weight=data["weight"], rules_json=data["rules"]))
    db.commit()


def _persist_report(db, analysis_id: str, report: dict, ctx: dict) -> None:
    db.add(Report(analysis_id=analysis_id,
                  overall_score=ctx["scoring"]["overall"],
                  verdict=report.get("verdict"),
                  valuation_call=ctx["valuation"].get("call"),
                  confidence=ctx["scoring"]["confidence"],
                  report_json=report,
                  llm_enhanced=report.get("meta", {}).get("llm_enhanced", False)))
    db.commit()
