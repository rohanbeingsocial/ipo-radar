"""Rebuild the 20-year expanded IPO workbook from the canonical CSVs in data/.

Output: ipodata/finalipodata_expanded_20yr.xlsx (sheets Expanded / Original /
ReadMe). The user's original 125-row workbook is never modified.
"""
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
IPODATA = ROOT / "ipodata"
REPORTS = ROOT / "docs" / "data" / "reports"
ORIG = IPODATA / "finalipodata (6).xlsx"
OUT = IPODATA / "finalipodata_expanded_20yr.xlsx"

# the 14 analysis columns, and where each is sourced automatically
TARGET_COLS = ["Sector", "GMP", "Estimated Price", "ROE", "ROCE", "D/E", "PAT Margin",
               "P/B", "Post IPO P/E", "EBITA Margin", "% QIB", "% Retail", "% anchor", "LTP Gain"]
# report fundamentals (fractions) -> (column, scale-to-sheet-units)
FUND_MAP = {"roe": ("ROE", 100), "roce": ("ROCE", 100), "debt_equity": ("D/E", 1),
            "pat_margin": ("PAT Margin", 100), "ebitda_margin": ("EBITA Margin", 100),
            "post_ipo_pe": ("Post IPO P/E", 1)}


def norm(name):
    s = re.sub(r"[^a-z0-9 ]", " ", str(name).lower())
    drop = {"limited", "ltd", "private", "pvt", "the", "and", "of", "india"}
    return " ".join(w for w in s.split() if w and w not in drop)


def num(x):
    if pd.isna(x):
        return np.nan
    try:
        v = float(str(x).replace(",", "").replace("%", "").strip())
        return v if np.isfinite(v) else np.nan
    except ValueError:
        return np.nan


def id_str(s):
    return s.map(lambda v: "" if pd.isna(v)
                 else str(int(float(v))) if str(v).replace(".", "").isdigit() else str(v))


def sid(v):
    try:
        return str(int(float(v)))
    except (ValueError, TypeError):
        return "" if v is None else str(v).strip()


def auto_enrichment(ids):
    """Populate the 14 columns per cg_ipo_id from the automated sources, in priority
    order: Chittorgarh's published KPI block (the same figures the source sheet was
    filled from by hand) -> the analyzer's own RHP-derived ratios for any gaps ->
    Yahoo outcomes (LTP) and the GMP feed. Hand-sheet is the final fallback."""
    ids = set(ids)
    out = {i: {} for i in ids}

    def _pull(csv_path, mapping):
        if not csv_path.exists():
            return
        df = pd.read_csv(csv_path)
        if "cg_ipo_id" not in df.columns:
            return
        for _, r in df.iterrows():
            i = sid(r.get("cg_ipo_id"))
            if i not in ids:
                continue
            for src, col in mapping.items():
                v = r.get(src)
                if pd.notna(v) and v != "" and out[i].get(col) is None:
                    out[i][col] = num(v) if col != "Sector" else str(v)

    # 0. Sector first, from the dedicated backfill (Yahoo GICS -> RHP bucket -> company
    #    name). It reaches ~77% of rows where the detail-page sector reached ~36%, and
    #    sector turns out to be the strongest signal in the dataset — the median 24-month
    #    return spans 62pp between Technology and Real Estate.
    _pull(DATA / "cg_sector.csv", {"sector": "Sector"})

    # 1. Chittorgarh detail page — reserved split and the KPI block (and sector for any
    #    row the backfill missed). Already in sheet units, so no scaling.
    _pull(DATA / "cg_details.csv", {
        "sector": "Sector", "pct_qib": "% QIB", "pct_retail": "% Retail",
        "pct_anchor": "% anchor", "kpi_roe": "ROE", "kpi_roce": "ROCE",
        "kpi_debt_equity": "D/E", "kpi_pat_margin": "PAT Margin",
        "kpi_ebitda_margin": "EBITA Margin", "kpi_pb": "P/B",
        "kpi_post_ipo_pe": "Post IPO P/E"})

    # 2. the analyzer's RHP-derived ratios fill whatever Chittorgarh didn't publish
    #    (these are fractions, hence the scale)
    for i in ids:
        rp = REPORTS / f"{i}.json"
        if not rp.exists():
            continue
        try:
            fund = (json.loads(rp.read_text(encoding="utf-8")) or {}).get("fundamentals") or {}
        except (ValueError, OSError):
            continue
        for k, (col, scale) in FUND_MAP.items():
            if fund.get(k) is not None and out[i].get(col) is None:
                out[i][col] = round(float(fund[k]) * scale, 2)

    _pull(DATA / "ipo_outcomes.csv", {"current_ret": "LTP Gain"})
    _pull(DATA / "cg_gmp.csv", {"gmp": "GMP", "estimated_price": "Estimated Price"})
    return out


def build():
    issue = pd.read_csv(DATA / "cg_issue.csv")
    subs = pd.read_csv(DATA / "cg_subs.csv")
    lst = pd.read_csv(DATA / "cg_listing.csv")
    lst.columns = [c.split("<")[0].strip() for c in lst.columns]
    outc_p = DATA / "ipo_outcomes.csv"
    outc = pd.read_csv(outc_p) if outc_p.exists() else pd.DataFrame()

    issue["cg_ipo_id"] = id_str(issue["cg_ipo_id"])
    subs["cg_ipo_id"] = id_str(subs["cg_ipo_id"])
    lst["cg_ipo_id"] = id_str(lst["cg_ipo_id"] if lst["cg_ipo_id"].notna().any() else lst["id"])

    base = issue[["cg_ipo_id", "company", "detail_url", "Pricing Method", "Opening Date",
                  "Closing Date", "Listing Date", "Issue Price (Rs.)",
                  "Total Issue Amount (Incl.Firm reservations) (Rs.cr.)",
                  "Fresh Capital (Rs.cr.)", "Offer for sale (Rs.cr.)",
                  "Left Lead Manager"]].copy()
    base.columns = ["cg_ipo_id", "Name", "Chittorgarh URL", "Pricing", "Apply Date",
                    "Close Date", "List Date", "Offer Price", "Issue Size (cr)",
                    "Fresh (cr)", "OFS (cr)", "Lead Manager"]

    s = subs[["cg_ipo_id", "QIB (x)", "bNII (x)", "sNII (x)", "NII (x)", "Retail (x)",
              "Employee (x)", "Total (x)", "Applications"]].rename(columns={
        "QIB (x)": "QIB", "bNII (x)": "bNII", "sNII (x)": "sNII", "NII (x)": "NII",
        "Retail (x)": "Retail", "Employee (x)": "Employee", "Total (x)": "Total Sub"})
    base = base.merge(s.drop_duplicates("cg_ipo_id"), on="cg_ipo_id", how="left")

    l2 = lst[["cg_ipo_id", "ISIN", "NSE Symbol", "BSE Scrip Code",
              "Close Price on Listing (Rs.)",
              "% Gain/Loss (Issue price v/s close price on Listing)"]].rename(columns={
        "Close Price on Listing (Rs.)": "Day1 Close",
        "% Gain/Loss (Issue price v/s close price on Listing)": "Listing Gain"})
    base = base.merge(l2.drop_duplicates("cg_ipo_id"), on="cg_ipo_id", how="left")

    if len(outc):
        outc["cg_ipo_id"] = id_str(outc["cg_ipo_id"])
        oc = outc.drop_duplicates("cg_ipo_id")[
            [c for c in ("cg_ipo_id", "ret_6m", "ret_12m", "ret_24m", "bottom_12m_pct",
                         "sessions_to_bottom", "peak_24m_pct", "sessions_to_peak",
                         "current_ret", "delisted") if c in outc.columns]]
        oc = oc.rename(columns={"ret_6m": "Ret 6m %", "ret_12m": "Ret 12m %",
                                "ret_24m": "Ret 24m %", "bottom_12m_pct": "Bottom 12m %",
                                "sessions_to_bottom": "Sessions to Bottom",
                                "peak_24m_pct": "Peak 24m %", "sessions_to_peak": "Sessions to Peak",
                                "current_ret": "Current Ret %", "delisted": "Delisted"})
        base = base.merge(oc, on="cg_ipo_id", how="left")

    # automated sources first (keyed by cg_ipo_id); the hand-sheet only fills gaps
    auto = auto_enrichment(list(base["cg_ipo_id"]))
    for col in TARGET_COLS:
        base[col] = [auto.get(i, {}).get(col) for i in base["cg_ipo_id"]]

    orig = pd.read_excel(ORIG, sheet_name="Sheet1")
    orig.columns = [str(c).strip() for c in orig.columns]
    orig = orig[orig["Name"].notna()].copy()
    fb_cols = [c for c in TARGET_COLS if c in orig.columns]
    orig_x = orig[["Name"] + fb_cols].copy()
    orig_x["key"] = orig_x["Name"].map(norm)
    orig_x = orig_x.drop_duplicates("key").drop(columns=["Name"]).set_index("key")
    base["key"] = base["Name"].map(norm)
    for col in fb_cols:
        fb = base["key"].map(orig_x[col])
        base[col] = [a if (a is not None and pd.notna(a)) else b
                     for a, b in zip(base[col], fb)]
    base = base.drop(columns=["key"])
    for col in TARGET_COLS:                          # numeric cols to numbers, Sector stays text
        if col != "Sector":
            base[col] = pd.to_numeric(base[col], errors="coerce")

    for c in ["Offer Price", "Issue Size (cr)", "Fresh (cr)", "OFS (cr)", "QIB", "bNII",
              "sNII", "NII", "Retail", "Employee", "Total Sub", "Day1 Close", "Listing Gain"]:
        base[c] = base[c].map(num)

    # GMP = Estimated Price - Offer Price. The source sheet's GMP column is corrupted
    # (a trailing digit: Waaree 1295 -> 12955, Hyundai 62 -> 621) and its own Offer
    # Price column is row-misaligned, but Estimated Price is sound and Chittorgarh's
    # offer price is authoritative — so deriving is both a repair and a backfill.
    # Live rows (GMP straight from the GMP feed) satisfy Est = Offer + GMP already,
    # so this reproduces them unchanged.
    derived = (pd.to_numeric(base["Estimated Price"], errors="coerce")
               - pd.to_numeric(base["Offer Price"], errors="coerce")).round(2)
    base["GMP"] = derived.where(derived.notna(), pd.to_numeric(base["GMP"], errors="coerce"))

    base["_dt"] = pd.to_datetime(base["List Date"], format="%d-%b-%Y", errors="coerce")
    base = base.sort_values("_dt", ascending=False).drop(columns=["_dt"])

    readme = pd.DataFrame({"Field": [
        "Coverage", "Sources", "QIB/bNII/sNII/Retail", "Listing Gain", "Ret 6m/12m/24m %",
        "Bottom 12m % / Sessions to Bottom", "Peak 24m %", "ROE/ROCE/D-E/margins/Post-IPO P/E",
        "Sector", "% QIB / % Retail / % anchor", "LTP Gain", "GMP / Estimated Price", "Caveat"],
        "Notes": [
            "All NSE/BSE mainboard IPOs Feb-2005 onward (Chittorgarh archive); auto-updated daily.",
            "chittorgarh.com cloud reports 82/21/25 + detail pages; Yahoo Finance; RHP analysis; ipowatch GMP.",
            "Final subscription multiples (x).",
            "Listing-day close vs offer price, % (Chittorgarh; close, not open).",
            "Close vs offer price at 6/12/24 months after listing (Yahoo; survivors only).",
            "Lowest close within 12m vs offer = optimal entry; trading sessions to reach it.",
            "Highest close within 24m AFTER the bottom, vs offer = exit marker.",
            "Computed from the RHP's restated financials by the analyzer (latest fiscal year); "
            "only for IPOs with an analyzed RHP. Hand-sheet fills any gaps.",
            "Yahoo assetProfile sector for the listed symbol (GICS-style taxonomy).",
            "Reserved offer split from the Chittorgarh issue-structure table; anchor = 60% of the QIB portion.",
            "Current price vs offer price, % (= Current Ret %, Yahoo).",
            "Grey-market premium is live-only and third-party (ipowatch); captured for currently-open "
            "IPOs and NOT recoverable for older ones. Estimated Price = Offer + GMP.",
            "Delisted companies lack Yahoo horizons -> long-horizon columns skew to survivors.",
        ]})

    with pd.ExcelWriter(OUT, engine="openpyxl") as xw:
        base.to_excel(xw, sheet_name="Expanded", index=False)
        orig.to_excel(xw, sheet_name="Original", index=False)
        readme.to_excel(xw, sheet_name="ReadMe", index=False)
    print(f"  excel: {len(base)} rows -> {OUT.name}")


if __name__ == "__main__":
    build()
