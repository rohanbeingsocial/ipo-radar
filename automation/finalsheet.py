"""Build ipodata/finalipodata.xlsx in the exact layout of the hand-built sheet.

The hand-built workbook (finalipodata (7).xlsx) has 47 columns on "Sheet1" for
125 IPOs of Jan 2024 - Jul 2025, and a "Cleaned" sheet derived from it. This
rebuilds both, same headers in the same order, for every mainboard IPO the
pipeline tracks (2005 onward), and refreshes daily.

Where each column comes from (first source that has a value wins):
  dates, offer, listing open/close, subscription   Chittorgarh reports (cg_*.csv)
  face value, lot size, shares offered, KPIs        Chittorgarh IPO page record (cg_records.json)
  ratios the page's KPI block lacks (pre-2022)      recomputed from the page's restated
                                                    financials + pre/post-issue share counts
  ROCE / EBITDA margin / D-E still missing          the IPO's RHP, via this repo's analyzer
  Nifty 50 / prev close / change / monthly          nifty.py (Dhan 10:00 level, else day open)
  Sector                                            Chittorgarh industry -> the sheet's 12 sectors
  GMP / Estimated Price                             grey-market capture while open (cg_gmp.csv),
                                                    the hand sheet's Estimated Price for its rows
  LTP                                               Dhan last price, else Chittorgarh current price

A value that was computed rather than published is listed per IPO on the
"Sources" sheet, so nothing derived passes as reported.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nifty  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
REPORTS = ROOT / "docs" / "data" / "reports"
OUT_XLSX = ROOT / "ipodata" / "finalipodata.xlsx"
OUT_CSV = ROOT / "ipodata" / "finalipodata.csv"
HAND_SEED = DATA / "hand_sheet_seed.csv"
LTP_CSV = DATA / "ltp.csv"

SHEET1 = ["Apply Date", "List Date", "Nifty 50", "Nifty 50 price of Prev Closing", " Change ",
          "NIfty 50 monthly", "Sector", "Name", "Cap", "Face Value", "Offer Price", "GMP",
          "Estimated Price", "Listing Price", "Listing Gain", "Closing Price", "Closing Gain(LND)",
          "LTP", "LTP Gain", "QIB", "bNII", "sNII", "Retail", "Net Worth", "EBITA Margin", "ROE",
          "ROCE", "D/E", "PAT Margin", "RoNW", "P/B", "Total Shares Offered", "QIB Shares Offered",
          "bNII Shares Offered", "sNII Shares Offered", "Retail Shares Offered",
          "Anchor Shares Offered", "Lot size", "Pre IPO EPs", "Pre IPO P/E", "Post IPO Eps",
          "Post IPO P/E", "% QIB", "% bNII", "% sNII", "% Retail", "% anchor"]
CLEANED = ["Apply Date", "List Date", " Change ", "NIfty 50 monthly", "Sector", "Name", "Cap",
           "Face Value", "Offer Price", "GMP", "Estimated Price", "Listing Price", "Listing Gain",
           "Closing Price", "Closing Gain", "QIB", "bNII", "sNII", "Retail", "Net Worth",
           "EBITA Margin", "ROE", "ROCE", "D/E", "PAT Margin", "RoNW", "P/B", "Pre IPO EPs",
           "Pre IPO P/E", "Post IPO Eps", "Post IPO P/E", "% QIB", "% bNII", "% sNII", "% Retail",
           "% Anchor"]
MOOD_CODE = {"High": 1, "Neutral": 0, "Low": -1}

# Market-cap bands (post-issue shares x offer price, Rs crore), the best fixed cut-offs
# for the hand sheet's labels (101/125 agree; its labels follow no single rule, e.g.
# NTPC Green at Rs 91k cr is "Small Cap"). The sheet's own label wins for its rows.
CAP_LARGE_CR = 60000
CAP_MID_CR = 8500

# Chittorgarh (NSE basic-industry) code -> the sheet's 12 sectors. Ranges follow
# NSE's macro-sector grouping; single codes are the hand sheet's own choices.
SECTOR_RANGES = [
    ((14, 22), "Materials"), ((23, 24), "Agriculture"), ((25, 41), "Materials"),
    ((42, 48), "Auto & Auto Ancillaries"), ((49, 64), "Consumer Discretionary"),
    ((65, 67), "Manufacturing"), ((68, 76), "Services"), ((77, 79), "Real Estate"),
    ((80, 88), "Services"), ((89, 94), "Consumer Discretionary"), ((95, 105), "Energy"),
    ((106, 109), "Agriculture"), ((110, 122), "Consumer Discretionary"),
    ((123, 145), "Financials"), ((146, 151), "Healthcare"), ((152, 174), "Industrials"),
    ((175, 178), "Technology"), ((179, 195), "Services"), ((196, 199), "Technology"),
    ((200, 204), "Energy"), ((205, 209), "Services"), ((210, 210), "Industrials"),
]
SECTOR_OVERRIDES: dict[int, str] = {}   # filled from the hand sheet by fit_sector_overrides()

BADGE = re.compile(r"\s+(?:O|P|CT|LT|U|A|C|L|OT)\s*$")


def clean_name(s):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return BADGE.sub("", s)


def num(v):
    if v is None:
        return np.nan
    if isinstance(v, (int, float, np.floating, np.integer)):
        return float(v)
    s = str(v).replace(",", "").replace("%", "").replace("₹", "").replace("−", "-").strip()
    s = re.sub(r"(?i)\s*cr\.?$", "", s)
    try:
        return float(s)
    except ValueError:
        return np.nan


def fin(v):
    return v is not None and not (isinstance(v, float) and np.isnan(v))


def r2(v):
    return round(float(v), 2) if fin(v) else None


def sid(v):
    try:
        return str(int(float(v)))
    except (TypeError, ValueError):
        return str(v)


def sector_for(code) -> str | None:
    try:
        c = int(code)
    except (TypeError, ValueError):
        return None
    if c in SECTOR_OVERRIDES:
        return SECTOR_OVERRIDES[c]
    for (a, b), name in SECTOR_RANGES:
        if a <= c <= b:
            return name
    return None


def cap_for(mcap_cr) -> str | None:
    if not fin(mcap_cr):
        return None
    return "Large Cap" if mcap_cr >= CAP_LARGE_CR else "Mid Cap" if mcap_cr >= CAP_MID_CR \
        else "Small Cap"


# ─────────────────────────── ratio recomputation ───────────────────────────

def _latest_full_year(f: dict):
    """Index of the latest period that is a full financial year (a stub like
    '30 Nov 2005' would understate EPS and margins), plus the one before it."""
    periods = f.get("periods") or []
    for i, p in enumerate(periods):
        if re.match(r"(?i)\s*31\s*mar", p):
            return i, (i + 1 if i + 1 < len(periods) else None)
    return (0, 1 if len(periods) > 1 else None) if periods else (None, None)


def _row(f: dict, *names):
    rows = f.get("rows") or {}
    for want in names:
        for k, v in rows.items():
            if k.strip().lower() == want:
                return v
    return None


def derived_ratios(rec: dict, offer) -> dict:
    """KPIs recomputed from the page's restated financials (Rs crore) and share counts,
    using the same definitions as Chittorgarh's own KPI block (checked on Shanti Gold:
    EPS 10.34 / 7.75, P/E 19.24 / 25.69, NAV 28.22, P/B 7.05, RoNW 44.8 on average NW)."""
    f = rec.get("financials") or {}
    i, j = _latest_full_year(f)
    if i is None:
        return {}

    def at(vals, k):
        return vals[k] if vals and k is not None and k < len(vals) and vals[k] is not None \
            else np.nan

    income = at(_row(f, "total income", "revenue"), i)
    pat = at(_row(f, "profit after tax"), i)
    ebitda = at(_row(f, "ebitda"), i)
    nw = at(_row(f, "net worth"), i)
    nw_prev = at(_row(f, "net worth"), j)
    debt = at(_row(f, "total borrowing"), i)
    pre = num(rec.get("total_shareholding_pre_issue"))
    post = num(rec.get("total_shareholding_post_issue"))
    out = {}
    nw_latest = at(_row(f, "net worth"), 0)     # a balance-sheet figure: newest period, stub or not
    if fin(nw_latest):
        out["net_worth"] = nw_latest
    if fin(pat) and fin(income) and income > 0:
        out["pat_margin"] = pat / income * 100
    if fin(ebitda) and fin(income) and income > 0:
        out["ebitda_margin"] = ebitda / income * 100
    if fin(debt) and fin(nw) and nw > 0:
        out["debt_equity"] = debt / nw
    if fin(pat) and fin(nw) and nw > 0:
        avg = (nw + nw_prev) / 2 if fin(nw_prev) and nw_prev > 0 else nw
        out["ronw"] = pat / avg * 100
    if fin(pat) and fin(pre) and pre > 0:
        out["eps_pre"] = pat * 1e7 / pre
    if fin(pat) and fin(post) and post > 0:
        out["eps_post"] = pat * 1e7 / post
    if fin(offer):
        for k_eps, k_pe in (("eps_pre", "pe_pre"), ("eps_post", "pe_post")):
            if fin(out.get(k_eps)) and out[k_eps] != 0:
                out[k_pe] = offer / out[k_eps]
        if fin(nw) and fin(pre) and pre > 0 and nw > 0:
            out["pb"] = offer / (nw * 1e7 / pre)
    return out


def rhp_fundamentals(cid: str) -> dict:
    """ROCE / margins / D-E from this repo's RHP analysis, with sanity bounds (the
    extractor occasionally mixes units across statement pages)."""
    p = REPORTS / f"{cid}.json"
    if not p.exists():
        return {}
    fu = (json.loads(p.read_text(encoding="utf-8")).get("fundamentals") or {})
    bounds = {"roce": (-1.0, 1.5), "roe": (-1.0, 1.5), "ebitda_margin": (-1.0, 1.0),
              "pat_margin": (-1.0, 1.0), "debt_equity": (0.0, 20.0)}
    out = {}
    for k, (lo, hi) in bounds.items():
        v = num(fu.get(k))
        if fin(v) and lo <= v <= hi:
            out[k] = v * 100 if k != "debt_equity" else v
    return out


# ─────────────────────────── build ───────────────────────────

def _read(name, **kw):
    p = DATA / name
    return pd.read_csv(p, **kw) if p.exists() else pd.DataFrame()


def load_inputs():
    issue = _read("cg_issue.csv", dtype={"cg_ipo_id": str})
    subs = _read("cg_subs.csv", dtype={"cg_ipo_id": str})
    listing = _read("cg_listing.csv", dtype={"cg_ipo_id": str})
    gmp = _read("cg_gmp.csv", dtype={"cg_ipo_id": str})
    ig = _read("ig_gmp.csv", dtype={"cg_ipo_id": str})
    ltp = _read("ltp.csv", dtype={"cg_ipo_id": str})
    hand = _read(HAND_SEED.name, dtype={"cg_ipo_id": str})
    recs = json.loads((DATA / "cg_records.json").read_text(encoding="utf-8")) \
        if (DATA / "cg_records.json").exists() else {}
    for df in (issue, subs, listing, gmp, ig, ltp, hand):
        if len(df) and "cg_ipo_id" in df:
            df["cg_ipo_id"] = df["cg_ipo_id"].map(sid)
    return issue, subs, listing, gmp, ig, ltp, hand, recs


def fit_sector_overrides(hand: pd.DataFrame, recs: dict):
    """Where the hand sheet labelled an industry consistently, use its label."""
    votes: dict[int, dict[str, int]] = {}
    for _, h in hand.iterrows():
        code = (recs.get(h["cg_ipo_id"]) or {}).get("ipo_industry")
        if code is None or not isinstance(h.get("sector"), str):
            continue
        votes.setdefault(int(code), {}).setdefault(h["sector"], 0)
        votes[int(code)][h["sector"]] += 1
    for code, v in votes.items():
        label, n = max(v.items(), key=lambda kv: kv[1])
        if n > sum(v.values()) / 2:
            SECTOR_OVERRIDES[code] = label


def pdt(s):
    return pd.to_datetime(s, format="%d-%b-%Y", errors="coerce")


def build(log=print):
    issue, subs, listing, gmp, ig, ltp, hand, recs = load_inputs()
    if len(hand):
        fit_sector_overrides(hand, recs)
    by = lambda df: {r["cg_ipo_id"]: r for _, r in df.iterrows()} if len(df) else {}  # noqa: E731
    subs_by, lst_by, gmp_by, ig_by, ltp_by, hand_by = map(by, (subs, listing, gmp, ig, ltp, hand))

    daily = nifty.load_daily()
    at10 = nifty.load_listing()
    lst_cols = {c.split("<")[0].strip(): c for c in listing.columns}

    rows, sources = [], []
    for _, r in issue.iterrows():
        cid = r["cg_ipo_id"]
        rec = recs.get(cid, {})
        s = subs_by.get(cid, {})
        L = lst_by.get(cid, {})
        g = gmp_by.get(cid, {})
        h = hand_by.get(cid, {})
        derived: list[str] = []

        apply_dt = pdt(r.get("Opening Date"))
        list_dt = pdt(r.get("Listing Date"))
        if pd.isna(list_dt) and L is not None and len(L):
            list_dt = pdt(L.get("Listing Date"))
        offer = num(r.get("Issue Price (Rs.)"))
        if not fin(offer):
            offer = num(rec.get("issue_price_final"))

        mk = nifty.listing_row(list_dt, daily, at10) if pd.notna(list_dt) else {}
        if mk.get("nifty_source") in ("daily_open", "daily_close"):
            derived.append("Nifty 50=day " + mk["nifty_source"].split("_")[1])

        # sector: the hand sheet's own label where it exists, else the industry mapping
        sector = h.get("sector") if isinstance(h.get("sector"), str) else \
            sector_for(rec.get("ipo_industry"))

        post_sh = num(rec.get("total_shareholding_post_issue"))
        mcap = post_sh * offer / 1e7 if fin(post_sh) and fin(offer) else np.nan

        # GMP: the hand sheet's pre-listing reading for its rows; else InvestorGain's
        # final pre-listing GMP (2019 on; it matches 117/121 of the hand sheet's
        # Estimated Price); else today's ipowatch capture for issues still open
        ig_row = ig_by.get(cid, {})
        gmp_v, est = np.nan, np.nan
        if len(h) and fin(num(h.get("estimated_price"))):
            est = num(h.get("estimated_price"))
            gmp_v = est - offer if fin(offer) else np.nan
            derived.append("GMP/Estimated=hand sheet")
        elif len(ig_row) and fin(num(ig_row.get("gmp"))):
            gmp_v = num(ig_row.get("gmp"))
            est = offer + gmp_v if fin(offer) else num(ig_row.get("est_price"))
            derived.append("GMP=InvestorGain")
        elif len(g) and fin(num(g.get("gmp"))):
            gmp_v = num(g.get("gmp"))
            est = offer + gmp_v if fin(offer) else np.nan
            derived.append("GMP=ipowatch while open")

        # listing day: NSE's print where it listed there (the sheet's convention), else BSE
        ld = rec.get("listing_day") or {}
        day1 = ld.get("NSE") or ld.get("BSE") or {}
        open_px = num(day1.get("open"))
        if not fin(open_px) and len(L):
            open_px = num(L.get(lst_cols.get("Open Price on Listing (Rs.)", "")))
        close_px = num(day1.get("close"))
        if not fin(close_px):
            close_px = num(rec.get("listing_day_closing_price"))
        if not fin(close_px) and len(L):
            close_px = num(L.get(lst_cols.get("Close Price on Listing (Rs.)", "")))
        ltp_px = num(ltp_by[cid].get("ltp")) if cid in ltp_by else np.nan
        if not fin(ltp_px) and len(L):
            for col in ("Current Price <br>at NSE (Rs.)", "Current Price <br>at BSE (Rs.)", "Current Price"):
                if fin(num(L.get(col))):
                    ltp_px = num(L.get(col))
                    derived.append("LTP=Chittorgarh (may be stale)")
                    break
        pct = lambda px: (px / offer - 1) * 100 if fin(px) and fin(offer) and offer else np.nan  # noqa: E731

        # KPIs: published block first, then recomputed, then the RHP
        d = derived_ratios(rec, offer)
        rhp = rhp_fundamentals(cid)

        def pick(label, *cands):
            for src, v in cands:
                if fin(v):
                    if src != "cg":
                        derived.append(f"{label}={src}")
                    return v
            return np.nan

        ronw = pick("RoNW", ("cg", num(rec.get("kpi_ronw"))), ("calc", d.get("ronw")),
                    ("rhp", rhp.get("roe")))
        roe = pick("ROE", ("cg", num(rec.get("kpi_roe"))), ("cg", num(rec.get("kpi_ronw"))),
                   ("calc", d.get("ronw")), ("rhp", rhp.get("roe")))
        tot = num(rec.get("shares_offered_total"))
        share = lambda k: num(rec.get(k))  # noqa: E731
        qib_sh, anc_sh = share("shares_offered_qib_with_anchor"), share("shares_offered_anchor_investor")
        bnii_sh, snii_sh = share("shares_offered_big_nii"), share("shares_offered_small_nii")
        rii_sh = share("shares_offered_rii")
        of_tot = lambda v: v / tot * 100 if fin(v) and fin(tot) and tot else np.nan  # noqa: E731
        sub = lambda k: num(s.get(k)) if len(s) else np.nan  # noqa: E731

        row = {
            "Apply Date": apply_dt, "List Date": list_dt,
            "Nifty 50": mk.get("nifty"), "Nifty 50 price of Prev Closing": mk.get("prev_close"),
            " Change ": mk.get("change"), "NIfty 50 monthly": mk.get("mood"),
            "Sector": sector, "Name": clean_name(rec.get("company_name") or r.get("company")),
            "Cap": h.get("cap") if isinstance(h.get("cap"), str) else cap_for(mcap),
            "Face Value": num(rec.get("face_value")), "Offer Price": offer,
            "GMP": gmp_v, "Estimated Price": est,
            "Listing Price": open_px, "Listing Gain": pct(open_px),
            "Closing Price": close_px, "Closing Gain(LND)": pct(close_px),
            "LTP": ltp_px, "LTP Gain": pct(ltp_px),
            "QIB": sub("QIB (x)"), "bNII": sub("bNII (x)"), "sNII": sub("sNII (x)"),
            "Retail": sub("Retail (x)"),
            "Net Worth": pick("Net Worth", ("cg", d.get("net_worth"))),
            "EBITA Margin": pick("EBITA Margin", ("cg", num(rec.get("kpi_ebitda"))),
                                 ("calc", d.get("ebitda_margin")), ("rhp", rhp.get("ebitda_margin"))),
            "ROE": roe,
            "ROCE": pick("ROCE", ("cg", num(rec.get("kpi_roce"))), ("rhp", rhp.get("roce"))),
            "D/E": pick("D/E", ("cg", num(rec.get("kpi_debt_equity"))), ("calc", d.get("debt_equity")),
                        ("rhp", rhp.get("debt_equity"))),
            "PAT Margin": pick("PAT Margin", ("cg", num(rec.get("kpi_pat_margin"))),
                               ("calc", d.get("pat_margin")), ("rhp", rhp.get("pat_margin"))),
            "RoNW": ronw,
            "P/B": pick("P/B", ("cg", num(rec.get("price_to_book_value"))), ("calc", d.get("pb"))),
            "Total Shares Offered": tot, "QIB Shares Offered": qib_sh,
            "bNII Shares Offered": bnii_sh, "sNII Shares Offered": snii_sh,
            "Retail Shares Offered": rii_sh, "Anchor Shares Offered": anc_sh,
            "Lot size": num(rec.get("market_lot_size")),
            "Pre IPO EPs": pick("Pre IPO EPs", ("cg", num(rec.get("kpi_eps"))), ("calc", d.get("eps_pre"))),
            "Pre IPO P/E": pick("Pre IPO P/E", ("cg", num(rec.get("pe_ratio"))), ("calc", d.get("pe_pre"))),
            "Post IPO Eps": pick("Post IPO Eps", ("cg", num(rec.get("kpi_eps_post"))),
                                 ("calc", d.get("eps_post"))),
            "Post IPO P/E": pick("Post IPO P/E", ("cg", num(rec.get("post_pe_ratio"))),
                                 ("calc", d.get("pe_post"))),
            "% QIB": of_tot(qib_sh), "% bNII": of_tot(bnii_sh), "% sNII": of_tot(snii_sh),
            "% Retail": of_tot(rii_sh), "% anchor": of_tot(anc_sh),
        }
        for k, v in row.items():
            if isinstance(v, float) and k not in ("Apply Date", "List Date"):
                row[k] = None if np.isnan(v) else (round(v, 2) if "Shares" not in k else round(v))
        rows.append(row)
        sources.append({"cg_ipo_id": cid, "Name": row["Name"], "Chittorgarh URL": r.get("detail_url"),
                        "Computed or non-Chittorgarh fields": "; ".join(derived)})

    df = pd.DataFrame(rows, columns=SHEET1)
    order = df["Apply Date"].fillna(pd.Timestamp("1900-01-01")).argsort(kind="stable")[::-1]
    df = df.iloc[order].reset_index(drop=True)
    src = pd.DataFrame(sources).iloc[order].reset_index(drop=True)

    cleaned = df[df["Listing Price"].notna()].copy()
    cleaned = cleaned.rename(columns={"Closing Gain(LND)": "Closing Gain", "% anchor": "% Anchor"})
    cleaned["NIfty 50 monthly"] = cleaned["NIfty 50 monthly"].map(MOOD_CODE)
    cleaned = cleaned[CLEANED]

    write(df, cleaned, src)
    filled = {c: int(df[c].notna().sum()) for c in SHEET1}
    log(f"  final sheet: {len(df)} IPOs, {len(cleaned)} listed -> {OUT_XLSX.name}")
    return df, cleaned, src, filled


README_ROWS = [
    ("Sheet1", "One row per NSE/BSE mainboard IPO since 2005, newest first. Same 47 columns, in "
               "the same order, as the hand-built finalipodata sheet."),
    ("Cleaned", "Listed IPOs only, the 36 modelling columns; NIfty 50 monthly coded High=1, "
                "Neutral=0, Low=-1. Blanks are left blank (not 0)."),
    ("Sources", "Per IPO, every value that was computed or taken from a non-Chittorgarh source."),
    ("Apply Date / List Date", "Issue opening date and listing date (Chittorgarh)."),
    ("Nifty 50", "NIFTY 50 at 10:00 IST on the listing day (close of the 10:00 one-minute candle, "
                 "Dhan; available from ~2018). Earlier: the day's open, or its close before 2010 when "
                 "NSE printed the open as the previous close (see Sources)."),
    ("Nifty 50 price of Prev Closing", "NIFTY 50 close on the session before listing (NSE's "
                                       "official index history)."),
    (" Change ", "Nifty 50 vs previous close, %."),
    ("NIfty 50 monthly", "Where Nifty 50 sits in the last 22 sessions' high-low range: top third "
                         "High, bottom third Low, else Neutral (reproduces 90% of the hand labels)."),
    ("Sector", "The hand sheet's 12 sectors, mapped from Chittorgarh's NSE industry code."),
    ("Cap", f"Post-issue market cap at the offer price: >= Rs {CAP_LARGE_CR:,} cr Large, "
            f">= Rs {CAP_MID_CR:,} cr Mid, else Small."),
    ("GMP / Estimated Price", "Final grey-market premium before listing (InvestorGain's GMP tracker, "
                              "from late 2019; none is published for earlier IPOs), the live "
                              "ipowatch reading for issues still open, and the hand sheet's own "
                              "reading for its rows. Estimated Price = Offer Price + GMP."),
    ("Listing / Closing Price", "Listing-day open and close; gains are vs the offer price, %."),
    ("LTP / LTP Gain", "Last traded price (Dhan, else Chittorgarh) vs offer, %; not adjusted for "
                       "splits or bonuses."),
    ("QIB / bNII / sNII / Retail", "Final subscription, times. bNII/sNII exist only since 2022."),
    ("Net Worth", "Latest restated net worth, Rs crore."),
    ("KPI columns", "Chittorgarh's KPI block where published; otherwise recomputed from the "
                    "restated financials and share counts with the same definitions (RoNW/ROE on "
                    "average net worth, EPS on pre/post-issue shares, P/B on pre-issue NAV); "
                    "ROCE/margins/D-E last from the RHP analysis. Percentages are in %."),
    ("Shares Offered / Lot size / % columns", "From the IPO's reservation table; % columns are "
                                              "each category's share of total shares offered."),
]


def write(df, cleaned, src):
    OUT_XLSX.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl", datetime_format="yyyy-mm-dd",
                        date_format="yyyy-mm-dd") as xw:
        df.to_excel(xw, sheet_name="Sheet1", index=False)
        cleaned.to_excel(xw, sheet_name="Cleaned", index=False)
        src.to_excel(xw, sheet_name="Sources", index=False)
        pd.DataFrame(README_ROWS, columns=["Field", "Notes"]).to_excel(xw, sheet_name="ReadMe",
                                                                        index=False)
        for ws in xw.book.worksheets:
            ws.freeze_panes = "A2"
    out = df.copy()
    for c in ("Apply Date", "List Date"):
        out[c] = out[c].dt.strftime("%Y-%m-%d")
    out.to_csv(OUT_CSV, index=False)


def refresh_ltp(log=print) -> int:
    """Last traded price for every listed IPO from Dhan (NSE where it trades there,
    else BSE) into data/ltp.csv. Prices Dhan no longer returns (delisted, merged)
    keep their last value and date. Returns how many prices were refreshed."""
    import dhan
    if not dhan.token_ok():
        log("  ltp: no valid Dhan token; keeping the last prices")
        return 0
    issue = pd.read_csv(DATA / "cg_issue.csv", dtype={"cg_ipo_id": str})
    recs = json.loads((DATA / "cg_records.json").read_text(encoding="utf-8"))
    ids = dhan.scrip_ids(DATA / "cache" / "dhan-scrip-master.csv")
    want = {}
    for _, r in issue.iterrows():
        cid = sid(r["cg_ipo_id"])
        rec = recs.get(cid, {})
        nse = str(rec.get("il_nse_script_symbol") or r.get("nse_symbol") or "").strip().upper()
        bse = sid(rec.get("il_bse_script_code") or r.get("bse_script_code") or "")
        if nse and ("NSE", nse) in ids:
            want[cid] = ("NSE_EQ", ids[("NSE", nse)])
        elif bse and ("BSE", bse) in ids:
            want[cid] = ("BSE_EQ", ids[("BSE", bse)])
    px = dhan.ltp(list(want.values()))
    old = {sid(r["cg_ipo_id"]): dict(r) for _, r in pd.read_csv(LTP_CSV, dtype={"cg_ipo_id": str}).iterrows()} \
        if LTP_CSV.exists() else {}
    today = str(pd.Timestamp.now(tz="Asia/Kolkata").date())
    got = 0
    for cid, inst in want.items():
        if inst in px:
            old[cid] = {"cg_ipo_id": cid, "ltp": px[inst], "exchange": inst[0][:3], "as_of": today}
            got += 1
    pd.DataFrame(old.values()).sort_values("cg_ipo_id").to_csv(LTP_CSV, index=False)
    log(f"  ltp: {got}/{len(want)} prices from Dhan")
    return got


def seed_from_hand_sheet(xlsx: Path, log=print):
    """One-off: keep what only the hand sheet knows (its Sector labels and the
    Estimated Price it recorded pre-listing) as data/hand_sheet_seed.csv, matched
    to Chittorgarh ids by apply date + name. The sheet's GMP column is not used:
    it has a stray trailing digit (Anthem 179 -> 1794), Estimated Price does not."""
    from update import norm_tokens
    hs = pd.read_excel(xlsx, sheet_name="Sheet1").iloc[:, :47].dropna(subset=["Apply Date", "Name"])
    issue = pd.read_csv(DATA / "cg_issue.csv", dtype={"cg_ipo_id": str})
    issue["open_dt"] = pdt(issue["Opening Date"])
    toks = {cid: set(norm_tokens(clean_name(n))) - {"ipo"} for cid, n in zip(issue.cg_ipo_id, issue.company)}
    out, missed = [], []
    for _, h in hs.iterrows():
        t = set(norm_tokens(h["Name"])) - {"ipo"}
        day = pd.Timestamp(h["Apply Date"]).normalize()
        near = issue[(issue.open_dt - day).abs() <= pd.Timedelta(days=7)]
        best, score = None, 0.0
        for cid in near.cg_ipo_id:
            u = toks[cid]
            s = len(t & u) / max(1, min(len(t), len(u)))
            if s > score:
                best, score = cid, s
        if best is None or score < 0.5:
            missed.append(h["Name"])
            continue
        cap = str(h.get("Cap") or "").replace("‑cap", " Cap").strip() or None
        out.append({"cg_ipo_id": best, "name": h["Name"], "sector": h.get("Sector"), "cap": cap,
                    "estimated_price": num(h.get("Estimated Price")),
                    "hand_offer_price": num(h.get("Offer Price"))})
    pd.DataFrame(out).to_csv(HAND_SEED, index=False)
    log(f"  hand sheet seed: {len(out)} matched, {len(missed)} unmatched {missed}")


if __name__ == "__main__":
    if "--seed" in sys.argv:
        seed_from_hand_sheet(Path(sys.argv[sys.argv.index("--seed") + 1]))
    else:
        build()
