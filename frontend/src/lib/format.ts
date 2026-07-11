export function fmtCr(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "n/a";
  return `₹${v.toLocaleString("en-IN", { maximumFractionDigits: digits })} cr`;
}

export function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "n/a";
  return `${(v * 100).toFixed(digits)}%`;
}

export function fmtX(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "n/a";
  return `${v.toFixed(digits)}x`;
}

export function titleCase(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export const CATEGORY_LABELS: Record<string, string> = {
  business_quality: "Business Quality",
  financial_health: "Financial Health",
  growth: "Growth",
  valuation: "Valuation",
  promoter_quality: "Promoter Quality",
  governance: "Governance",
  risk_level: "Risk Level",
  competitive_position: "Competitive Position",
  capital_efficiency: "Capital Efficiency",
  cash_generation: "Cash Generation",
};
