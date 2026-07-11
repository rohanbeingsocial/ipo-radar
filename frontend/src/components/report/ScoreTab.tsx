"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { Report, RuleTrace } from "@/lib/api";
import { CATEGORY_LABELS, fmtPct, titleCase } from "@/lib/format";
import { Badge, Card, CardTitle, PageChip, Progress, cn, scoreTone } from "@/components/ui";

function fmtVal(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "number") {
    return v.toLocaleString("en-IN", { maximumFractionDigits: Math.abs(v) >= 100 ? 0 : 2 });
  }
  return String(v);
}

function RuleRow({ r }: { r: RuleTrace }) {
  return (
    <div className={cn("rounded border border-edge p-3", !r.included && "opacity-60")}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-medium text-ink">{titleCase(r.rule)}</span>
        {r.included ? (
          <span className="tabular text-sm font-semibold text-ink">
            {r.points}<span className="text-ink-3">/{r.max_points} pts</span>
          </span>
        ) : (
          <Badge variant="neutral">not scored — data missing</Badge>
        )}
      </div>
      {r.evidence ? (
        <p className="mt-1.5 text-sm leading-relaxed text-ink-2">
          {r.evidence}
          <PageChip pages={r.source_pages} />
        </p>
      ) : null}
      <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-ink-3">
        <span>value: <span className="tabular text-ink-2">{fmtVal(r.value)}</span></span>
        {r.thresholds ? <span>bands: <span className="text-ink-2">{r.thresholds}</span></span> : null}
        <span>confidence: <span className="tabular text-ink-2">{Math.round(r.confidence * 100)}%</span></span>
      </div>
      {r.rationale ? (
        <p className="mt-1.5 text-xs leading-relaxed text-ink-3">Why this rule: {r.rationale}</p>
      ) : null}
    </div>
  );
}

export default function ScoreTab({ report }: { report: Report }) {
  const s = report.scoring;
  const [open, setOpen] = useState<Set<string>>(new Set([Object.keys(s.categories)[0]].filter(Boolean)));
  const toggle = (k: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k); else next.add(k);
      return next;
    });

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-center gap-x-8 gap-y-3">
          <div>
            <div className="text-[11px] uppercase tracking-wider text-ink-3">Overall score</div>
            <div className="tabular text-4xl font-bold" style={{ color: scoreTone(s.overall) }}>
              {Math.round(s.overall)}<span className="text-lg text-ink-3">/100</span>
            </div>
          </div>
          <div>
            <div className="text-[11px] uppercase tracking-wider text-ink-3">Weight lens</div>
            <div className="text-sm font-medium text-ink">{titleCase(s.lens)}</div>
          </div>
          <div className="min-w-0 flex-1 text-xs leading-relaxed text-ink-3">
            Every point below traces to a rule, the extracted value it was applied to, the scoring bands,
            and the page the value was read from. Rules with missing inputs are excluded rather than
            silently scored zero — coverage feeds the confidence level, not the score.
          </div>
        </div>
        {s.cap_note ? (
          <p className="mt-3 rounded border border-serious/50 bg-serious/10 p-2 text-xs leading-relaxed text-ink">
            {s.cap_note}
          </p>
        ) : null}
      </Card>

      {Object.entries(s.categories).map(([key, cat]) => {
        const isOpen = open.has(key);
        const included = cat.rules.filter((r) => r.included);
        return (
          <Card key={key} className="p-0">
            <button onClick={() => toggle(key)} aria-expanded={isOpen}
              className="flex w-full items-center gap-3 p-4 text-left">
              {isOpen ? <ChevronDown className="h-4 w-4 shrink-0 text-ink-3" aria-hidden />
                : <ChevronRight className="h-4 w-4 shrink-0 text-ink-3" aria-hidden />}
              <span className="w-44 shrink-0 text-sm font-semibold text-ink">
                {CATEGORY_LABELS[key] ?? titleCase(key)}
              </span>
              <Progress value={cat.score} tone={scoreTone(cat.score)} className="max-w-64" />
              <span className="tabular w-14 shrink-0 text-right text-sm font-semibold text-ink">
                {Math.round(cat.score)}
              </span>
              <span className="tabular hidden shrink-0 text-xs text-ink-3 sm:inline">
                weight {fmtPct(s.weights[key], 0)} · {included.length}/{cat.rules.length} rules · coverage {fmtPct(cat.coverage, 0)}
              </span>
            </button>
            {isOpen ? (
              <div className="space-y-2 border-t border-edge p-4">
                {cat.rules.map((r) => <RuleRow key={r.rule} r={r} />)}
                {!cat.rules.length ? (
                  <p className="text-sm text-ink-3">No rules defined for this category.</p>
                ) : null}
              </div>
            ) : null}
          </Card>
        );
      })}
    </div>
  );
}
