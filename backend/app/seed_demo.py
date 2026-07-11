"""Seed a demo analysis: generates the synthetic RHP and runs the real
pipeline on it, so the app is evaluable without a genuine prospectus.

Run:  python -m app.seed_demo   (from backend/)
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from .config import UPLOAD_DIR
from .db import SessionLocal, init_db
from .models import Analysis, Document, IssueDetails, MarketSignal
from .pipeline.orchestrator import run_analysis

BACKEND = Path(__file__).resolve().parent.parent


def seed(force: bool = False) -> str | None:
    init_db()
    db = SessionLocal()
    try:
        existing = db.query(Analysis).filter(Analysis.is_demo == True).all()  # noqa: E712
        if existing and not force:
            print(f"Demo analysis already exists: {existing[0].id} (status: {existing[0].status})")
            return existing[0].id
        for old in existing:  # force: replace, don't accumulate duplicates
            # IssueDetails/MarketSignal are not ORM relationships on Analysis,
            # so ORM cascade misses them — delete explicitly.
            db.query(IssueDetails).filter(IssueDetails.analysis_id == old.id).delete()
            db.query(MarketSignal).filter(MarketSignal.analysis_id == old.id).delete()
            doc = db.get(Document, old.document_id)
            db.delete(old)
            if doc:
                shutil.rmtree(Path(UPLOAD_DIR) / doc.id, ignore_errors=True)
                db.delete(doc)
        db.commit()

        pdf_path = BACKEND / "sample_data" / "synthetic_rhp.pdf"
        if not pdf_path.exists():
            sys.path.insert(0, str(BACKEND / "tools"))
            from make_synthetic_rhp import build  # type: ignore
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            build(str(pdf_path))

        doc = Document(filename="Acme Precision Industries RHP (sample).pdf",
                       stored_path="", doc_type="RHP")
        db.add(doc)
        db.flush()
        dest_dir = Path(UPLOAD_DIR) / doc.id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "document.pdf"
        shutil.copyfile(pdf_path, dest)
        doc.stored_path = str(dest)
        doc.file_size = dest.stat().st_size

        analysis = Analysis(document_id=doc.id, status="queued", is_demo=True)
        db.add(analysis)
        db.commit()
        analysis_id = analysis.id
    finally:
        db.close()

    print(f"Running pipeline on demo document (analysis {analysis_id})...")
    run_analysis(analysis_id)

    db = SessionLocal()
    try:
        a = db.get(Analysis, analysis_id)
        print(f"Demo analysis {a.id}: status={a.status} confidence={a.confidence} "
              f"company={a.company_name} error={a.error}")
        return a.id
    finally:
        db.close()


if __name__ == "__main__":
    seed(force="--force" in sys.argv)
