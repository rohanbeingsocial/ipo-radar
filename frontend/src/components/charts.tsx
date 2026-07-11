"use client";

import {
  Bar, BarChart, CartesianGrid, Cell, LabelList, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

/* Shared chart chrome per the dataviz spec: recessive horizontal-only grid,
   muted axis ink, thin marks with 4px rounded data-ends, 2px bar gaps,
   hover tooltips on every mark, legend for ≥2 series, direct labels selective. */

const AXIS = { fontSize: 11, fill: "var(--text-muted)" } as const;

export interface Series {
  key: string;
  label: string;
  color: string; // CSS var reference
}

function ChartTooltip({ active, payload, label, format }: {
  active?: boolean;
  payload?: { name: string; value: number; color?: string }[];
  label?: string;
  format: (v: number) => string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded border border-edge bg-surface px-2.5 py-1.5 text-xs shadow-sm">
      <div className="mb-1 font-semibold text-ink">{label}</div>
      {payload.map((p) => (
        <div key={p.name} className="flex items-center gap-1.5 text-ink-2">
          <span className="h-2 w-2 rounded-sm" style={{ background: p.color }} />
          {p.name}: <span className="tabular font-medium text-ink">{format(p.value)}</span>
        </div>
      ))}
    </div>
  );
}

function LegendRow({ series }: { series: Series[] }) {
  if (series.length < 2) return null;
  return (
    <div className="mt-1 flex flex-wrap items-center gap-3 text-[11px] text-ink-2">
      {series.map((s) => (
        <span key={s.key} className="inline-flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-sm" style={{ background: s.color }} />
          {s.label}
        </span>
      ))}
    </div>
  );
}

export function GroupedBars({ data, series, xKey, format, height = 220 }: {
  data: Record<string, unknown>[];
  series: Series[];
  xKey: string;
  format: (v: number) => string;
  height?: number;
}) {
  return (
    <div>
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={data} barGap={2} barCategoryGap="28%" margin={{ top: 8, right: 8, bottom: 0, left: 4 }}>
          <CartesianGrid vertical={false} stroke="var(--grid)" strokeWidth={1} />
          <XAxis dataKey={xKey} tick={AXIS} axisLine={{ stroke: "var(--baseline)" }} tickLine={false} />
          <YAxis tick={AXIS} axisLine={false} tickLine={false} width={52}
            tickFormatter={(v: number) => format(v).replace(/^₹/, "")} />
          <Tooltip cursor={{ fill: "var(--grid)", opacity: 0.4 }}
            content={<ChartTooltip format={format} />} />
          {series.map((s) => (
            <Bar key={s.key} dataKey={s.key} name={s.label} fill={s.color}
              radius={[4, 4, 0, 0]} maxBarSize={34} />
          ))}
        </BarChart>
      </ResponsiveContainer>
      <LegendRow series={series} />
    </div>
  );
}

export function TrendLines({ data, series, xKey, format, height = 220 }: {
  data: Record<string, unknown>[];
  series: Series[];
  xKey: string;
  format: (v: number) => string;
  height?: number;
}) {
  return (
    <div>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
          <CartesianGrid vertical={false} stroke="var(--grid)" strokeWidth={1} />
          <XAxis dataKey={xKey} tick={AXIS} axisLine={{ stroke: "var(--baseline)" }} tickLine={false} />
          <YAxis tick={AXIS} axisLine={false} tickLine={false} width={48}
            tickFormatter={(v: number) => format(v)} />
          <Tooltip cursor={{ stroke: "var(--baseline)", strokeDasharray: "3 3" }}
            content={<ChartTooltip format={format} />} />
          {series.map((s) => (
            <Line key={s.key} dataKey={s.key} name={s.label} stroke={s.color}
              strokeWidth={2} dot={false} activeDot={{ r: 4, strokeWidth: 0 }} />
          ))}
        </LineChart>
      </ResponsiveContainer>
      <LegendRow series={series} />
    </div>
  );
}

/** Emphasis form: the issuer bar in the accent hue, peers in de-emphasis gray. */
export function EmphasisBars({ data, format, height = 220 }: {
  data: { name: string; value: number; emphasized?: boolean }[];
  format: (v: number) => string;
  height?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} layout="vertical" barCategoryGap="24%"
        margin={{ top: 4, right: 56, bottom: 0, left: 8 }}>
        <CartesianGrid horizontal={false} stroke="var(--grid)" strokeWidth={1} />
        <XAxis type="number" tick={AXIS} axisLine={{ stroke: "var(--baseline)" }} tickLine={false} />
        <YAxis type="category" dataKey="name" tick={{ ...AXIS, fill: "var(--text-secondary)" }}
          axisLine={false} tickLine={false} width={170} />
        <Tooltip cursor={{ fill: "var(--grid)", opacity: 0.4 }} content={<ChartTooltip format={format} />} />
        <Bar dataKey="value" name="P/E" radius={[0, 4, 4, 0]} maxBarSize={22}>
          <LabelList dataKey="value" position="right"
            style={{ fill: "var(--text-secondary)", fontSize: 11 }}
            formatter={(v) => format(Number(v))} />
          {data.map((d) => (
            <Cell key={d.name} fill={d.emphasized ? "var(--accent)" : "var(--emphasis-gray)"} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Category scores: magnitude on a common 0–100 scale → single-hue bars. */
export function ScoreBars({ data, height }: {
  data: { name: string; value: number }[];
  height?: number;
}) {
  const h = height ?? data.length * 34 + 24;
  return (
    <ResponsiveContainer width="100%" height={h}>
      <BarChart data={data} layout="vertical" barCategoryGap="30%"
        margin={{ top: 0, right: 40, bottom: 0, left: 8 }}>
        <XAxis type="number" domain={[0, 100]} tick={AXIS} axisLine={{ stroke: "var(--baseline)" }} tickLine={false} />
        <YAxis type="category" dataKey="name" tick={{ ...AXIS, fill: "var(--text-secondary)" }}
          axisLine={false} tickLine={false} width={148} />
        <Tooltip cursor={{ fill: "var(--grid)", opacity: 0.4 }}
          content={<ChartTooltip format={(v) => `${v}/100`} />} />
        <Bar dataKey="value" name="Score" fill="var(--seq-400)" radius={[0, 4, 4, 0]} maxBarSize={16}>
          <LabelList dataKey="value" position="right" style={{ fill: "var(--text-secondary)", fontSize: 11 }} />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Part-to-whole: horizontal stacked share bar with 2px surface gaps. */
export function ShareBar({ parts, format }: {
  parts: { label: string; value: number; color: string }[];
  format: (v: number) => string;
}) {
  const total = parts.reduce((s, p) => s + p.value, 0);
  if (!total) return null;
  return (
    <div>
      <div className="flex h-5 w-full gap-[2px] overflow-hidden rounded">
        {parts.map((p) => (
          <div key={p.label} title={`${p.label}: ${format(p.value)} (${((p.value / total) * 100).toFixed(0)}%)`}
            className="h-full first:rounded-l last:rounded-r"
            style={{ width: `${(p.value / total) * 100}%`, background: p.color }} />
        ))}
      </div>
      <div className="mt-1.5 flex flex-wrap gap-3 text-[11px] text-ink-2">
        {parts.map((p) => (
          <span key={p.label} className="inline-flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-sm" style={{ background: p.color }} />
            {p.label}: <span className="tabular font-medium text-ink">{format(p.value)}</span>
            <span className="text-ink-3">({((p.value / total) * 100).toFixed(0)}%)</span>
          </span>
        ))}
      </div>
    </div>
  );
}
