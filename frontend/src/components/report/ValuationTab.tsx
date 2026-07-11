"use client";

import type { Report } from "@/lib/api";
import { fmtX } from "@/lib/format";
import { EmphasisBars } from "@/components/charts";
import { Badge, Card, CardTitle, PageChip } from "@/components/ui";

const CALL_TONE: Record<string, "good" | "warn" | "serious" | "critical" | "neutral"> = {
  undervalued: "good",
  fairly_valued: "good",
  fairly_valued_expensive: "warn",
  overvalued: "critical",
  indeterminate: "neutral",
};

export default function ValuationTab({ report }: { report: Report }) {
  const v = report.valuation;
  const issuerName = report.meta.company_name ?? "This issue";
  const chartData = [
    ...(v.issuer_pe != null ? [{ name: `${issuerName} (issue)`, value: v.issuer_pe, emphasized: true }] : []),
    ...v.peers.filter((p) => typeof p.pe === "number" && p.pe! > 0)
      .map((p) => ({ name: p.name, value: p.pe as number })),
  ];

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardTitle>Issue P/E vs listed peers</CardTitle>
        {chartData.length ? (
          <EmphasisBars data={chartData} format={(x) => fmtX(x)} height={Math.max(160, chartData.length * 42)} />
        ) : (
          <p className="text-sm text-ink-3">Peer P/E table could not be extracted from the Basis for Offer Price chapter.</p>
        )}
      </Card>

      <Card>
        <CardTitle>Valuation call</CardTitle>
        <div className="mb-3 flex items-center gap-2">
          <Badge variant={CALL_TONE[v.call] ?? "neutral"} className="px-2 py-1 text-sm">{v.call_label}</Badge>
          {v.relative != null ? (
            <span className="text-sm text-ink-2">
              {v.relative}x the peer-median P/E
            </span>
          ) : null}
        </div>
        <dl className="mb-4 grid grid-cols-2 gap-3">
          <div>
            <dt className="text-[11px] uppercase tracking-wider text-ink-3">Issue P/E (upper band)</dt>
            <dd className="tabular text-lg font-semibold text-ink">{fmtX(v.issuer_pe)}</dd>
          </div>
          <div>
            <dt className="text-[11px] uppercase tracking-wider text-ink-3">Peer median P/E</dt>
            <dd className="tabular text-lg font-semibold text-ink">{fmtX(v.peer_pe_median)}</dd>
          </div>
        </dl>
        <CardTitle>Reasoning</CardTitle>
        <ul className="space-y-2 text-sm leading-relaxed text-ink-2">
          {v.reasoning.map((r, i) => (
            <li key={i} className="flex gap-2">
              <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-baseline" aria-hidden />
              {r}
            </li>
          ))}
        </ul>
      </Card>

      <Card className="lg:col-span-2">
        <CardTitle>Peer comparison as disclosed in the RHP</CardTitle>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-edge text-left text-[11px] uppercase tracking-wider text-ink-3">
                <th className="py-1.5 pr-2 font-medium">Company</th>
                <th className="tabular px-2 py-1.5 text-right font-medium">EPS (₹)</th>
                <th className="tabular px-2 py-1.5 text-right font-medium">P/E</th>
                <th className="tabular px-2 py-1.5 text-right font-medium">RoNW (%)</th>
                <th className="px-2 py-1.5 text-right font-medium">Source</th>
              </tr>
            </thead>
            <tbody>
              {v.peers.map((p) => (
                <tr key={p.name} className="border-b border-edge last:border-0">
                  <td className="py-1.5 pr-2 text-ink-2">{p.name}</td>
                  <td className="tabular px-2 py-1.5 text-right text-ink">{p.eps ?? "—"}</td>
                  <td className="tabular px-2 py-1.5 text-right text-ink">{p.pe ?? "—"}</td>
                  <td className="tabular px-2 py-1.5 text-right text-ink">{p.ronw ?? "—"}</td>
                  <td className="px-2 py-1.5 text-right"><PageChip pages={[p.source_page]} /></td>
                </tr>
              ))}
              {!v.peers.length ? (
                <tr><td colSpan={5} className="py-3 text-center text-ink-3">No peer table extracted.</td></tr>
              ) : null}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-[11px] text-ink-3">
          The peer set is chosen by the issuer and may be flattering; treat the relative call as a starting point.
        </p>
      </Card>
    </div>
  );
}
