"""End-to-end pipeline test against the synthetic RHP.

Verifies section detection, financial extraction (incl. lakh→crore
normalization), entity extraction, risk analysis, scoring and report assembly.
"""
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tools"))

from app.pipeline import (  # noqa: E402
    entity_extractor as ent,
    financial_extractor as fin,
    forensic,
    pdf_processor,
    promoter_analyzer,
    report_builder,
    risk_analyzer,
    scoring,
    section_extractor,
    valuation,
)

PDF = BACKEND / "sample_data" / "synthetic_rhp.pdf"


@pytest.fixture(scope="module")
def ctx():
    if not PDF.exists():
        from make_synthetic_rhp import build
        PDF.parent.mkdir(parents=True, exist_ok=True)
        build(str(PDF))

    pdf = pdf_processor.process_pdf(str(PDF))
    pages = pdf["pages"]
    sections = section_extractor.extract_sections(pages, pdf["toc"])
    financials = fin.extract_financials(str(PDF), pages, sections)
    issue = ent.extract_issue_details(pages, sections)
    issue["peers_json"] = ent.extract_peers(str(PDF), pages, sections)
    issue["objects_json"] = ent.extract_objects(pages, sections)
    entities = {
        "litigation": ent.extract_litigation(pages, sections),
        "rpt": ent.extract_rpt(pages, sections),
        "contingent": ent.extract_contingent_liabilities(pages, sections),
        "dividend": ent.extract_dividend(pages, sections),
        "pledging": ent.detect_pledging(pages, sections),
        "auditor": ent.detect_auditor_flags(pages, sections),
    }
    risks = risk_analyzer.analyze_risks(pages, sections, financials, entities)
    ratios = valuation.compute_ratios(financials)
    issuer_pe, _ = ent.extract_issuer_pe(pages, sections)
    val = valuation.valuation_call(issuer_pe, issue["peers_json"], ratios)
    for_out = forensic.run_forensics(financials)
    for_out["consistency"] = []
    c = {
        "doc_type": "RHP", "page_count": pdf["page_count"], "pages": pages,
        "readable_ratio": pdf["readable_ratio"], "sections": sections,
        "section_hit_rate": section_extractor.section_hit_rate(sections),
        "financials": financials, "issue": issue, "entities": entities,
        "company_name": ent.extract_company_name(pages),
        "risks": risks, "ratios": ratios, "valuation": val, "forensic": for_out,
        "promoter": promoter_analyzer.analyze_promoters(pages, sections, entities, issue),
    }
    c["scoring"] = scoring.score_all(c)
    return c


def test_sections_found(ctx):
    assert ctx["section_hit_rate"] >= 0.8
    for key in ("risk_factors", "objects_of_offer", "basis_for_offer_price",
                "financial_statements", "litigation", "offer_structure"):
        assert ctx["sections"][key]["found"], f"section {key} not found"


def test_company_name(ctx):
    assert "Acme" in (ctx["company_name"] or "")


def test_financials_normalized_to_crore(ctx):
    f = ctx["financials"]
    assert f["fiscal_order"][:1] == ["FY24"]
    latest = f["series"]["FY24"]
    # 4,52,180 lakhs -> 4,521.8 crore
    assert latest["revenue"] == pytest.approx(4521.8, rel=0.01)
    assert latest["pat"] == pytest.approx(498.7, rel=0.01)
    assert latest["cfo"] == pytest.approx(523.1, rel=0.01)
    assert latest["total_debt"] == pytest.approx(966.0, rel=0.01)
    assert latest["ebitda"] == pytest.approx(911.4, rel=0.01)  # derived


def test_issue_details(ctx):
    issue = ctx["issue"]
    assert issue["price_band_low"] == 315 and issue["price_band_high"] == 332
    assert issue["fresh_issue_cr"] == pytest.approx(600, rel=0.01)
    assert issue["ofs_cr"] == pytest.approx(900, rel=0.01)
    assert issue["lot_size"] == 45
    assert issue["pre_issue_promoter_pct"] == pytest.approx(68.4)
    assert issue["post_issue_promoter_pct"] == pytest.approx(54.2)


def test_peers_and_valuation(ctx):
    peers = ctx["issue"]["peers_json"]
    pes = sorted(p["pe"] for p in peers if p.get("pe"))
    assert pes == pytest.approx([27.9, 31.2, 42.5])
    assert ctx["valuation"]["issuer_pe"] == pytest.approx(38.6)
    assert ctx["valuation"]["call"] in ("fairly_valued_expensive", "overvalued")


def test_objects_classified(ctx):
    cats = {o["category"] for o in ctx["issue"]["objects_json"]}
    assert {"debt_repayment", "capex", "working_capital", "general_corporate"} <= cats


def test_risks(ctx):
    types = {f["risk_type"] for f in ctx["risks"]["findings"]}
    assert "customer_concentration" in types
    cc = next(f for f in ctx["risks"]["findings"] if f["risk_type"] == "customer_concentration")
    assert cc["severity"] == "high" and cc["quantified"]["value"] == pytest.approx(62.4)
    assert "forex_exposure" in types


def test_entities(ctx):
    e = ctx["entities"]
    assert e["litigation"]["counts"].get("criminal") == 2
    assert e["pledging"]["pledged"] is False
    assert e["dividend"]["declared"] is False
    assert e["contingent"]["total_cr"] == pytest.approx(186, rel=0.01)
    assert e["rpt"]["total_cr"] == pytest.approx(124.5, rel=0.01)


def test_scoring_explainable(ctx):
    s = ctx["scoring"]
    assert set(s["categories"]) == set(scoring.WEIGHT_LENSES["balanced"])
    assert 0 <= s["overall"] <= 100
    # every included rule must carry evidence machinery
    for cat in s["categories"].values():
        for rule in cat["rules"]:
            assert "points" in rule and "max_points" in rule and "rationale" in rule
    growth = s["categories"]["growth"]
    assert growth["score"] >= 70  # 23% revenue CAGR + 30% PAT CAGR


def test_report_builds(ctx):
    report = report_builder.build_report(ctx)
    assert report["meta"]["disclaimer"]
    assert len(report["executive_summary"]) >= 3
    assert report["cases"]["bull"] and report["cases"]["bear"]
    assert report["questions"]
    assert any(cell["severity"] != "none" for cell in report["risk"]["heatmap"])


def test_promoters(ctx):
    names = ctx["promoter"]["names"]
    assert "Rajesh Mehta" in names
    assert all("\n" not in n for n in names), "name spanning lines means the regex swallowed a heading"
    assert 30 in ctx["promoter"]["experience_claims"]


def test_text_fallback_borderless_statement():
    """AMC-style layout (e.g. SBI Funds Management RHP): no table ruling
    lines, label on one line with Ind-AS roman-numeral refs, one value line
    per fiscal column underneath."""
    page = """SUMMARY STATEMENT OF PROFIT AND LOSS
   (in ₹ million, unless otherwise specified)
Particulars
For the Financial
Year ended March
31, 2026
For the Financial
Year ended March
31, 2025
For the Financial
Year ended
March 31, 2024
Revenue from operations
Asset management fees
43,894.88
35,977.57
26,905.58
Total revenue from operations (I)
43,894.88
35,977.57
26,905.58
Other income (II)
5,866.18
6,383.94
7,355.21
Total income (III = I+II)
49,761.06
42,361.51
34,260.79
Profit for the year (VIII= V+VI-VII)
21,001.61
18,052.51
13,422.35
Total equity
59,630.62
82,975.33
67,477.47
Total liabilities and equity
64,204.47
87,718.59
71,069.31
"""
    fy = fin._fy_labels_from_text(page)
    assert fy == ["FY26", "FY25", "FY24"]
    rows = dict(fin._rows_from_text(page, fy))
    # heading "Revenue from operations" has no value lines -> the totals row,
    # which also matches the revenue synonym, must supply the figures
    assert rows["revenue"] == [43894.88, 35977.57, 26905.58]
    assert rows["total_income"] == [49761.06, 42361.51, 34260.79]
    assert rows["pat"] == [21001.61, 18052.51, 13422.35]
    assert rows["net_worth"] == [59630.62, 82975.33, 67477.47]
    assert "total_assets" not in rows or rows["total_assets"] != rows["net_worth"]


def test_text_fallback_inline_values_and_dashes():
    page = """RESTATED STATEMENT OF ASSETS AND LIABILITIES
(₹ in crores)
As at March 31, 2025    As at March 31, 2024
Total current assets 1,240.55 980.10
Total borrowings - 150.00
"""
    fy = fin._fy_labels_from_text(page)
    assert fy == ["FY25", "FY24"]
    rows = dict(fin._rows_from_text(page, fy))
    assert rows["current_assets"] == [1240.55, 980.10]
    assert rows["total_debt"] == [None, 150.00]


def test_text_fallback_note_column_and_junk_lines():
    """Alpine-style layout: a bare-integer Notes reference sits between the
    label and the figures (must not shift years), and a stray artifact line
    ("Total A") sits between the PAT label and its values."""
    page = """Annexure II
 For the year ended
31st March, 2026
 For the year ended
31st March, 2025
 For the year ended
31st March, 2024
Income
Revenue from Operations
25
3,427.13
2,373.24
1,836.03
Profit/(Loss) after tax for the year
Total A
217.16
86.26
48.81
"""
    fy = fin._fy_labels_from_text(page)
    assert fy == ["FY26", "FY25", "FY24"]
    rows = dict(fin._rows_from_text(page, fy))
    assert rows["revenue"] == [3427.13, 2373.24, 1836.03], "note ref '25' must be dropped, not treated as FY26"
    assert rows["pat"] == [217.16, 86.26, 48.81]


def test_flow_rows_never_match_balance_metrics():
    assert fin._match_metric("Proceeds from Long Term Borrowings") is None
    assert fin._match_metric("Repayment of current borrowings") is None
    assert fin._match_metric("(Increase)/Decrease in Trade Receivables") is None
    assert fin._match_metric("Net Increase in Cash and Cash Equivalents") is None
    assert fin._match_metric("Net cash flow generated from/(used in) operating activities") == "cfo"
    assert fin._match_metric("(a) Inventories") == "inventory"


def test_bare_borrowings_use_liability_context():
    page = """RESTATED STATEMENT OF ASSETS AND LIABILITIES
As at March 31, 2025   As at March 31, 2024
Non-current Liabilities
(i) Borrowings
2,064.89
2,634.47
Current Liabilities
(i) Borrowings
1,100.00
900.00
"""
    fy = fin._fy_labels_from_text(page)
    rows = fin._rows_from_text(page, fy)
    d = {}
    for k, v in rows:
        d.setdefault(k, v)
    assert d["borrowings_lt"] == [2064.89, 2634.47]
    assert d["borrowings_st"] == [1100.00, 900.00]


def test_extra_column_rows_are_dropped_not_guessed():
    """Aastha-style: proforma column + three fiscal years = 4 value lines for
    3 detected year headers -> the row must be skipped entirely."""
    page = """RESTATED STATEMENT OF ASSETS AND LIABILITIES
March 31, 2025  March 31, 2024  March 31, 2023
Total Equity
15,318.16
12,105.21
7,637.83
6,000.94
"""
    fy = fin._fy_labels_from_text(page)
    assert fy == ["FY25", "FY24", "FY23"]
    rows = dict(fin._rows_from_text(page, fy))
    assert "net_worth" not in rows


def test_stub_period_column_consumed_not_assigned():
    """Aastha-style: a nine-month stub column precedes the fiscal years; its
    values must be consumed so year columns stay aligned, but never stored."""
    page = """RESTATED STATEMENT OF PROFIT & LOSS
(All amounts are in ₹ lakhs)
Particulars
December 31,
2025
March 31,
2025
March 31,
2024
March 31,
2023
Revenue from operations
31,328.50
35,116.02
30,486.16
23,926.50
"""
    fy = fin._fy_labels_from_text(page)
    assert fy == ["STUB25", "FY25", "FY24", "FY23"]
    rows = dict(fin._rows_from_text(page, fy))
    assert rows["revenue"] == [31328.50, 35116.02, 30486.16, 23926.50]


def test_horizon_forecast_degrades_without_model(monkeypatch, tmp_path):
    """No horizon_model.pkl -> engine returns None and forecast() omits it."""
    from app.pipeline import listing_predictor as lp
    monkeypatch.setattr(lp, "HORIZON_MODEL_PATH", tmp_path / "absent.pkl")
    monkeypatch.setattr(lp, "_horizon_cache", {})
    assert lp.horizon_forecast({"scoring": {"overall": 70}}) is None
    out = lp.forecast({"scoring": {"overall": 70, "categories": {}}})
    assert "ml_horizons" not in out
    assert "rules" in out


def test_market_signals_migration_adds_columns(tmp_path):
    """init_db on a pre-expansion market_signals table gains the new columns."""
    from sqlalchemy import create_engine, inspect, text
    url = f"sqlite:///{tmp_path / 'mig.db'}"
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text("CREATE TABLE market_signals (analysis_id VARCHAR PRIMARY KEY, gmp FLOAT)"))
    from app import db as app_db
    app_db._migrate(eng)
    cols = {col["name"] for col in inspect(eng).get_columns("market_signals")}
    assert {"sub_bnii", "sub_snii", "day1_gain"} <= cols
