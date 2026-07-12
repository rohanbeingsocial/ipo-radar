"""Give every IPO a sector — and say what KIND of company it is.

Why this exists: sector was reaching the models for only ~36% of rows (and the RHP
bucket for only 18%), so the sector one-hots were dead weight on most of the training
data. A model cannot know that financials and real estate behave differently if it
cannot see which rows are financials and real estate.

It also captures Yahoo's `industry` (much finer than `sector`: "Banks - Regional",
"Asset Management", "REIT - Office") and derives structural flags from it. Those flags
matter because the SAME NUMBER MEANS DIFFERENT THINGS in different structures:

  * debt/equity of 6 is ordinary for an NBFC and alarming for a manufacturer — leverage
    IS the business model of a lender,
  * P/B is the multiple that prices a bank; P/E is the one that prices a manufacturer,
  * a REIT/InvIT is a yield instrument. Its total return is mostly distributions, which
    a price series does not contain, so judging it on "did the price recover vs offer"
    is measuring the wrong thing entirely. (None are in the dataset today — Chittorgarh
    lists them separately from mainboard IPOs — but the flag is here so that if one ever
    arrives it cannot be silently scored as if it were a growth equity.)

    python automation/backfill_sectors.py          # fill only what is missing
    python automation/backfill_sectors.py --all    # re-fetch everything

Writes data/cg_sector.csv (cg_ipo_id, symbol, yahoo_sector, yahoo_industry, sector,
instrument, is_financial, is_realestate, source).
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.parse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "automation"))
from update import canon_sector, yahoo_session  # noqa: E402

DATA = ROOT / "data"
OUT = DATA / "cg_sector.csv"

# Checked in order — first match wins. Lenders come FIRST because "Bajaj Housing Finance"
# and "Northern Arc Capital" must not be swept into Real Estate or Industrials by a later
# generic rule.
#
# Stems are matched as PREFIXES (`\b` only on the left). Getting this wrong is easy and
# silent: an earlier version wrapped the alternation in \b(...)\b, so "technolog" could
# never match "Technologies" and "pharma" could never match "Pharmaceuticals" — the
# trailing boundary rejects exactly the words the stem exists to catch. Short/ambiguous
# tokens that WOULD over-match as prefixes are pinned with an explicit \b on the right.
NAME_RULES = [
    ("Financials",
     r"\b(?:bank|nbfc|financ|finserv|fincorp|capital\b|securit|broking|brokerage|insur|"
     r"assurance|asset manage|funds? manage|mutual fund|wealth|microfin|credit\b|lending|"
     r"fintech|bajaj fin|investment\b|invest manage)"),
    ("Real Estate",
     r"\b(?:realty|real estate|estates?\b|developer|properti|property|builder|township|"
     r"infratech|housing\b(?! finance))"),
    ("Healthcare",
     r"\b(?:pharma|health|hospital|medic|medi |drug|lab\b|labs\b|laborator|diagnostic|"
     r"life ?science|bio ?tech|biotech|surgical|remedies|therapeut|nutrit|speciality|"
     r"parenteral|wellness|research\b|crop ?science|vaccin)"),
    ("Technology",
     r"\b(?:tech\b|techno|software|infotech|digital|digitek|cyber|e-?commerce|internet|"
     r"computer|analytic|infosys|datamatic|softech|it services|semiconduct|electronic)"),
    ("Energy",
     r"\b(?:power|energy|solar|renewable|wind\b|oil\b|oils\b|gas\b|gases|petro|coal|"
     r"electric|utilit|bioenerg|ethanol|hydro)"),
    ("Materials",
     r"\b(?:steel|cement|chemical|metal|alloy|mining|mineral|polymer|paper|plastic|glass|"
     r"fertili|pigment|granite|ceramic|stainless|wires?\b|copper|aluminium|zinc|carbon|"
     r"resin|adhesive|agrolife|agro ?chem)"),
    ("Consumer Discretionary",
     r"\b(?:food|beverage|retail|apparel|textile|garment|spin|twistex|texworld|hotel|"
     r"restaurant|jewel|diamond|consumer|fmcg|dairy|agro|footwear|furniture|cosmetic|"
     r"entertain|media|travel|tourism|leisure|educat|edtech|coffee|tea\b|brewer|distiller|"
     r"studds|accessories|mattress|wakefit)"),
    ("Industrials",
     r"\b(?:engineering|industr|manufactur|machin|tools?\b|infra|construct|logistic|"
     r"transport|cargo|freight|forwarder|shipping|defence|aerospace|auto|motors?\b|"
     r"bearing|electrical|packaging|mechatronic|pumps?\b|valves?\b|castings?\b|"
     r"fabricat|equipment|instrument)"),
]

FIN_INDUSTRY = re.compile(
    r"bank|credit|capital market|asset management|insurance|financial|lending|mortgage|"
    r"savings|thrift|shell company|closed-end fund", re.I)
RE_INDUSTRY = re.compile(r"real estate|reit|property", re.I)
TRUST_RE = re.compile(r"\b(reit|invit)\b|real estate investment trust|infrastructure "
                      r"investment trust|business trust", re.I)


def instrument_of(name, industry):
    """Growth equity, or a yield vehicle that is not supposed to 'recover' at all?"""
    blob = f"{name} {industry or ''}"
    if re.search(r"\binvit\b|infrastructure investment trust", blob, re.I):
        return "invit"
    if re.search(r"\breit\b|real estate investment trust", blob, re.I):
        return "reit"
    return "equity"


def from_name(name):
    n = str(name or "")
    for sector, pat in NAME_RULES:
        if re.search(pat, n, re.I):
            return sector
    return None


# the analyzer's own RHP bucket -> the sheet's sector vocabulary. This is the third
# source and it covers exactly the gap the other two leave: the newest IPOs, which have
# a prospectus but no Yahoo profile yet because they have barely started trading.
RHP_TO_SHEET = {"insurance": "Financials", "asset_management": "Financials",
                "fin_services": "Financials", "consumer": "Consumer Discretionary",
                "pharma_health": "Healthcare", "energy_infra": "Energy",
                "industrial": "Industrials", "workspace_realty": "Real Estate",
                "tech_platform": "Technology"}


def from_rhp():
    """Sector implied by each committed RHP report, keyed by cg_ipo_id."""
    sys.path.insert(0, str(ROOT / "backend"))
    from app.pipeline.listing_predictor import features_from_report

    out = {}
    for p in (ROOT / "docs" / "data" / "reports").glob("*.json"):
        try:
            rep = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        b = features_from_report(rep).get("sector")
        if b and b in RHP_TO_SHEET:
            out[p.stem] = RHP_TO_SHEET[b]
    return out


def yahoo_profile(op, crumb, sym):
    """sector AND industry — the coarse label alone cannot tell a bank from an AMC."""
    u = (f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{sym}"
         f"?modules=assetProfile&crumb={urllib.parse.quote(crumb)}")
    try:
        d = json.loads(op.open(u, timeout=30).read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 404):
            return None, None
        raise
    except Exception:                                    # noqa: BLE001
        return None, None
    res = (d.get("quoteSummary") or {}).get("result") or []
    prof = res[0].get("assetProfile", {}) if res else {}
    return prof.get("sector"), prof.get("industry")


def main():
    refetch_all = "--all" in sys.argv
    outc = pd.read_csv(DATA / "ipo_outcomes.csv", dtype={"cg_ipo_id": str})
    issue = pd.read_csv(DATA / "cg_issue.csv", dtype={"cg_ipo_id": str})
    issue.columns = [c.split("<")[0].strip() for c in issue.columns]
    names = dict(zip(issue["cg_ipo_id"], issue["company"]))

    have = {}
    if OUT.exists() and not refetch_all:
        have = {r["cg_ipo_id"]: dict(r)
                for _, r in pd.read_csv(OUT, dtype={"cg_ipo_id": str}).iterrows()}

    todo = [r for _, r in outc.iterrows()
            if pd.notna(r.get("yahoo_symbol"))
            and (r["cg_ipo_id"] not in have
                 or pd.isna(have[r["cg_ipo_id"]].get("yahoo_sector")))]
    print(f"rows: {len(outc)}   cached: {len(have)}   to fetch from Yahoo: {len(todo)}")

    op, crumb = yahoo_session()
    fetched = 0
    for i, r in enumerate(todo):
        cid, sym = r["cg_ipo_id"], r["yahoo_symbol"]
        try:
            ysec, yind = yahoo_profile(op, crumb, sym)
        except Exception as e:                           # noqa: BLE001
            print(f"  re-handshaking after {sym}: {e}")
            op, crumb = yahoo_session()
            time.sleep(2)
            continue
        if ysec:
            fetched += 1
        have[cid] = {"cg_ipo_id": cid, "symbol": sym, "yahoo_sector": ysec,
                     "yahoo_industry": yind, "source": "yahoo" if ysec else "yahoo-miss"}
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(todo)}  resolved {fetched}")
        time.sleep(0.35)                                 # be a good citizen
    print(f"  yahoo resolved {fetched}/{len(todo)}")

    # Every row gets a sector. Three sources, best first: Yahoo's GICS profile (only exists
    # once a stock trades), then the prospectus the analyzer already parsed (covers the
    # newest IPOs, which are precisely the ones Yahoo has not resolved yet), then the
    # company name.
    rhp = from_rhp()
    rows = []
    for cid, name in names.items():
        rec = have.get(cid) or {"cg_ipo_id": cid, "symbol": None,
                                "yahoo_sector": None, "yahoo_industry": None, "source": None}
        ysec, yind = rec.get("yahoo_sector"), rec.get("yahoo_industry")
        sector = canon_sector(ysec) if ysec and pd.notna(ysec) else None
        source = "yahoo"
        if not sector and cid in rhp:
            sector, source = rhp[cid], "rhp"
        if not sector:
            sector, source = from_name(name), "name"
        if not sector:
            sector, source = "Other", "unknown"

        ind = yind if (yind and pd.notna(yind)) else ""
        inst = instrument_of(name, ind)
        is_fin = bool(FIN_INDUSTRY.search(ind)) if ind else (sector == "Financials")
        is_re = bool(RE_INDUSTRY.search(ind)) if ind else (sector == "Real Estate")
        rows.append({"cg_ipo_id": cid, "symbol": rec.get("symbol"),
                     "yahoo_sector": ysec, "yahoo_industry": yind, "sector": sector,
                     "instrument": inst, "is_financial": int(is_fin),
                     "is_realestate": int(is_re), "source": source})

    df = pd.DataFrame(rows)
    df.to_csv(OUT, index=False)
    print(f"\nwrote {OUT.relative_to(ROOT)}  ({len(df)} rows)")
    print("\nsector coverage:")
    print(df["sector"].value_counts().to_string())
    print("\nby source:", df["source"].value_counts().to_dict())
    print("financials:", int(df["is_financial"].sum()),
          " real estate:", int(df["is_realestate"].sum()),
          " non-equity trusts:", df[df["instrument"] != "equity"].shape[0])


if __name__ == "__main__":
    main()
