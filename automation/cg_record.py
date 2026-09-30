"""Chittorgarh IPO detail pages -> one structured record per IPO.

Every detail page is a Next.js page whose flight data (`self.__next_f.push`)
carries the IPO as a JSON object: face value, lot size, shares offered per
category (anchor / QIB / bNII / sNII / retail / total), pre/post-issue share
counts, the KPI block (RoNW, ROCE, D/E, EPS, P/E, P/B, margins, NAV), market
cap, listing-day close and direct prospectus links. The restated financials
table (total income, PAT, EBITDA, net worth, borrowing) is a separate text
chunk the record points at ("$1e").

Reading that JSON instead of the rendered text is what makes old IPOs usable:
the KPI block only exists from ~2022, but the share counts and the financials
table go back to 2006, so the ratios can be recomputed (see finalsheet.py).

Raw flight text is cached gzipped under data/cache/cg_flight/ (gitignored) so
parsing can change without refetching ~1,000 pages.

    python automation/cg_record.py            # fetch missing + recent, rebuild data/cg_records.json
    python automation/cg_record.py --all      # refetch everything (slow: ~1 page / 1.5 s)
"""
from __future__ import annotations

import gzip
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "cache" / "cg_flight"
RECORDS = DATA / "cg_records.json"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
PUSH = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')
REFRESH_DAYS = 60        # IPOs this recent get refetched every run (KPIs, listing fill in late)
PAUSE_S = 1.5

# record fields kept in cg_records.json (everything else on the page is presentation)
KEEP = [
    "id", "company_name", "issue_category", "issue_process_type", "issue_open_date",
    "issue_close_date", "face_value", "issue_price_lower", "issue_price_upper",
    "issue_price_final", "market_lot_size", "issue_size_in_shares", "issue_size_in_amt",
    "issue_size_fresh_in_shares", "issue_size_ofs_in_shares", "ipo_industry",
    "shares_offered_anchor_investor", "shares_offered_qib", "shares_offered_qib_ex_anchor",
    "shares_offered_qib_with_anchor", "shares_offered_nii", "shares_offered_small_nii",
    "shares_offered_big_nii", "shares_offered_rii", "shares_offered_emp",
    "shares_offered_market_maker", "shares_offered_others", "shares_offered_total",
    "shares_offered_qib_percentage_temp", "shares_offered_nii_percentage_temp",
    "shares_offered_rii_percentage_temp",
    "total_shareholding_pre_issue", "total_shareholding_post_issue",
    "promoter_shareholding_pre_issue", "promoter_shareholding_post_issue",
    "kpi_roe", "kpi_roce", "kpi_ronw", "kpi_debt_equity", "kpi_eps", "kpi_eps_post",
    "kpi_pat_margin", "kpi_ebitda", "kpi_as_of_date", "pe_ratio", "post_pe_ratio",
    "price_to_book_value", "nav", "market_cap", "market_cap_pre", "listing_day_closing_price",
    "prospectus_rhp", "prospectus_drhp", "final_prospectus",
    "il_ipo_listing_date", "il_isin", "il_bse_script_code", "il_nse_script_symbol",
    "latest_fy_dt",
]


def _flight(html: str) -> str:
    return "".join(json.loads('"' + p + '"') for p in PUSH.findall(html))


def _object_with(s: str, key: str):
    """The first JSON object in the flight text that contains `"key":`."""
    for m in re.finditer(r'"%s":' % re.escape(key), s):
        depth, i = 0, m.start()
        while i >= 0:
            c = s[i]
            if c == "}":
                depth += 1
            elif c == "{":
                if depth == 0:
                    break
                depth -= 1
            i -= 1
        depth, j = 0, i
        while j < len(s):
            c = s[j]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        try:
            return json.loads(s[i:j + 1])
        except ValueError:
            continue
    return None


def _chunk(s: str, ref) -> str | None:
    """Resolve a "$1e"-style reference to its text chunk ("1e:T<hex bytes>,<text>")."""
    if not isinstance(ref, str) or not ref.startswith("$"):
        return None
    b = s.encode("utf-8")
    # text chunks are concatenated straight after the previous one ("...</ul>1e:T8a0,<div"),
    # so the id's left edge is only safe when the preceding byte can't be part of an id
    key = re.escape(ref[1:].encode()) + rb":T([0-9a-f]+),"
    m = re.search(rb"(?<![0-9a-f])" + key, b) or re.search(key, b)
    if not m:
        return None
    n = int(m.group(1), 16)
    return b[m.end():m.end() + n].decode("utf-8", "replace")


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("%", "").replace("₹", "").strip()
    s = re.sub(r"(?i)\s*cr\.?$", "", s)
    try:
        return float(s)
    except ValueError:
        return None


def parse_financials(html_chunk: str | None) -> dict:
    """Chittorgarh's restated-financials table -> {"periods": [...], rows: {name: [values]}}.

    Values are ₹ crore. Rows can be shorter than the period list (a blank cell at the
    end is simply absent), so values align from the left, like the rendered table."""
    if not html_chunk:
        return {}
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html_chunk, re.S)
    table = {}
    periods = []
    for tr in rows:
        cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip()
                 for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S)]
        if not cells:
            continue
        head = cells[0].lower()
        if head.startswith("period"):
            periods = cells[1:]
            continue
        if not periods or head.startswith("amount"):
            continue
        vals = [_num(c) for c in cells[1:]]
        if any(v is not None for v in vals):
            table[cells[0]] = vals
    title = re.search(r"Company Financials\s*\(([^)]*)\)", re.sub(r"<[^>]+>", " ", html_chunk))
    return {"basis": title.group(1).strip() if title else None, "periods": periods, "rows": table}


def parse_listing_day(s: str) -> dict:
    """The "Listing Day Trading Information" table -> {"NSE": {"open":..,"low":..,
    "high":..,"close":..}, "BSE": {...}}. The report API only has the listing open
    for recent years; the page has it back to 2006."""
    i = s.find("Listing Day Trading Information")
    if i < 0:
        return {}
    seg = s[i:i + 8000]
    exch = re.findall(r'"children":\["\$","span",null,\{"children":"(BSE|NSE)"\}\]', seg)
    out = {}
    for label, key in (("Open", "open"), ("Low", "low"), ("High", "high"), ("Last Trade", "close")):
        m = re.search(r'"children":"%s"\}\],\[(.*?)\]\]\]' % re.escape(label), seg)
        if not m:
            continue
        vals = re.findall(r'\["₹","([\d,.]+)"\]', m.group(1))
        for ex, v in zip(exch, vals):
            out.setdefault(ex, {})[key] = _num(v)
    return out


def parse_page(html: str) -> dict | None:
    s = _flight(html)
    o = _object_with(s, "shares_offered_total") or _object_with(s, "face_value")
    if not o:
        return None
    rec = {k: o.get(k) for k in KEEP if o.get(k) not in (None, "")}
    fin = parse_financials(_chunk(s, o.get("financial")))
    if fin:
        rec["financials"] = fin
    ld = parse_listing_day(s)
    if ld:
        rec["listing_day"] = ld
    if isinstance(o.get("listing_detail"), dict) and rec.get("listing_day_closing_price") is None:
        rec["listing_day_closing_price"] = o["listing_detail"].get("ildt_close_price")
    return rec


def _cache_path(cid: str) -> Path:
    return CACHE / f"{cid}.txt.gz"


def fetch(cid: str, url: str) -> str:
    req = urllib.request.Request(url, headers={**UA, "Referer": "https://www.chittorgarh.com/"})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                html = r.read().decode("utf-8", "replace")
            CACHE.mkdir(parents=True, exist_ok=True)
            with gzip.open(_cache_path(cid), "wt", encoding="utf-8") as f:
                f.write(html)
            return html
        except Exception as e:  # noqa: BLE001 - network: retry, then surface
            last = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"fetch {cid} failed: {last}")


def cached(cid: str) -> str | None:
    p = _cache_path(cid)
    if not p.exists():
        return None
    with gzip.open(p, "rt", encoding="utf-8") as f:
        return f.read()


def refresh(refetch_all=False, max_fetch=None, log=print) -> dict:
    """Fetch pages that are missing (or recent / all), then rebuild cg_records.json
    from the cache. Returns counts for the run summary."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    issue = pd.read_csv(DATA / "cg_issue.csv", dtype={"cg_ipo_id": str})
    issue["open_dt"] = pd.to_datetime(issue["Opening Date"], format="%d-%b-%Y", errors="coerce")
    now = pd.Timestamp.now().normalize()
    fetched = failed = 0
    for _, r in issue.sort_values("open_dt", ascending=False).iterrows():
        cid, url = str(r["cg_ipo_id"]), str(r.get("detail_url") or "")
        if not url.startswith("http"):
            continue
        recent = pd.notna(r["open_dt"]) and (now - r["open_dt"]).days <= REFRESH_DAYS
        if not (refetch_all or recent or not _cache_path(cid).exists()):
            continue
        if max_fetch is not None and fetched >= max_fetch:
            break
        try:
            fetch(cid, url)
            fetched += 1
        except RuntimeError as e:
            failed += 1
            log(f"  {e}")
        time.sleep(PAUSE_S)
        if fetched and fetched % 50 == 0:
            log(f"  cg pages: {fetched} fetched so far")

    records, unparsed = {}, 0
    for _, r in issue.iterrows():
        cid = str(r["cg_ipo_id"])
        html = cached(cid)
        if not html:
            continue
        rec = parse_page(html)
        if rec is None:
            unparsed += 1
            continue
        records[cid] = rec
    RECORDS.write_text(json.dumps(records, ensure_ascii=False, sort_keys=True, indent=0),
                       encoding="utf-8")
    log(f"  cg records: {fetched} fetched, {failed} failed, {len(records)} parsed, "
        f"{unparsed} unparseable, {len(issue) - len(records)} without a page")
    return {"fetched": fetched, "failed": failed, "records": len(records), "unparsed": unparsed}


if __name__ == "__main__":
    refresh(refetch_all="--all" in sys.argv)
