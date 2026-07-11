import uuid
from datetime import datetime

from sqlalchemy import (
    JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    filename: Mapped[str] = mapped_column(String(512))
    stored_path: Mapped[str] = mapped_column(Text)
    doc_type: Mapped[str] = mapped_column(String(16), default="RHP")
    page_count: Mapped[int | None] = mapped_column(Integer)
    file_size: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    analyses: Mapped[list["Analysis"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    company_name: Mapped[str | None] = mapped_column(String(256))
    sector: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # queued|processing|completed|failed
    stage: Mapped[str | None] = mapped_column(String(64))
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[str | None] = mapped_column(String(8))  # high|medium|low
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    document: Mapped[Document] = relationship(back_populates="analyses")
    sections: Mapped[list["Section"]] = relationship(cascade="all, delete-orphan")
    financials: Mapped[list["Financial"]] = relationship(cascade="all, delete-orphan")
    scores: Mapped[list["Score"]] = relationship(cascade="all, delete-orphan")
    risk_findings: Mapped[list["RiskFinding"]] = relationship(cascade="all, delete-orphan")
    report: Mapped["Report | None"] = relationship(back_populates="analysis", cascade="all, delete-orphan", uselist=False)


class Section(Base):
    __tablename__ = "sections"
    __table_args__ = (UniqueConstraint("analysis_id", "key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(String(512))
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    char_count: Mapped[int | None] = mapped_column(Integer)
    text_excerpt: Mapped[str | None] = mapped_column(Text)
    full_text_path: Mapped[str | None] = mapped_column(Text)
    found: Mapped[bool] = mapped_column(Boolean, default=True)
    method: Mapped[str | None] = mapped_column(String(24))  # toc|printed_toc|heading_scan|fallback


class Financial(Base):
    __tablename__ = "financials"
    __table_args__ = (UniqueConstraint("analysis_id", "fiscal_label", "metric"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), index=True)
    fiscal_label: Mapped[str] = mapped_column(String(16))
    fiscal_rank: Mapped[int] = mapped_column(Integer)  # 0 = latest
    metric: Mapped[str] = mapped_column(String(48), index=True)
    value: Mapped[float | None] = mapped_column(Float)  # normalized to ₹ crore
    unit_source: Mapped[str | None] = mapped_column(String(16))
    source_page: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float | None] = mapped_column(Float)


class IssueDetails(Base):
    __tablename__ = "issue_details"

    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), primary_key=True)
    price_band_low: Mapped[float | None] = mapped_column(Float)
    price_band_high: Mapped[float | None] = mapped_column(Float)
    face_value: Mapped[float | None] = mapped_column(Float)
    fresh_issue_cr: Mapped[float | None] = mapped_column(Float)
    ofs_cr: Mapped[float | None] = mapped_column(Float)
    total_issue_cr: Mapped[float | None] = mapped_column(Float)
    lot_size: Mapped[int | None] = mapped_column(Integer)
    listing_at: Mapped[str | None] = mapped_column(String(64))
    pre_issue_promoter_pct: Mapped[float | None] = mapped_column(Float)
    post_issue_promoter_pct: Mapped[float | None] = mapped_column(Float)
    objects_json: Mapped[list | None] = mapped_column(JSON)
    peers_json: Mapped[list | None] = mapped_column(JSON)
    shareholding_json: Mapped[dict | None] = mapped_column(JSON)
    dividend_json: Mapped[dict | None] = mapped_column(JSON)
    source_pages: Mapped[dict | None] = mapped_column(JSON)


class Score(Base):
    __tablename__ = "scores"
    __table_args__ = (UniqueConstraint("analysis_id", "category"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(32))
    score: Mapped[float] = mapped_column(Float)
    weight: Mapped[float] = mapped_column(Float)
    rules_json: Mapped[list] = mapped_column(JSON)


class RiskFinding(Base):
    __tablename__ = "risk_findings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), index=True)
    risk_type: Mapped[str] = mapped_column(String(48))
    severity: Mapped[str] = mapped_column(String(12))  # low|medium|high|critical
    likelihood: Mapped[str | None] = mapped_column(String(12))
    title: Mapped[str] = mapped_column(String(512))
    detail: Mapped[str | None] = mapped_column(Text)
    evidence_text: Mapped[str | None] = mapped_column(Text)
    source_page: Mapped[int | None] = mapped_column(Integer)
    quantified: Mapped[dict | None] = mapped_column(JSON)


class Report(Base):
    __tablename__ = "reports"

    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), primary_key=True)
    overall_score: Mapped[float | None] = mapped_column(Float)
    verdict: Mapped[str | None] = mapped_column(String(256))
    valuation_call: Mapped[str | None] = mapped_column(String(24))
    confidence: Mapped[str | None] = mapped_column(String(8))
    report_json: Mapped[dict] = mapped_column(JSON)
    llm_enhanced: Mapped[bool] = mapped_column(Boolean, default=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    analysis: Mapped[Analysis] = relationship(back_populates="report")


class MarketSignal(Base):
    __tablename__ = "market_signals"

    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), primary_key=True)
    gmp: Mapped[float | None] = mapped_column(Float)
    sub_qib: Mapped[float | None] = mapped_column(Float)
    sub_nii: Mapped[float | None] = mapped_column(Float)
    sub_rii: Mapped[float | None] = mapped_column(Float)
    sub_bnii: Mapped[float | None] = mapped_column(Float)   # big NII (> ₹10L bids)
    sub_snii: Mapped[float | None] = mapped_column(Float)   # small NII
    day1_gain: Mapped[float | None] = mapped_column(Float)  # listing-day close vs offer, %
    noted_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)
    source: Mapped[str] = mapped_column(String(24), default="user_input")
