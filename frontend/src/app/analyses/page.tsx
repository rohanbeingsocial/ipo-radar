"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, type AnalysisSummary } from "@/lib/api";
import { Badge, Card, Progress, Skeleton, scoreTone } from "@/components/ui";

export default function AnalysesPage() {
  const [rows, setRows] = useState<AnalysisSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api.listAnalyses()
        .then((r) => { if (alive) { setRows(r); setError(null); } })
        .catch((e) => { if (alive) setError(e instanceof Error ? e.message : "Failed to load"); });
    void load();
    const t = setInterval(load, 4000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  if (error) return <p className="text-sm text-critical">Could not reach the backend: {error}</p>;
  if (!rows) return <div className="space-y-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-20" />)}</div>;
  if (!rows.length) {
    return (
      <p className="py-16 text-center text-ink-3">
        No analyses yet. <Link href="/" className="text-accent hover:underline">Upload an RHP</Link> to get started.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <h1 className="text-lg font-bold text-ink">Analyses</h1>
      {rows.map((row) => (
        <Link key={row.id} href={`/analysis/${row.id}`} className="block">
          <Card className="transition-colors hover:border-accent/50">
            <div className="flex items-center justify-between gap-4">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="truncate font-semibold text-ink">
                    {row.company_name ?? row.filename}
                  </span>
                  {row.is_demo ? <Badge variant="accent">sample</Badge> : null}
                  {row.status === "failed" ? <Badge variant="critical">failed</Badge> : null}
                </div>
                <div className="mt-0.5 truncate text-xs text-ink-3">
                  {row.filename} · {row.created_at ? new Date(row.created_at).toLocaleString() : ""}
                </div>
                {row.status === "processing" || row.status === "queued" ? (
                  <div className="mt-2 flex items-center gap-2">
                    <Progress value={row.progress * 100} className="max-w-56" />
                    <span className="text-xs text-ink-3">{row.stage ?? "queued"}…</span>
                  </div>
                ) : row.verdict ? (
                  <div className="mt-1 text-xs text-ink-2">{row.verdict}</div>
                ) : null}
              </div>
              {row.overall_score !== null ? (
                <div className="text-right">
                  <div className="tabular text-2xl font-bold" style={{ color: scoreTone(row.overall_score) }}>
                    {Math.round(row.overall_score)}
                  </div>
                  <div className="text-[10px] uppercase tracking-wider text-ink-3">/100 · {row.confidence}</div>
                </div>
              ) : null}
            </div>
          </Card>
        </Link>
      ))}
    </div>
  );
}
