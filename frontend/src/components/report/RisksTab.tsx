"use client";

import type { Report, RiskFinding } from "@/lib/api";
import { fmtPct, titleCase } from "@/lib/format";
import { Badge, Card, CardTitle, PageChip, Progress, cn, scoreTone } from "@/components/ui";

const SEV_BADGE: Record<string, "neutral" | "warn" | "serious" | "critical"> = {
  low: "neutral",
  medium: "warn",
  high: "serious",
  critical: "critical",
};

const SEV_CELL: Record<string, string> = {
  none: "border-edge opacity-55",
  low: "border-edge bg-grid",
  medium: "border-warn/50 bg-warn/10",
  high: "border-serious/50 bg-serious/10",
  critical: "border-critical/50 bg-critical/10",
};

const SEV_RANK: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3 };

function Finding({ f }: { f: RiskFinding }) {
  return (
    <li className="border-b border-edge pb-3 last:border-0 last:pb-0">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={SEV_BADGE[f.severity] ?? "neutral"}>{f.severity}</Badge>
        <Badge variant="neutral">{titleCase(f.risk_type)}</Badge>
        <span className="text-sm font-medium text-ink">
          {f.title}
          <PageChip pages={[f.source_page]} />
        </span>
      </div>
      {f.detail ? <p className="mt-1 text-sm leading-relaxed text-ink-2">{f.detail}</p> : null}
      {f.evidence_text ? (
        <p className="mt-1 border-l-2 border-edge pl-2 text-xs italic leading-relaxed text-ink-3">
          “{f.evidence_text}”
        </p>
      ) : null}
    </li>
  );
}

export default function RisksTab({ report }: { report: Report }) {
  const { risk, forensic } = report;
  const findings = [...risk.findings].sort(
    (a, b) => (SEV_RANK[a.severity] ?? 9) - (SEV_RANK[b.severity] ?? 9));
  const bp = risk.boilerplate;

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card>
        <CardTitle>Risk score</CardTitle>
        <div className="flex items-baseline gap-2">
          <span className="tabular text-4xl font-bold" style={{ color: scoreTone(risk.score) }}>
            {Math.round(risk.score)}
          </span>
          <span className="text-sm text-ink-3">/100 · higher = fewer material risks</span>
        </div>
        <p className="mt-2 text-xs leading-relaxed text-ink-3">
          Starts at 100 and deducts per finding by severity (critical −18, high −10, medium −4, low −1).
        </p>
        {bp.specificity_ratio != null ? (
          <div className="mt-4">
            <div className="mb-1 flex items-baseline justify-between text-[11px] uppercase tracking-wider text-ink-3">
              <span>Risk-factor specificity</span>
              <span className="tabular">{fmtPct(bp.specificity_ratio, 0)}</span>
            </div>
            <Progress value={bp.specificity_ratio * 100}
              tone={bp.specificity_ratio < 0.35 ? "var(--status-serious)" : "var(--accent)"} />
            <p className="mt-1.5 text-xs leading-relaxed text-ink-3">
              {bp.specific} of {bp.total_factors} risk paragraphs carry a number or amount.
              {bp.specificity_ratio < 0.35 ? " Mostly generic boilerplate — the disclosed risks say little." : ""}
            </p>
          </div>
        ) : null}
      </Card>

      <Card className="lg:col-span-2">
        <CardTitle>Risk heatmap — {risk.heatmap.length} classes screened</CardTitle>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4">
          {risk.heatmap.map((cell) => (
            <div key={cell.risk_type}
              className={cn("rounded border p-2", SEV_CELL[cell.severity] ?? "border-edge")}>
              <div className="text-xs font-medium leading-tight text-ink">{titleCase(cell.risk_type)}</div>
              <div className="mt-1 flex items-center justify-between">
                <span className={cn("text-[11px] font-semibold uppercase tracking-wide",
                  cell.severity === "none" ? "text-ink-3" : "text-ink-2")}>
                  {cell.severity === "none" ? "clear" : cell.severity}
                </span>
                {cell.count > 0 ? (
                  <span className="tabular text-[11px] text-ink-3">{cell.count} finding{cell.count > 1 ? "s" : ""}</span>
                ) : null}
              </div>
            </div>
          ))}
        </div>
        <p className="mt-2 text-[11px] text-ink-3">
          Severity text is authoritative; color is only reinforcement. “Clear” means no matching disclosure
          was detected — not that the risk cannot exist.
        </p>
      </Card>

      <Card className="lg:col-span-2">
        <CardTitle>Findings ({findings.length})</CardTitle>
        <ul className="space-y-3">
          {findings.map((f, i) => <Finding key={i} f={f} />)}
          {!findings.length ? <li className="text-sm text-ink-3">No material risk findings detected.</li> : null}
        </ul>
      </Card>

      <Card>
        <CardTitle>Earnings-quality screens</CardTitle>
        {forensic.cap_triggered ? (
          <p className="mb-3 rounded border border-serious/50 bg-serious/10 p-2 text-xs leading-relaxed text-ink">
            A high-severity forensic flag fired — the overall score is capped at 55 regardless of other strengths.
          </p>
        ) : null}

        {forensic.flags.length ? (
          <ul className="mb-4 space-y-2">
            {forensic.flags.map((f, i) => (
              <li key={i} className="text-sm leading-relaxed text-ink-2">
                <Badge variant={SEV_BADGE[f.severity] ?? "warn"} className="mr-1.5">{f.severity}</Badge>
                {f.detail}
                <PageChip pages={[f.source_page]} />
              </li>
            ))}
          </ul>
        ) : (
          <p className="mb-4 text-sm text-ink-3">No Beneish-style manipulation flags fired.</p>
        )}

        <CardTitle>
          Strength checks — {forensic.strength_score.passed}/{forensic.strength_score.total} passed
        </CardTitle>
        <ul className="space-y-1.5">
          {forensic.checks.map((c) => (
            <li key={c.check} className="flex items-center justify-between gap-2 text-sm">
              <span className="flex items-center gap-1.5 text-ink-2">
                <span aria-hidden className={c.passed ? "text-good" : "text-critical"}>
                  {c.passed ? "✓" : "✗"}
                </span>
                {titleCase(c.check)}
              </span>
              <span className="tabular text-xs text-ink-3">{c.value}</span>
            </li>
          ))}
          {!forensic.checks.length ? (
            <li className="text-sm text-ink-3">Not enough financial history to run the checks.</li>
          ) : null}
        </ul>

        {forensic.consistency?.length ? (
          <>
            <CardTitle className="mt-4">Narrative consistency</CardTitle>
            <ul className="space-y-1.5">
              {forensic.consistency.map((c, i) => (
                <li key={i} className="text-sm leading-relaxed text-ink-2">
                  <Badge variant={SEV_BADGE[c.severity] ?? "warn"} className="mr-1.5">{c.severity}</Badge>
                  {c.detail}
                </li>
              ))}
            </ul>
          </>
        ) : null}
      </Card>
    </div>
  );
}
