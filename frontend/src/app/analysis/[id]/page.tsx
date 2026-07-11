"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api, type AnalysisSummary, type Report } from "@/lib/api";
import { Badge, Card, Progress, Skeleton, scoreTone } from "@/components/ui";
import OverviewTab from "@/components/report/OverviewTab";
import FinancialsTab from "@/components/report/FinancialsTab";
import ValuationTab from "@/components/report/ValuationTab";
import RisksTab from "@/components/report/RisksTab";
import PromotersTab from "@/components/report/PromotersTab";
import ScoreTab from "@/components/report/ScoreTab";
import QATab from "@/components/report/QATab";

const TABS = ["Overview", "Financials", "Valuation", "Risks", "Promoters", "Score", "Q&A"] as const;
type Tab = (typeof TABS)[number];

const STAGE_LABELS: Record<string, string> = {
  document_processing: "Reading the PDF",
  section_extraction: "Locating ICDR sections",
  financial_extraction: "Parsing restated financials",
  entity_extraction: "Extracting issue details, peers & objects",
  risk_analysis: "Analyzing risk factors",
  valuation: "Computing ratios & peer comparison",
  forensic: "Running earnings-quality screens",
  promoter_analysis: "Analyzing promoters & governance",
  scoring: "Scoring",
  report: "Assembling the report",
};

export default function AnalysisPage() {
  const { id } = useParams<{ id: string }>();
  const [status, setStatus] = useState<(AnalysisSummary & { error: string | null }) | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [tab, setTab] = useState<Tab>("Overview");
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const tick = async () => {
      try {
        const s = await api.getAnalysis(id);
        if (!alive) return;
        setStatus(s);
        setLoadError(null);
        if (s.status === "completed") {
          const r = await api.getReport(id);
          if (alive) setReport(r);
          return; // stop polling
        }
        if (s.status !== "failed") timer = setTimeout(tick, 2000);
      } catch (e) {
        if (alive) {
          setLoadError(e instanceof Error ? e.message : "Failed to load analysis");
          timer = setTimeout(tick, 4000);
        }
      }
    };
    void tick();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [id]);

  if (loadError && !status) return <p className="text-sm text-critical">Could not reach the backend: {loadError}</p>;
  if (!status) return <div className="space-y-3"><Skeleton className="h-28" /><Skeleton className="h-64" /></div>;

  if (status.status === "failed") {
    return (
      <Card className="mx-auto max-w-xl text-center">
        <div className="text-lg font-semibold text-critical">Analysis failed</div>
        <p className="mt-2 text-sm text-ink-2">{status.error ?? "Unknown error"}</p>
        <p className="mt-2 text-xs text-ink-3">
          Scanned/image-only PDFs need OCR enabled on the backend (ENABLE_OCR=1 + tesseract).
        </p>
      </Card>
    );
  }

  if (status.status !== "completed" || !report) {
    const stage = status.stage ?? "queued";
    return (
      <Card className="mx-auto max-w-xl">
        <div className="font-semibold text-ink">Analyzing: {status.filename}</div>
        <div className="mt-3 flex items-center gap-3">
          <Progress value={status.progress * 100} />
          <span className="tabular text-sm text-ink-2">{Math.round(status.progress * 100)}%</span>
        </div>
        <div className="mt-2 text-sm text-ink-3">
          ⟳ {STAGE_LABELS[stage] ?? stage}…
        </div>
      </Card>
    );
  }

  const overall = report.scoring.overall;
  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-x-6 gap-y-3">
        <div className="flex items-center gap-4">
          <div className="flex h-16 w-16 flex-col items-center justify-center rounded-lg border-2"
            style={{ borderColor: scoreTone(overall) }}>
            <span className="tabular text-2xl font-bold text-ink">{Math.round(overall)}</span>
            <span className="text-[9px] uppercase tracking-wider text-ink-3">/100</span>
          </div>
          <div>
            <h1 className="text-xl font-bold text-ink">
              {report.meta.company_name ?? status.filename}
            </h1>
            <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-ink-3">
              <span>{report.meta.doc_type} · {report.meta.page_count} pages</span>
              {status.is_demo ? <Badge variant="accent">sample document</Badge> : null}
              <Badge variant={report.meta.confidence === "high" ? "good" : report.meta.confidence === "medium" ? "warn" : "serious"}>
                confidence: {report.meta.confidence}
              </Badge>
              {report.meta.llm_enhanced ? <Badge variant="accent">AI-enhanced narrative</Badge> : null}
            </div>
            <div className="mt-1 text-sm font-medium text-ink-2">{report.verdict}</div>
          </div>
        </div>
      </div>

      <nav className="mb-5 flex gap-1 overflow-x-auto border-b border-edge" role="tablist">
        {TABS.map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
            className={`whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium transition-colors
              ${tab === t ? "border-accent text-ink" : "border-transparent text-ink-3 hover:text-ink-2"}`}>
            {t}
          </button>
        ))}
      </nav>

      {tab === "Overview" && <OverviewTab report={report} analysisId={id} />}
      {tab === "Financials" && <FinancialsTab report={report} />}
      {tab === "Valuation" && <ValuationTab report={report} />}
      {tab === "Risks" && <RisksTab report={report} />}
      {tab === "Promoters" && <PromotersTab report={report} />}
      {tab === "Score" && <ScoreTab report={report} />}
      {tab === "Q&A" && <QATab analysisId={id} />}

      <p className="mt-8 rounded border border-edge bg-surface p-3 text-[11px] leading-relaxed text-ink-3">
        {report.meta.disclaimer}
      </p>
    </div>
  );
}
