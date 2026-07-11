"""Rebuild the 20-year expanded IPO workbook from the canonical CSVs in data/.

Output: ipodata/finalipodata_expanded_20yr.xlsx (sheets Expanded / Original /
ReadMe). The user's original 125-row workbook is never modified.
"""
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
IPODATA = ROOT / "ipodata"
ORIG = IPODATA / "finalipodata (6).xlsx"
OUT = IPODATA / "finalipodata_expanded_20yr.xlsx"


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

    orig = pd.read_excel(ORIG, sheet_name="Sheet1")
    orig.columns = [str(c).strip() for c in orig.columns]
    orig = orig[orig["Name"].notna()].copy()
    extra_cols = [c for c in ["Sector", "GMP", "Estimated Price", "ROE", "ROCE", "D/E",
                              "PAT Margin", "P/B", "Post IPO P/E", "EBITA Margin",
                              "% QIB", "% Retail", "% anchor", "LTP Gain"] if c in orig.columns]
    orig_x = orig[["Name"] + extra_cols].copy()
    orig_x["key"] = orig_x["Name"].map(norm)
    orig_x = orig_x.drop_duplicates("key").drop(columns=["Name"])
    base["key"] = base["Name"].map(norm)
    base = base.merge(orig_x, on="key", how="left", suffixes=("", "_orig")).drop(columns=["key"])

    for c in ["Offer Price", "Issue Size (cr)", "Fresh (cr)", "OFS (cr)", "QIB", "bNII",
              "sNII", "NII", "Retail", "Employee", "Total Sub", "Day1 Close", "Listing Gain"]:
        base[c] = base[c].map(num)
    base["_dt"] = pd.to_datetime(base["List Date"], format="%d-%b-%Y", errors="coerce")
    base = base.sort_values("_dt", ascending=False).drop(columns=["_dt"])

    readme = pd.DataFrame({"Field": [
        "Coverage", "Sources", "QIB/bNII/sNII/Retail", "Listing Gain", "Ret 6m/12m/24m %",
        "Bottom 12m % / Sessions to Bottom", "Peak 24m %", "GMP/Sector/fundamentals", "Caveat"],
        "Notes": [
            "All NSE/BSE mainboard IPOs Feb-2005 onward (Chittorgarh archive); auto-updated daily.",
            "chittorgarh.com cloud reports 82/21/25; Yahoo Finance charts; user's original sheet.",
            "Final subscription multiples (x).",
            "Listing-day close vs offer price, % (Chittorgarh; close, not open).",
            "Close vs offer price at 6/12/24 months after listing (Yahoo; survivors only).",
            "Lowest close within 12m vs offer = optimal entry; trading sessions to reach it.",
            "Highest close within 24m AFTER the bottom, vs offer = exit marker.",
            "Only for rows in the original 125-IPO sheet (GMP history isn't public for older IPOs).",
            "Delisted companies lack Yahoo horizons -> long-horizon columns skew to survivors.",
        ]})

    with pd.ExcelWriter(OUT, engine="openpyxl") as xw:
        base.to_excel(xw, sheet_name="Expanded", index=False)
        orig.to_excel(xw, sheet_name="Original", index=False)
        readme.to_excel(xw, sheet_name="ReadMe", index=False)
    print(f"  excel: {len(base)} rows -> {OUT.name}")


if __name__ == "__main__":
    build()
