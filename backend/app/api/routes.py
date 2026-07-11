import hashlib
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from ..config import DISCLAIMER, MAX_UPLOAD_MB, UPLOAD_DIR
from ..db import get_db
from ..models import Analysis, Document, MarketSignal, Report, Section
from ..pipeline.orchestrator import run_analysis
from ..pipeline import llm_layer

router = APIRouter()


class MarketSignalIn(BaseModel):
    gmp: float | None = None
    sub_qib: float | None = None
    sub_nii: float | None = None
    sub_rii: float | None = None
    sub_bnii: float | None = None
    sub_snii: float | None = None
    day1_gain: float | None = None


class QuestionIn(BaseModel):
    question: str


class ScoreLensIn(BaseModel):
    lens: str = "balanced"


@router.get("/health")
def health():
    return {"status": "ok", "disclaimer": DISCLAIMER}


@router.post("/documents", status_code=201)
def upload_document(background: BackgroundTasks, file: UploadFile = File(...),
                    db: DbSession = Depends(get_db)):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted.")

    doc = Document(filename=file.filename, stored_path="", doc_type="RHP")
    db.add(doc)
    db.flush()

    dest_dir = Path(UPLOAD_DIR) / doc.id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "document.pdf"
    sha = hashlib.sha256()
    size = 0
    with dest.open("wb") as out:
        while chunk := file.file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_MB * 1024 * 1024:
                out.close()
                shutil.rmtree(dest_dir, ignore_errors=True)
                db.rollback()
                raise HTTPException(413, f"File exceeds {MAX_UPLOAD_MB} MB limit.")
            sha.update(chunk)
            out.write(chunk)

    doc.stored_path = str(dest)
    doc.file_size = size
    doc.sha256 = sha.hexdigest()
    if "drhp" in (file.filename or "").lower() or "draft" in (file.filename or "").lower():
        doc.doc_type = "DRHP"

    analysis = Analysis(document_id=doc.id, status="queued")
    db.add(analysis)
    db.commit()

    background.add_task(run_analysis, analysis.id)
    return {"document_id": doc.id, "analysis_id": analysis.id, "status": "queued"}


@router.get("/analyses")
def list_analyses(db: DbSession = Depends(get_db)):
    rows = db.execute(
        select(Analysis, Document.filename, Report.overall_score, Report.verdict)
        .join(Document, Analysis.document_id == Document.id)
        .outerjoin(Report, Report.analysis_id == Analysis.id)
        .order_by(Analysis.created_at.desc())
    ).all()
    return [{
        "id": a.id, "company_name": a.company_name, "filename": filename,
        "status": a.status, "stage": a.stage, "progress": a.progress,
        "confidence": a.confidence, "is_demo": a.is_demo,
        "overall_score": overall, "verdict": verdict,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    } for a, filename, overall, verdict in rows]


@router.get("/analyses/{analysis_id}")
def get_analysis(analysis_id: str, db: DbSession = Depends(get_db)):
    a = db.get(Analysis, analysis_id)
    if not a:
        raise HTTPException(404, "Analysis not found")
    doc = db.get(Document, a.document_id)
    return {"id": a.id, "company_name": a.company_name, "status": a.status,
            "stage": a.stage, "progress": a.progress, "error": a.error,
            "confidence": a.confidence, "is_demo": a.is_demo,
            "filename": doc.filename if doc else None,
            "page_count": doc.page_count if doc else None,
            "created_at": a.created_at.isoformat() if a.created_at else None}


@router.get("/analyses/{analysis_id}/report")
def get_report(analysis_id: str, db: DbSession = Depends(get_db)):
    report = db.get(Report, analysis_id)
    if not report:
        a = db.get(Analysis, analysis_id)
        if not a:
            raise HTTPException(404, "Analysis not found")
        raise HTTPException(409, f"Report not ready (status: {a.status}, stage: {a.stage})")
    signal = db.get(MarketSignal, analysis_id)
    payload = dict(report.report_json)
    payload["market_signals"] = ({
        "gmp": signal.gmp, "sub_qib": signal.sub_qib, "sub_nii": signal.sub_nii,
        "sub_rii": signal.sub_rii, "sub_bnii": signal.sub_bnii,
        "sub_snii": signal.sub_snii, "day1_gain": signal.day1_gain,
        "note": "User-supplied market context. Not derived from the prospectus and "
                "not part of the fundamental score.",
    } if signal else None)
    return payload


@router.post("/analyses/{analysis_id}/market-signals")
def set_market_signals(analysis_id: str, body: MarketSignalIn, db: DbSession = Depends(get_db)):
    if not db.get(Analysis, analysis_id):
        raise HTTPException(404, "Analysis not found")
    signal = db.get(MarketSignal, analysis_id)
    if not signal:
        signal = MarketSignal(analysis_id=analysis_id)
        db.add(signal)
    signal.gmp, signal.sub_qib = body.gmp, body.sub_qib
    signal.sub_nii, signal.sub_rii = body.sub_nii, body.sub_rii
    signal.sub_bnii, signal.sub_snii = body.sub_bnii, body.sub_snii
    signal.day1_gain = body.day1_gain
    signal.noted_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.post("/analyses/{analysis_id}/qa")
def ask_question(analysis_id: str, body: QuestionIn, db: DbSession = Depends(get_db)):
    if not db.get(Analysis, analysis_id):
        raise HTTPException(404, "Analysis not found")
    if not llm_layer.llm_available():
        raise HTTPException(503, "Q&A requires the AI layer. Set ANTHROPIC_API_KEY, or use a Claude "
                                 "subscription via LLM_PROVIDER=claude_cli (Claude Code must be installed).")
    sections = db.execute(select(Section).where(Section.analysis_id == analysis_id,
                                                Section.found == True)).scalars().all()  # noqa: E712
    corpus = []
    for s in sections:
        text = s.text_excerpt or ""
        if s.full_text_path:
            try:
                text = Path(s.full_text_path).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                pass
        corpus.append({"title": s.title, "page_start": s.page_start,
                       "page_end": s.page_end, "text": text})
    result = llm_layer.answer_question(body.question, corpus)
    result["disclaimer"] = DISCLAIMER
    return result


@router.get("/analyses/{analysis_id}/listing-forecast")
def listing_forecast(analysis_id: str, llm: bool = False, db: DbSession = Depends(get_db)):
    """Pre-listing hype-cycle forecast (listing premium, break-below-offer,
    bottom window, recovery) from RHP-only features. `?llm=1` adds the AI
    engine's view computed from the same anonymized features."""
    from ..pipeline import listing_predictor
    report = db.get(Report, analysis_id)
    if not report:
        raise HTTPException(404, "Report not found or not ready")
    signal = db.get(MarketSignal, analysis_id)
    signals = ({"gmp": signal.gmp, "sub_qib": signal.sub_qib, "sub_nii": signal.sub_nii,
                "sub_rii": signal.sub_rii, "sub_bnii": signal.sub_bnii,
                "sub_snii": signal.sub_snii, "day1_gain": signal.day1_gain}
               if signal else None)
    out = listing_predictor.forecast(dict(report.report_json), use_llm=llm, signals=signals)
    out["disclaimer"] = DISCLAIMER
    return out


@router.get("/analyses/{analysis_id}/similar")
def similar_analyses(analysis_id: str, db: DbSession = Depends(get_db)):
    """Historical-comparison: nearest completed analyses by score-vector distance."""
    from ..models import Score
    target = db.execute(select(Score).where(Score.analysis_id == analysis_id)).scalars().all()
    if not target:
        return []
    tvec = {s.category: s.score for s in target}
    others = db.execute(
        select(Analysis).where(Analysis.id != analysis_id, Analysis.status == "completed")
    ).scalars().all()
    results = []
    for other in others:
        scores = db.execute(select(Score).where(Score.analysis_id == other.id)).scalars().all()
        if not scores:
            continue
        common = [(tvec[s.category], s.score) for s in scores if s.category in tvec]
        if len(common) < 5:
            continue
        dist = (sum((a - b) ** 2 for a, b in common) / len(common)) ** 0.5
        report = db.get(Report, other.id)
        results.append({"analysis_id": other.id, "company_name": other.company_name,
                        "distance": round(dist, 1),
                        "overall_score": report.overall_score if report else None,
                        "verdict": report.verdict if report else None})
    return sorted(results, key=lambda r: r["distance"])[:5]
