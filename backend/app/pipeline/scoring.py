"""Stage 8: transparent rubric scoring.

Every category score is the weighted sum of named rules. A rule always emits:
points, max_points, the extracted value it used, threshold bands, evidence
text, source page(s) and a confidence. Missing inputs EXCLUDE the rule (never
silently zero) and reduce category confidence. No black boxes.
"""
from __future__ import annotations

import re
from statistics import median, pstdev

from .financial_extractor import get_metric

WEIGHT_LENSES = {
    "balanced": {"business_quality": 0.12, "financial_health": 0.12, "growth": 0.12,
                 "valuation": 0.14, "promoter_quality": 0.10, "governance": 0.08,
                 "risk_level": 0.12, "competitive_position": 0.06,
                 "capital_efficiency": 0.07, "cash_generation": 0.07},
    "conservative": {"business_quality": 0.10, "financial_health": 0.14, "growth": 0.06,
                     "valuation": 0.12, "promoter_quality": 0.10, "governance": 0.12,
                     "risk_level": 0.18, "competitive_position": 0.04,
                     "capital_efficiency": 0.06, "cash_generation": 0.08},
    "growth": {"business_quality": 0.12, "financial_health": 0.08, "growth": 0.18,
               "valuation": 0.10, "promoter_quality": 0.10, "governance": 0.08,
               "risk_level": 0.08, "competitive_position": 0.10,
               "capital_efficiency": 0.08, "cash_generation": 0.08},
}


def _band(value: float, bands: list[tuple[float, float]], ascending: bool = False) -> float:
    """bands: [(threshold, points)...] with thresholds in DESCENDING order.
    Default (higher-is-better): first band where value >= threshold wins.
    ascending=True (lower-is-better, e.g. D/E): first band where value >
    threshold wins, so the list reads 'above X → few points … above sentinel →
    full points'."""
    for threshold, points in bands:
        if (value > threshold) if ascending else (value >= threshold):
            return points
    return bands[-1][1]


class Rubric:
    def __init__(self):
        self.rules: list[dict] = []

    def add(self, category: str, rule: str, points: float | None, max_points: float,
            value=None, thresholds: str = "", evidence: str = "", source_pages: list[int] | None = None,
            confidence: float = 0.9, rationale: str = ""):
        """points=None → input missing → rule excluded from scoring, listed as unknown."""
        self.rules.append({
            "category": category, "rule": rule,
            "points": round(points, 1) if points is not None else None,
            "max_points": max_points,
            "value": round(value, 3) if isinstance(value, float) else value,
            "thresholds": thresholds, "evidence": evidence,
            "source_pages": [p for p in (source_pages or []) if p],
            "confidence": confidence, "rationale": rationale,
            "included": points is not None,
        })


def build_rubric(ctx: dict) -> Rubric:
    fin, ratios = ctx["financials"], ctx["ratios"]
    issue, ents = ctx["issue"], ctx["entities"]
    risks, forensic, val = ctx["risks"], ctx["forensic"], ctx["valuation"]
    sp = fin.get("source_pages", {})
    R = Rubric()

    def fp(metric: str) -> list[int]:
        return [sp.get(metric)] if sp.get(metric) else []

    # ---------------- business quality ----------------
    om = ratios.get("operating_margin")
    R.add("business_quality", "operating_margin_level",
          None if om is None else _band(om * 100, [(25, 20), (15, 15), (8, 10), (3, 5), (-999, 0)]),
          20, om if om is None else om * 100, "≥25%→20 · 15–25→15 · 8–15→10 · 3–8→5 · <3→0",
          f"EBITDA margin {om * 100:.1f}% (latest restated year)" if om is not None else "EBITDA/revenue unavailable",
          fp("ebitda") + fp("revenue"),
          rationale="Margin level proxies pricing power and business model quality.")

    margins = [m["net_margin"] for m in ratios.get("margin_series", []) if m.get("net_margin") is not None]
    if len(margins) >= 2:
        spread = pstdev(margins) * 100
        R.add("business_quality", "margin_stability",
              _band(spread, [(6, 0), (3, 5), (1.5, 8), (-1, 10)], ascending=True),
              10, spread, "σ(net margin) ≤1.5pt→10 · ≤3→8 · ≤6→5 · >6→0",
              f"Net-margin dispersion across {len(margins)} years: {spread:.1f} points", fp("pat"),
              rationale="Stable margins indicate durable economics rather than one-off swings.")
    else:
        R.add("business_quality", "margin_stability", None, 10, None, "", "Fewer than 2 years of margins extracted")

    rev = get_metric(fin, "revenue")
    R.add("business_quality", "revenue_scale",
          None if rev is None else _band(rev, [(1000, 10), (250, 7), (50, 4), (-1, 2)]),
          10, rev, "≥₹1,000cr→10 · ≥250→7 · ≥50→4 · <50→2",
          f"Revenue from operations ₹{rev:,.0f} cr" if rev is not None else "Revenue unavailable", fp("revenue"),
          rationale="Scale correlates with resilience and institutional interest.")

    # ---------------- financial health ----------------
    de = ratios.get("debt_equity")
    R.add("financial_health", "debt_to_equity",
          None if de is None else _band(de, [(2.5, 0), (1.5, 4), (0.75, 10), (0.3, 19), (-1, 25)], ascending=True),
          25, de, "≤0.3→25 · ≤0.75→19 · ≤1.5→10 · ≤2.5→4 · >2.5→0",
          f"D/E {de:.2f} (borrowings ÷ net worth)" if de is not None else "Debt or net worth unavailable",
          fp("total_debt") + fp("net_worth"),
          rationale="Leverage entering listing is the primary balance-sheet risk.")

    cr = ratios.get("current_ratio")
    R.add("financial_health", "current_ratio",
          None if cr is None else _band(cr, [(1.5, 15), (1.1, 11), (0.9, 6), (-1, 0)]),
          15, cr, "≥1.5→15 · ≥1.1→11 · ≥0.9→6 · <0.9→0",
          f"Current ratio {cr:.2f}" if cr is not None else "Current assets/liabilities unavailable",
          fp("current_assets"),
          rationale="Short-term liquidity buffer.")

    ic = ratios.get("interest_cover")
    R.add("financial_health", "interest_cover",
          None if ic is None else _band(ic, [(8, 10), (4, 7), (2, 4), (-999, 0)]),
          10, ic, "≥8x→10 · ≥4→7 · ≥2→4 · <2→0",
          f"EBIT/interest {ic:.1f}x" if ic is not None else "Finance costs unavailable", fp("finance_costs"),
          rationale="Ability to service debt from operating profit.")

    cl_amt, nw = (ents.get("contingent") or {}).get("total_cr"), get_metric(fin, "net_worth")
    if cl_amt is not None and nw:
        pct = cl_amt / nw * 100
        R.add("financial_health", "contingent_liabilities",
              _band(pct, [(50, 0), (30, 3), (10, 6), (-1, 10)], ascending=True),
              10, pct, "≤10% of net worth→10 · ≤30→6 · ≤50→3 · >50→0",
              f"Contingent liabilities ₹{cl_amt:,.0f} cr = {pct:.0f}% of net worth",
              [(ents.get("contingent") or {}).get("source_page")],
              rationale="Off-balance-sheet obligations that can crystallize post-listing.")
    else:
        R.add("financial_health", "contingent_liabilities", None, 10, None, "",
              "Contingent-liabilities total not extracted")

    # ---------------- growth ----------------
    for name, key in [("revenue_cagr", "revenue_cagr"), ("pat_cagr", "pat_cagr")]:
        g = ratios.get(key)
        R.add("growth", name,
              None if g is None else _band(g * 100, [(25, 20), (15, 16), (8, 10), (0, 5), (-999, 0)]),
              20, None if g is None else g * 100, "≥25%→20 · ≥15→16 · ≥8→10 · ≥0→5 · <0→0",
              f"{name.split('_')[0].upper()} CAGR {g * 100:.1f}% over the restated period" if g is not None
              else "Insufficient year series", fp("revenue" if "revenue" in name else "pat"),
              rationale="Multi-year compounding, not just the latest pre-IPO year.")

    order = fin.get("fiscal_order") or []
    if len(order) >= 3:
        revs = [get_metric(fin, "revenue", i) for i in range(len(order))]
        if all(v is not None for v in revs):
            ups = sum(1 for a, b in zip(revs[:-1], revs[1:]) if a > b)
            R.add("growth", "growth_consistency", 10 * ups / (len(revs) - 1), 10, ups,
                  "every yoy step positive → full points",
                  f"Revenue rose in {ups}/{len(revs) - 1} year-over-year steps", fp("revenue"),
                  rationale="Consistency separates trend from a single dressed-up year.")

    # ---------------- valuation ----------------
    call = val.get("call")
    call_points = {"undervalued": 30, "fairly_valued": 22, "fairly_valued_expensive": 12, "overvalued": 4}
    R.add("valuation", "peer_relative_pe",
          call_points.get(call), 30, val.get("relative"),
          "vs peer-median P/E: <0.7x→30 · 0.7–1.1→22 · 1.1–1.5→12 · >1.5→4",
          f"Issue P/E {val.get('issuer_pe')}x vs peer median {val.get('peer_pe_median')}x"
          if val.get("issuer_pe") and val.get("peer_pe_median") else "; ".join(val.get("reasoning", [])[:1]),
          [p.get("source_page") for p in (ctx["issue"].get("peers_json") or [])[:1]],
          rationale="Peer-relative pricing from the RHP's own mandated comparison table.")

    ipe = val.get("issuer_pe")
    R.add("valuation", "absolute_pe",
          None if ipe is None else _band(ipe, [(60, 1), (30, 4), (15, 7), (-1, 10)], ascending=True),
          10, ipe, "≤15x→10 · ≤30→7 · ≤60→4 · >60→1",
          f"Issue P/E {ipe:.1f}x at upper price band" if ipe is not None else "Issuer P/E not extracted",
          rationale="Sanity check independent of the issuer-chosen peer set.")

    if ipe and ratios.get("pat_cagr") and ratios["pat_cagr"] > 0:
        peg = ipe / (ratios["pat_cagr"] * 100)
        R.add("valuation", "growth_adjusted_pe", _band(peg, [(2, 2), (1, 6), (-1, 10)], ascending=True),
              10, peg, "PEG ≤1→10 · ≤2→6 · >2→2",
              f"PEG {peg:.2f} (P/E {ipe:.1f} ÷ PAT CAGR {ratios['pat_cagr'] * 100:.0f})",
              rationale="High multiples can be earned by growth; flat growth cannot.")
    else:
        R.add("valuation", "growth_adjusted_pe", None, 10, None, "", "Needs both issuer P/E and positive PAT CAGR")

    # ---------------- promoter quality ----------------
    post = issue.get("post_issue_promoter_pct")
    R.add("promoter_quality", "post_issue_holding",
          None if post is None else _band(post, [(60, 15), (45, 12), (30, 7), (-1, 3)]),
          15, post, "≥60%→15 · ≥45→12 · ≥30→7 · <30→3",
          f"Post-issue promoter holding {post:.1f}%" if post is not None else "Not extracted",
          [issue.get("source_pages", {}).get("post_issue_promoter_pct")],
          rationale="Skin in the game after listing.")

    fresh, ofs = issue.get("fresh_issue_cr"), issue.get("ofs_cr")
    if fresh is not None or ofs is not None:
        total = (fresh or 0) + (ofs or 0)
        ofs_share = (ofs or 0) / total * 100 if total else 0
        R.add("promoter_quality", "ofs_share_of_issue",
              _band(ofs_share, [(75, 0), (50, 5), (25, 10), (-1, 15)], ascending=True),
              15, ofs_share, "≤25% OFS→15 · ≤50→10 · ≤75→5 · >75→0",
              f"OFS ₹{ofs or 0:,.0f} cr of ₹{total:,.0f} cr total ({ofs_share:.0f}%) — "
              f"fresh capital ₹{fresh or 0:,.0f} cr goes to the company, OFS to selling holders",
              [issue.get("source_pages", {}).get("ofs")],
              rationale="Exit-heavy offers raise nothing for growth; every manual analyst review leads with this.")
    else:
        R.add("promoter_quality", "ofs_share_of_issue", None, 15, None, "", "Fresh/OFS split not extracted")

    pledge = ents.get("pledging") or {}
    R.add("promoter_quality", "no_pledging", 0 if pledge.get("pledged") else 10, 10,
          pledge.get("pledged"), "no promoter pledge→10 · pledge disclosed→0",
          pledge.get("evidence") or "No promoter share pledge detected in Capital Structure / Risk Factors",
          [pledge.get("source_page")],
          rationale="Pledged promoter stakes transmit personal leverage into the stock.")

    pre = issue.get("pre_issue_promoter_pct")
    if pre is not None and post is not None:
        dilution = pre - post
        R.add("promoter_quality", "dilution_size", _band(dilution, [(20, 3), (10, 6), (-999, 10)], ascending=True),
              10, dilution, "≤10pt→10 · ≤20→6 · >20→3",
              f"Promoter stake {pre:.1f}% → {post:.1f}% ({dilution:.1f} points)",
              rationale="Large step-downs at listing warrant a why.")

    # ---------------- governance ----------------
    rpt = ents.get("rpt") or {}
    if rpt.get("total_cr") is not None and rev:
        intensity = rpt["total_cr"] / rev * 100
        R.add("governance", "rpt_intensity", _band(intensity, [(10, 0), (2, 6), (-1, 10)], ascending=True),
              10, intensity, "≤2% of revenue→10 · ≤10→6 · >10→0",
              f"Related-party transactions ₹{rpt['total_cr']:,.0f} cr = {intensity:.1f}% of revenue",
              [rpt.get("source_page")],
              rationale="High RPT intensity is a top forensic governance marker.")
    else:
        R.add("governance", "rpt_intensity", None, 10, None, "",
              "RPT aggregate not extracted" + (" (note located)" if rpt.get("found") else ""),
              [rpt.get("source_page")])

    lit = ents.get("litigation") or {}
    crim = (lit.get("counts") or {}).get("criminal")
    R.add("governance", "criminal_proceedings",
          None if crim is None else (10 if crim == 0 else 4 if crim <= 2 else 0),
          10, crim, "0→10 · 1–2→4 · >2→0",
          f"{crim} criminal proceeding(s) disclosed" if crim is not None else "Litigation summary not parsed",
          [lit.get("source_page")],
          rationale="Criminal matters against company/promoters/directors.")

    aud = ents.get("auditor") or {}
    R.add("governance", "audit_opinion",
          0 if aud.get("qualified") else 5 if aud.get("emphasis_of_matter") else 10,
          10, None, "clean→10 · emphasis of matter→5 · qualified/adverse→0",
          "Qualified/adverse audit language detected" if aud.get("qualified")
          else "Emphasis-of-matter paragraph present" if aud.get("emphasis_of_matter")
          else "No qualification language detected in the restated auditor's report",
          [aud.get("source_page")],
          rationale="Auditor reservations are the highest-signal governance flag in the document.")

    div = ents.get("dividend") or {}
    R.add("governance", "dividend_track", 5 if div.get("declared") else 3 if div.get("declared") is False else None,
          5, div.get("declared"), "history of dividends→5 · none→3",
          "Dividend history disclosed" if div.get("declared") else "No dividends declared in reported years",
          [div.get("source_page")],
          rationale="A payout history modestly evidences real distributable cash.")

    # ---------------- risk level (inverted from analyzer) ----------------
    rs = risks.get("risk_score", 50)
    sev_counts: dict[str, int] = {}
    for f in risks.get("findings", []):
        sev_counts[f["severity"]] = sev_counts.get(f["severity"], 0) + 1
    R.add("risk_level", "risk_penalty_model", rs, 100, rs,
          "100 − Σ(critical 18 · high 10 · medium 4 · low 1)",
          "Findings: " + (", ".join(f"{v} {k}" for k, v in sev_counts.items()) or "none material"),
          [ctx["sections"].get("risk_factors", {}).get("page_start")],
          rationale="Each finding is itemised in the Risks tab with its own evidence.")

    # ---------------- competitive position ----------------
    peers = issue.get("peers_json") or []
    roe = ratios.get("roe")
    peer_ronws = [p["ronw"] for p in peers if isinstance(p.get("ronw"), (int, float))]
    if roe is not None and peer_ronws:
        med = median(peer_ronws)
        rel = roe * 100 - med
        R.add("competitive_position", "returns_vs_peers", _band(rel, [(5, 15), (0, 11), (-5, 6), (-999, 2)]),
              15, rel, "≥+5pt vs peer median→15 · ≥0→11 · ≥−5→6 · else→2",
              f"Issuer RoE {roe * 100:.1f}% vs peer median RoNW {med:.1f}%",
              [peers[0].get("source_page")],
              rationale="Return spread vs the listed peers named in the RHP.")
    else:
        R.add("competitive_position", "returns_vs_peers", None, 15, None, "", "Peer RoNW table not extracted")

    biz_text = ctx["sections"].get("our_business", {}).get("text", "")[:20000]
    claim = re.search(r"(?:one of the |the )?(largest|leading|market leader|top\s+(?:three|five|3|5))", biz_text, re.I)
    R.add("competitive_position", "market_position_claim", 5 if claim else 3, 5,
          bool(claim), "leadership claim present→5 (company-claimed, unverified) · none→3",
          f"Company claims: “…{claim.group(0)}…” — note this is the issuer's own characterisation"
          if claim else "No leadership claims found",
          [ctx["sections"].get("our_business", {}).get("page_start")],
          rationale="Claims are recorded but explicitly labelled as company-sourced.")

    # ---------------- capital efficiency ----------------
    for name, key, pages_key in [("roe", "roe", "pat"), ("roce", "roce", "pbt")]:
        v = ratios.get(key)
        R.add("capital_efficiency", name,
              None if v is None else _band(v * 100, [(20, 15), (12, 11), (6, 6), (-999, 1)]),
              15, None if v is None else v * 100, "≥20%→15 · ≥12→11 · ≥6→6 · <6→1",
              f"{name.upper()} {v * 100:.1f}%" if v is not None else f"{name.upper()} inputs unavailable",
              fp(pages_key),
              rationale="Returns on capital are the core of business quality compounding.")

    # ---------------- cash generation ----------------
    if order:
        cfo_vals = [(fy, fin["series"][fy].get("cfo")) for fy in order]
        known = [(fy, v) for fy, v in cfo_vals if v is not None]
        if known:
            pos = sum(1 for _, v in known if v > 0)
            R.add("cash_generation", "cfo_positive_years", 10 * pos / len(known), 10, pos,
                  "all reported years positive → full points",
                  f"CFO positive in {pos}/{len(known)} reported years "
                  f"({', '.join(f'{fy}: ₹{v:,.0f}cr' for fy, v in known)})", fp("cfo"),
                  rationale="Operating cash positivity streak.")
        else:
            R.add("cash_generation", "cfo_positive_years", None, 10, None, "", "Cash-flow statement not parsed")

    c2p = ratios.get("cfo_to_pat")
    R.add("cash_generation", "cfo_to_pat",
          None if c2p is None else _band(c2p, [(1.0, 20), (0.8, 14), (0.5, 8), (-999, 0)]),
          20, c2p, "≥1.0→20 · ≥0.8→14 · ≥0.5→8 · <0.5→0",
          f"CFO/PAT {c2p:.2f} (latest year)" if c2p is not None else "Needs both CFO and positive PAT",
          fp("cfo") + fp("pat"),
          rationale="Profit that never becomes cash is the classic earnings-quality failure.")

    cfo_l, capex_l = get_metric(fin, "cfo"), get_metric(fin, "capex")
    if cfo_l is not None and capex_l is not None:
        fcf = cfo_l - abs(capex_l)
        R.add("cash_generation", "free_cash_flow", 10 if fcf > 0 else 3, 10, fcf,
              "FCF positive→10 · negative→3 (heavy investment phase is common pre-IPO)",
              f"CFO ₹{cfo_l:,.0f}cr − capex ₹{abs(capex_l):,.0f}cr = FCF ₹{fcf:,.0f}cr", fp("capex"),
              rationale="Negative FCF is contextual pre-IPO; persistent negativity matters more.")
    else:
        R.add("cash_generation", "free_cash_flow", None, 10, None, "", "Capex line not extracted")

    return R


def score_all(ctx: dict, lens: str = "balanced") -> dict:
    weights = WEIGHT_LENSES.get(lens, WEIGHT_LENSES["balanced"])
    rubric = build_rubric(ctx)

    categories: dict[str, dict] = {}
    for cat in weights:
        rules = [r for r in rubric.rules if r["category"] == cat]
        included = [r for r in rules if r["included"]]
        got = sum(r["points"] for r in included)
        mx = sum(r["max_points"] for r in included)
        score = round(100 * got / mx, 1) if mx else 50.0
        coverage = sum(r["max_points"] for r in included) / max(1, sum(r["max_points"] for r in rules))
        categories[cat] = {"score": score, "weight": weights[cat], "rules": rules,
                           "coverage": round(coverage, 2)}

    overall = round(sum(c["score"] * c["weight"] for c in categories.values()), 1)

    cap_note = None
    if ctx["forensic"].get("cap_triggered") and overall > 55:
        cap_note = ("Forensic cap applied: a high-severity earnings-quality flag caps the overall "
                    "score at 55 regardless of category scores (see Red Flags).")
        overall = 55.0

    coverage_avg = sum(c["coverage"] * c["weight"] for c in categories.values())
    confidence = "high" if coverage_avg >= 0.8 else "medium" if coverage_avg >= 0.5 else "low"

    return {"lens": lens, "weights": weights, "categories": categories,
            "overall": overall, "cap_note": cap_note,
            "coverage": round(coverage_avg, 2), "confidence": confidence}
