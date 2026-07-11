"use client";

import type { Report } from "@/lib/api";
import { fmtCr, fmtPct, titleCase } from "@/lib/format";
import { GroupedBars, TrendLines } from "@/components/charts";
import { Card, CardTitle, PageChip } from "@/components/ui";

const TABLE_METRICS = ["revenue", "ebitda", "pat", "net_worth", "total_debt", "cfo", "capex",
  "current_assets", "current_liabilities", "receivables", "total_assets"] as const;

export default function FinancialsTab({ report }: { report: Report }) {
  const series = report.financials.series; // oldest → latest
  const ratios = report.financials.ratios;
  const marginData = report.financials.margin_series
    .slice()
    .reverse()
    .map((m) => ({
      fy: m.fy,
      net: m.net_margin != null ? +(m.net_margin * 100).toFixed(1) : null,
      operating: m.operating_margin != null ? +(m.operating_margin * 100).toFixed(1) : null,
    }));

  if (!series.length) {
    return <Card><p className="text-sm text-ink-3">
      Financial statements could not be extracted from this document. This usually means the statement
      tables are scanned images (enable OCR) or use an unusual layout.
    </p></Card>;
  }

  const fmt = (v: number) => fmtCr(v);
  const ratioRows: [string, string][] = [
    ["Revenue CAGR", fmtPct(ratios.revenue_cagr)],
    ["PAT CAGR", fmtPct(ratios.pat_cagr)],
    ["Operating margin", fmtPct(ratios.operating_margin)],
    ["Net margin", fmtPct(ratios.net_margin)],
    ["ROE", fmtPct(ratios.roe)],
    ["ROCE", fmtPct(ratios.roce)],
    ["Debt / Equity", ratios.debt_equity != null ? ratios.debt_equity.toFixed(2) : "n/a"],
    ["Current ratio", ratios.current_ratio != null ? ratios.current_ratio.toFixed(2) : "n/a"],
    ["Interest cover", ratios.interest_cover != null ? `${ratios.interest_cover.toFixed(1)}x` : "n/a"],
    ["CFO / PAT", ratios.cfo_to_pat != null ? ratios.cfo_to_pat.toFixed(2) : "n/a"],
  ];

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardTitle>Revenue & profit (₹ cr)</CardTitle>
        <GroupedBars data={series} xKey="fy" format={fmt} series={[
          { key: "revenue", label: "Revenue", color: "var(--series-1)" },
          { key: "pat", label: "Profit after tax", color: "var(--series-2)" },
        ]} />
      </Card>

      <Card>
        <CardTitle>Margins (%)</CardTitle>
        <TrendLines data={marginData} xKey="fy" format={(v) => `${v}%`} series={[
          { key: "operating", label: "Operating margin", color: "var(--series-1)" },
          { key: "net", label: "Net margin", color: "var(--series-2)" },
        ]} />
      </Card>

      <Card>
        <CardTitle>Leverage (₹ cr)</CardTitle>
        <GroupedBars data={series} xKey="fy" format={fmt} series={[
          { key: "net_worth", label: "Net worth", color: "var(--series-1)" },
          { key: "total_debt", label: "Total borrowings", color: "var(--series-6)" },
        ]} />
      </Card>

      <Card>
        <CardTitle>Cash conversion (₹ cr)</CardTitle>
        <GroupedBars data={series} xKey="fy" format={fmt} series={[
          { key: "pat", label: "Profit after tax", color: "var(--series-2)" },
          { key: "cfo", label: "Operating cash flow", color: "var(--series-1)" },
        ]} />
      </Card>

      <Card>
        <CardTitle>Key ratios (latest restated year)</CardTitle>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-2">
          {ratioRows.map(([label, value]) => (
            <div key={label} className="flex items-baseline justify-between border-b border-edge pb-1.5">
              <dt className="text-xs text-ink-3">{label}</dt>
              <dd className="tabular text-sm font-medium text-ink">{value}</dd>
            </div>
          ))}
        </dl>
      </Card>

      <Card>
        <CardTitle>Restated statements — extracted values</CardTitle>
        <p className="mb-2 text-[11px] text-ink-3">{report.financials.unit_note} Page chips show where each metric was read.</p>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-edge text-left text-[11px] uppercase tracking-wider text-ink-3">
                <th className="py-1.5 pr-2 font-medium">Metric</th>
                {series.map((row) => (
                  <th key={String(row.fy)} className="tabular px-2 py-1.5 text-right font-medium">{String(row.fy)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {TABLE_METRICS.filter((m) => series.some((row) => row[m] != null)).map((m) => (
                <tr key={m} className="border-b border-edge last:border-0">
                  <td className="py-1.5 pr-2 text-ink-2">
                    {titleCase(m)}
                    <PageChip pages={[report.financials.source_pages[m]]} />
                  </td>
                  {series.map((row) => (
                    <td key={String(row.fy)} className="tabular px-2 py-1.5 text-right text-ink">
                      {row[m] != null ? Number(row[m]).toLocaleString("en-IN", { maximumFractionDigits: 0 }) : "—"}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
