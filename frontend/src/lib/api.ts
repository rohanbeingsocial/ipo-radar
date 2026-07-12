export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface AnalysisSummary {
  id: string;
  company_name: string | null;
  filename: string;
  status: "queued" | "processing" | "completed" | "failed";
  stage: string | null;
  progress: number;
  confidence: string | null;
  is_demo: boolean;
  overall_score: number | null;
  verdict: string | null;
  created_at: string | null;
}

export interface RuleTrace {
  category: string;
  rule: string;
  points: number | null;
  max_points: number;
  value: unknown;
  thresholds: string;
  evidence: string;
  source_pages: number[];
  confidence: number;
  rationale: string;
  included: boolean;
}

export interface CaseItem {
  text: string;
  rule: string;
  category: string;
  source_pages: (number | null)[];
  confidence: number;
}

export interface RiskFinding {
  risk_type: string;
  severity: "low" | "medium" | "high" | "critical";
  title: string;
  detail?: string;
  evidence_text?: string;
  source_page?: number;
  quantified?: { metric: string; value: number } | null;
}

// The report payload is large; typed loosely where the UI only passes through.
export interface Report {
  meta: {
    company_name: string | null;
    page_count: number;
    doc_type: string;
    confidence: string;
    coverage: number;
    readable_ratio: number;
    section_hit_rate: number;
    llm_enhanced: boolean;
    disclaimer: string;
  };
  executive_summary: { text: string; source_pages: number[] }[];
  snapshot: Record<string, unknown> & {
    price_band: (number | null)[];
    objects: { purpose: string; category: string; amount_cr: number | null }[];
    source_pages: Record<string, number>;
  };
  financials: {
    series: Record<string, number | string | null>[];
    unit_note: string;
    source_pages: Record<string, number>;
    ratios: Record<string, number>;
    margin_series: { fy: string; net_margin: number | null; operating_margin: number | null }[];
  };
  valuation: {
    issuer_pe: number | null;
    peer_pe_median: number | null;
    relative: number | null;
    call: string;
    call_label: string;
    reasoning: string[];
    peers: { name: string; pe?: number | null; eps?: number | null; ronw?: number | null; source_page?: number }[];
  };
  risk: {
    score: number;
    heatmap: { risk_type: string; severity: string; count: number }[];
    findings: RiskFinding[];
    boilerplate: { total_factors: number; specific: number; specificity_ratio: number | null };
  };
  forensic: {
    flags: { rule: string; severity: string; detail: string; value: number | null; source_page: number | null }[];
    checks: { check: string; passed: boolean; value: number }[];
    strength_score: { passed: number; total: number };
    cap_triggered: boolean;
    consistency?: { type: string; severity: string; detail: string }[];
  };
  promoter: {
    names: string[];
    experience_claims: number[];
    board: Record<string, number>;
    group_company_conflicts: boolean;
    past_ventures_mentioned: boolean;
    pre_issue_pct: number | null;
    post_issue_pct: number | null;
    pledging: { pledged: boolean; evidence?: string | null };
    source_pages: Record<string, number | null>;
  };
  industry: { excerpt: string; source_page: number | null };
  scoring: {
    overall: number;
    lens: string;
    weights: Record<string, number>;
    cap_note: string | null;
    categories: Record<string, { score: number; weight: number; coverage: number; rules: RuleTrace[] }>;
  };
  verdict: string;
  cases: { bull: CaseItem[]; bear: CaseItem[]; neutral: CaseItem[] };
  flags: { green: CaseItem[]; red: CaseItem[] };
  questions: string[];
  sections_index: { key: string; title: string | null; found: boolean; page_start: number | null; page_end: number | null; method: string | null }[];
  market_signals: { gmp: number | null; sub_qib: number | null; sub_nii: number | null; sub_rii: number | null; sub_bnii: number | null; sub_snii: number | null; day1_gain: number | null; note: string } | null;
}

export interface HorizonForecast {
  engine: string;
  /** which model answered: "pre" (not yet listed, no day-1 gain) or "post" (trading) */
  variant?: "pre" | "post" | "legacy";
  inputs_used: string[];
  /**
   * Only horizons with demonstrated out-of-sample skill appear, and a horizon may carry
   * a probability without a point estimate — pre-listing, the direction and the size of
   * 6m/12m/24m moves are both unpredictable, so the key is simply absent. Missing means
   * "we cannot call this", not "not loaded".
   */
  horizons: Record<string, {
    ret_pct_vs_offer?: number; cv_mae_pp?: number | null; cv_baseline_mae_pp?: number | null;
    p_above_offer?: number; cv_direction_acc?: number | null;
  }>;
  no_skill?: { horizons: string[]; why: string };
  entry: { expected_bottom_session?: number; expected_bottom_depth_pct_vs_offer?: number; cv_mae_pp?: number | null; read?: string };
  exit: { call?: string; expected_ret_pct?: number; expected_peak_pct_vs_offer?: number; expected_peak_session?: number; note?: string };
  note: string;
}

export interface ListingForecast {
  rules?: {
    listing_open_premium_pct: { point: number; range: [number, number] };
    falls_below_offer: { probability: number; window_sessions: [number, number]; expected_depth_pct: number; note?: string };
    bottom: { window_sessions: [number, number]; depth_vs_offer_pct: [number, number]; note?: string };
    recovery: { above_offer_sessions: [number, number] | null; above_listing_sessions: [number, number] | null; note?: string };
  };
  ml_signals?: {
    forecast_listing_gain_pct_vs_offer: number;
    cv_mae_pp: number; baseline_mae_pp?: number; cv_direction_acc: number;
    cv?: string; inputs_used: string[]; note: string;
  };
  ml_horizons?: HorizonForecast;
  disclaimer: string;
}

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  listAnalyses: () => fetch(`${API_BASE}/api/analyses`, { cache: "no-store" }).then((r) => j<AnalysisSummary[]>(r)),
  getAnalysis: (id: string) =>
    fetch(`${API_BASE}/api/analyses/${id}`, { cache: "no-store" }).then((r) =>
      j<AnalysisSummary & { error: string | null; page_count: number | null }>(r)),
  getReport: (id: string) => fetch(`${API_BASE}/api/analyses/${id}/report`, { cache: "no-store" }).then((r) => j<Report>(r)),
  upload: async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return j<{ document_id: string; analysis_id: string }>(
      await fetch(`${API_BASE}/api/documents`, { method: "POST", body: form }));
  },
  ask: async (id: string, question: string) =>
    j<{ answer: string | null; citations?: { section: string; pages: (number | null)[] }[]; error?: string }>(
      await fetch(`${API_BASE}/api/analyses/${id}/qa`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      })),
  setSignals: async (id: string, body: { gmp?: number | null; sub_qib?: number | null; sub_nii?: number | null; sub_rii?: number | null; sub_bnii?: number | null; sub_snii?: number | null; day1_gain?: number | null }) =>
    j<{ ok: boolean }>(await fetch(`${API_BASE}/api/analyses/${id}/market-signals`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })),
  getForecast: (id: string) =>
    fetch(`${API_BASE}/api/analyses/${id}/listing-forecast`, { cache: "no-store" }).then((r) => j<ListingForecast>(r)),
};
