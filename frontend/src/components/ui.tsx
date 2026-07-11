import { clsx } from "clsx";
import type { ReactNode } from "react";

export function cn(...args: Parameters<typeof clsx>) {
  return clsx(...args);
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("rounded-lg border border-edge bg-surface p-4", className)}>
      {children}
    </div>
  );
}

export function CardTitle({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <h3 className={cn("mb-3 text-[11px] font-semibold uppercase tracking-wider text-ink-3", className)}>
      {children}
    </h3>
  );
}

const badgeVariants: Record<string, string> = {
  neutral: "border-edge text-ink-2",
  accent: "border-transparent bg-accent/15 text-accent",
  good: "border-transparent bg-good/15 text-good",
  warn: "border-transparent bg-warn/20 text-ink",
  serious: "border-transparent bg-serious/20 text-ink",
  critical: "border-transparent bg-critical/15 text-critical",
};

export function Badge({ children, variant = "neutral", className }:
  { children: ReactNode; variant?: keyof typeof badgeVariants; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-medium",
      badgeVariants[variant], className)}>
      {children}
    </span>
  );
}

export function PageChip({ pages }: { pages: (number | null | undefined)[] }) {
  const valid = [...new Set(pages.filter((p): p is number => typeof p === "number"))];
  if (!valid.length) return null;
  return (
    <span className="ml-1 inline-flex items-center gap-1 whitespace-nowrap rounded bg-accent/10 px-1 py-px text-[10px] font-medium text-accent"
      title="Source page(s) in the prospectus PDF">
      p.{valid.join(", ")}
    </span>
  );
}

export function Progress({ value, className, tone = "var(--accent)" }:
  { value: number; className?: string; tone?: string }) {
  return (
    <div className={cn("h-1.5 w-full overflow-hidden rounded-full bg-grid", className)}
      role="progressbar" aria-valuenow={Math.round(value)} aria-valuemin={0} aria-valuemax={100}>
      <div className="h-full rounded-full transition-all"
        style={{ width: `${Math.min(100, Math.max(0, value))}%`, background: tone }} />
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded bg-grid", className)} />;
}

export function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wider text-ink-3">{label}</div>
      <div className="tabular text-lg font-semibold text-ink">{value}</div>
      {sub ? <div className="text-xs text-ink-2">{sub}</div> : null}
    </div>
  );
}

export function Disclaimer({ text }: { text: string }) {
  return (
    <p className="mx-auto max-w-4xl border-t border-edge px-4 py-4 text-center text-[11px] leading-relaxed text-ink-3">
      ⚠ {text}
    </p>
  );
}

export function scoreTone(score: number): string {
  if (score >= 70) return "var(--status-good)";
  if (score >= 50) return "var(--status-warning)";
  if (score >= 35) return "var(--status-serious)";
  return "var(--status-critical)";
}
