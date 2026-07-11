"use client";

import type { Report, RuleTrace } from "@/lib/api";
import { titleCase } from "@/lib/format";
import { Badge, Card, CardTitle, PageChip, Stat } from "@/components/ui";

function RuleLine({ r }: { r: RuleTrace }) {
  if (!r.included) return null;
  return (
    <li className="flex items-start justify-between gap-3 border-b border-edge pb-2 text-sm last:border-0 last:pb-0">
      <span className="leading-relaxed text-ink-2">
        <span className="font-medium text-ink">{titleCase(r.rule)}.</span>{" "}
        {r.evidence}
        <PageChip pages={r.source_pages} />
      </span>
      <span className="tabular shrink-0 text-xs font-semibold text-ink">
        {r.points}/{r.max_points}
      </span>
    </li>
  );
}

export default function PromotersTab({ report }: { report: Report }) {
  const p = report.promoter;
  const pledged = p.pledging?.pledged === true;
  const dilution = p.pre_issue_pct != null && p.post_issue_pct != null
    ? +(p.pre_issue_pct - p.post_issue_pct).toFixed(2) : null;
  const promoterRules = report.scoring.categories.promoter_quality?.rules ?? [];
  const governanceRules = report.scoring.categories.governance?.rules ?? [];

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardTitle>
          Promoters
          <PageChip pages={[p.source_pages?.promoters]} />
        </CardTitle>
        {p.names.length ? (
          <ul className="mb-3 flex flex-wrap gap-2">
            {p.names.map((n) => (
              <li key={n} className="rounded border border-edge px-2 py-1 text-sm font-medium text-ink">{n}</li>
            ))}
          </ul>
        ) : (
          <p className="mb-3 text-sm text-ink-3">
            Promoter names could not be isolated from the narrative — check the “Our Promoters” chapter directly.
          </p>
        )}
        {p.experience_claims.length ? (
          <p className="text-sm leading-relaxed text-ink-2">
            Experience claimed in the document:{" "}
            {p.experience_claims.map((y) => `${y} yrs`).join(", ")} (as stated by the issuer, unverified).
          </p>
        ) : null}
        <div className="mt-3 flex flex-wrap gap-2">
          {p.group_company_conflicts ? (
            <Badge variant="serious">group-company conflict language present</Badge>
          ) : (
            <Badge variant="neutral">no conflict-of-interest language detected</Badge>
          )}
          {p.past_ventures_mentioned ? <Badge variant="warn">past ventures / disassociations mentioned</Badge> : null}
        </div>
      </Card>

      <Card>
        <CardTitle>Skin in the game</CardTitle>
        <div className="grid grid-cols-3 gap-3">
          <Stat label="Pre-issue holding" value={p.pre_issue_pct != null ? `${p.pre_issue_pct}%` : "n/a"} />
          <Stat label="Post-issue holding" value={p.post_issue_pct != null ? `${p.post_issue_pct}%` : "n/a"} />
          <Stat label="Dilution" value={dilution != null ? `−${dilution} pp` : "n/a"} />
        </div>
        <div className="mt-4">
          <div className="mb-1 text-[11px] uppercase tracking-wider text-ink-3">Share pledging</div>
          {pledged ? (
            <>
              <Badge variant="critical">shares pledged</Badge>
              {p.pledging?.evidence ? (
                <p className="mt-2 border-l-2 border-edge pl-2 text-xs italic leading-relaxed text-ink-3">
                  “{p.pledging.evidence}”
                </p>
              ) : null}
            </>
          ) : (
            <Badge variant="good">no pledge disclosed</Badge>
          )}
        </div>
        <div className="mt-4">
          <div className="mb-1 text-[11px] uppercase tracking-wider text-ink-3">
            Board composition mentions
            <PageChip pages={[p.source_pages?.management]} />
          </div>
          <p className="text-sm leading-relaxed text-ink-2">
            “Independent director” appears {p.board?.independent_director_mentions ?? 0} time(s),
            “woman director” {p.board?.woman_director_mentions ?? 0} time(s) in the management chapter —
            a proxy for emphasis, not a legal count.
          </p>
        </div>
      </Card>

      <Card>
        <CardTitle>
          Promoter quality — scored rules
          ({Math.round(report.scoring.categories.promoter_quality?.score ?? 0)}/100)
        </CardTitle>
        <ul className="space-y-2">
          {promoterRules.map((r) => <RuleLine key={r.rule} r={r} />)}
          {!promoterRules.some((r) => r.included) ? (
            <li className="text-sm text-ink-3">No promoter rules could be evaluated from this document.</li>
          ) : null}
        </ul>
      </Card>

      <Card>
        <CardTitle>
          Governance — scored rules
          ({Math.round(report.scoring.categories.governance?.score ?? 0)}/100)
        </CardTitle>
        <ul className="space-y-2">
          {governanceRules.map((r) => <RuleLine key={r.rule} r={r} />)}
          {!governanceRules.some((r) => r.included) ? (
            <li className="text-sm text-ink-3">No governance rules could be evaluated from this document.</li>
          ) : null}
        </ul>
      </Card>
    </div>
  );
}
