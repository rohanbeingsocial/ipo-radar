"use client";

import { useState } from "react";
import { api, type CaseItem, type ListingForecast, type Report } from "@/lib/api";
import { fmtCr } from "@/lib/format";
import { CATEGORY_LABELS } from "@/lib/format";
import { ScoreBars, ShareBar } from "@/components/charts";
import { Badge, Card, CardTitle, PageChip, Stat } from "@/components/ui";

function CaseList({ items, tone }: { items: CaseItem[]; tone: "good" | "critical" | "neutral" }) {
  const dot = tone === "good" ? "var(--status-good)" : tone === "critical" ? "var(--status-critical)" : "var(--text-muted)";
  return (
    <ul className="space-y-2">
      {items.map((c, i) => (
        <li key={i} className="flex gap-2 text-sm leading-relaxed text-ink-2">
          <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full" style={{ background: dot }} aria-hidden />
          <span>
            {c.text}
            <PageChip pages={c.source_pages} />
          </span>
        </li>
      ))}
      {!items.length ? <li className="text-sm text-ink-3">Nothing material identified.</li> : null}
    </ul>
  );
}

export default function OverviewTab({ report, analysisId }: { report: Report; analysisId: string }) {
  const snap = report.snapshot;
  const band = snap.price_band as (number | null)[];
  const fresh = snap.fresh_issue_cr as number | null;
  const ofs = snap.ofs_cr as number | null;

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card className="lg:col-span-2">
        <CardTitle>Executive summary</CardTitle>
        <div className="space-y-3">
          {report.executive_summary.map((p, i) => (
            <p key={i} className="text-sm leading-relaxed text-ink-2">
              {p.text}
              <PageChip pages={p.source_pages} />
            </p>
          ))}
        </div>
      </Card>

      <Card>
        <CardTitle>IPO snapshot</CardTitle>
        <div className="grid grid-cols-2 gap-x-4 gap-y-3">
          <Stat label="Price band"
            value={band?.[0] && band?.[1] ? `₹${band[0]}–${band[1]}` : "n/a"} />
          <Stat label="Lot size" value={(snap.lot_size as number | null) ?? "n/a"} />
          <Stat label="Total issue" value={fmtCr(snap.total_issue_cr as number | null)} />
          <Stat label="Listing at" value={(snap.listing_at as string | null) ?? "n/a"} />
          <Stat label="Promoter (pre)" value={snap.pre_issue_promoter_pct != null ? `${snap.pre_issue_promoter_pct}%` : "n/a"} />
          <Stat label="Promoter (post)" value={snap.post_issue_promoter_pct != null ? `${snap.post_issue_promoter_pct}%` : "n/a"} />
        </div>
        {fresh != null || ofs != null ? (
          <div className="mt-4">
            <div className="mb-1 text-[11px] uppercase tracking-wider text-ink-3">Fresh issue vs offer for sale</div>
            <ShareBar format={(v) => fmtCr(v)} parts={[
              { label: "Fresh issue (to company)", value: fresh ?? 0, color: "var(--series-1)" },
              { label: "OFS (to sellers)", value: ofs ?? 0, color: "var(--series-3)" },
            ]} />
          </div>
        ) : null}
      </Card>

      <Card>
        <CardTitle>Green flags</CardTitle>
        <CaseList items={report.flags.green.slice(0, 6)} tone="good" />
      </Card>

      <Card>
        <CardTitle>Red flags</CardTitle>
        <CaseList items={report.flags.red.slice(0, 6)} tone="critical" />
      </Card>

      <Card>
        <CardTitle>Score breakdown</CardTitle>
        <ScoreBars data={Object.entries(report.scoring.categories).map(([k, v]) => ({
          name: CATEGORY_LABELS[k] ?? k, value: Math.round(v.score),
        }))} />
      </Card>

      <Card className="lg:col-span-2">
        <div className="grid gap-6 md:grid-cols-3">
          <div>
            <CardTitle>Bull case</CardTitle>
            <CaseList items={report.cases.bull.slice(0, 5)} tone="good" />
          </div>
          <div>
            <CardTitle>Bear case</CardTitle>
            <CaseList items={report.cases.bear.slice(0, 5)} tone="critical" />
          </div>
          <div>
            <CardTitle>Unknowns / neutral</CardTitle>
            <CaseList items={report.cases.neutral.slice(0, 5)} tone="neutral" />
          </div>
        </div>
      </Card>

      <MarketSignalsCard report={report} analysisId={analysisId} />

      <Card className="lg:col-span-2">
        <CardTitle>Questions investors should ask</CardTitle>
        <ol className="list-decimal space-y-1.5 pl-5 text-sm leading-relaxed text-ink-2">
          {report.questions.map((q, i) => <li key={i}>{q}</li>)}
        </ol>
      </Card>

      <Card>
        <CardTitle>Use of proceeds</CardTitle>
        <ul className="space-y-2 text-sm text-ink-2">
          {(snap.objects ?? []).map((o, i) => (
            <li key={i} className="flex items-start justify-between gap-2">
              <span className="flex items-center gap-1.5">
                <Badge variant={o.category === "debt_repayment" || o.category === "general_corporate" ? "warn" : "accent"}>
                  {o.category.replace(/_/g, " ")}
                </Badge>
              </span>
              <span className="tabular shrink-0 font-medium text-ink">{o.amount_cr != null ? fmtCr(o.amount_cr) : "—"}</span>
            </li>
          ))}
          {!(snap.objects ?? []).length ? <li className="text-ink-3">Objects table not extracted.</li> : null}
        </ul>
        {report.industry.excerpt ? (
          <>
            <CardTitle className="mt-5">Industry (as stated by the issuer)</CardTitle>
            <p className="text-xs leading-relaxed text-ink-3">
              {report.industry.excerpt.slice(0, 400)}…
              <PageChip pages={[report.industry.source_page]} />
            </p>
          </>
        ) : null}
      </Card>
    </div>
  );
}

function MarketSignalsCard({ report, analysisId }: { report: Report; analysisId: string }) {
  const ms = report.market_signals;
  const [vals, setVals] = useState<Record<string, string>>({
    gmp: ms?.gmp?.toString() ?? "",
    sub_qib: ms?.sub_qib?.toString() ?? "",
    sub_bnii: ms?.sub_bnii?.toString() ?? "",
    sub_snii: ms?.sub_snii?.toString() ?? "",
    sub_rii: ms?.sub_rii?.toString() ?? "",
    day1_gain: ms?.day1_gain?.toString() ?? "",
  });
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [forecast, setForecast] = useState<ListingForecast | null>(null);

  const fields: [string, string][] = [
    ["gmp", "GMP (₹)"],
    ["sub_qib", "QIB (x)"],
    ["sub_bnii", "bNII (x)"],
    ["sub_snii", "sNII (x)"],
    ["sub_rii", "Retail (x)"],
    ["day1_gain", "Day-1 close gain (%)"],
  ];
  const set = (k: string, v: string) => { setVals((p) => ({ ...p, [k]: v })); setSaved(false); };
  const asNum = (k: string) => (vals[k].trim() ? Number(vals[k]) : null);

  return (
    <Card className="lg:col-span-2">
      <CardTitle>Market signals &amp; forecast (optional, user-supplied)</CardTitle>
      <p className="mb-3 text-xs leading-relaxed text-ink-3">
        GMP, subscription multiples and the listing-day close live outside the prospectus and are never
        part of the fundamental score. Enter whatever you have — these are all knowable within the first
        2–3 days of listing — and the forecast engines combine them with this report&apos;s RHP features.
      </p>
      <div className="flex flex-wrap items-end gap-3">
        {fields.map(([k, label]) => (
          <label key={k} className="text-xs text-ink-2">
            {label}
            <input value={vals[k]} onChange={(e) => set(k, e.target.value)}
              inputMode="decimal"
              className="mt-1 block w-24 rounded border border-edge bg-page px-2 py-1 text-sm text-ink" />
          </label>
        ))}
        <button
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await api.setSignals(analysisId, {
                gmp: asNum("gmp"), sub_qib: asNum("sub_qib"), sub_bnii: asNum("sub_bnii"),
                sub_snii: asNum("sub_snii"), sub_rii: asNum("sub_rii"), day1_gain: asNum("day1_gain"),
              });
              setSaved(true);
              setForecast(await api.getForecast(analysisId));
            } catch { setSaved(false); }
            setBusy(false);
          }}
          className="rounded bg-accent px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50">
          {busy ? "Working…" : "Save & forecast"}
        </button>
        {saved ? <span className="text-xs text-good">Saved ✓</span> : null}
      </div>
      {forecast ? <ForecastPanel fc={forecast} /> : null}
    </Card>
  );
}

function ForecastPanel({ fc }: { fc: ListingForecast }) {
  const hz = fc.ml_horizons;
  const rules = fc.rules;
  return (
    <div className="mt-4 border-t border-edge pt-4">
      {hz ? (
        <>
          <div className="grid gap-3 sm:grid-cols-3">
            {Object.entries(hz.horizons).map(([label, h]) => (
              <div key={label} className="rounded border border-edge bg-page p-3">
                <div className="text-xs text-ink-3">{label} vs offer price</div>
                <div className={`text-lg font-semibold tabular ${h.ret_pct_vs_offer >= 0 ? "text-good" : "text-critical"}`}>
                  {h.ret_pct_vs_offer >= 0 ? "+" : ""}{h.ret_pct_vs_offer}%
                </div>
                <div className="text-xs text-ink-3">
                  P(above offer) {Math.round(h.p_above_offer * 100)}%
                  {h.cv_mae_pp != null ? ` · ±${h.cv_mae_pp}pp CV error` : ""}
                </div>
              </div>
            ))}
          </div>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <div className="rounded border border-edge bg-page p-3 text-sm text-ink-2">
              <div className="mb-1 text-xs font-medium uppercase tracking-wide text-ink-3">Entry</div>
              {hz.entry?.expected_bottom_session != null ? (
                <>
                  Expected low around session <b>{hz.entry.expected_bottom_session}</b>
                  {hz.entry.expected_bottom_depth_pct_vs_offer != null ?
                    <> at <b>{hz.entry.expected_bottom_depth_pct_vs_offer}%</b> vs offer</> : null}
                  {hz.entry.read ? <div className="mt-1 text-xs text-ink-3">{hz.entry.read}</div> : null}
                </>
              ) : "Not enough data."}
            </div>
            <div className="rounded border border-edge bg-page p-3 text-sm text-ink-2">
              <div className="mb-1 text-xs font-medium uppercase tracking-wide text-ink-3">Exit</div>
              {hz.exit?.call ? (
                <>
                  <b>{hz.exit.call}</b>
                  {hz.exit.expected_ret_pct != null ? <> · expected {hz.exit.expected_ret_pct >= 0 ? "+" : ""}{hz.exit.expected_ret_pct}%</> : null}
                  {hz.exit.expected_peak_pct_vs_offer != null ?
                    <div className="mt-1 text-xs text-ink-3">
                      Peak within 2y: ~{hz.exit.expected_peak_pct_vs_offer}% vs offer around session {hz.exit.expected_peak_session}
                    </div> : null}
                  {hz.exit.note ? <div className="mt-1 text-xs text-ink-3">{hz.exit.note}</div> : null}
                </>
              ) : "Not enough data."}
            </div>
          </div>
          <p className="mt-2 text-xs leading-relaxed text-ink-3">{hz.note} Inputs used: {hz.inputs_used.join(", ") || "training medians only"}.</p>
        </>
      ) : rules ? (
        <p className="text-sm text-ink-2">
          Rules engine: listing premium ~{rules.listing_open_premium_pct.point}% (range {rules.listing_open_premium_pct.range[0]}…{rules.listing_open_premium_pct.range[1]}%),
          P(falls below offer) {Math.round(rules.falls_below_offer.probability * 100)}%, bottom window sessions {rules.bottom.window_sessions[0]}–{rules.bottom.window_sessions[1]}.
        </p>
      ) : null}
      <p className="mt-2 text-xs italic text-ink-3">{fc.disclaimer}</p>
    </div>
  );
}
