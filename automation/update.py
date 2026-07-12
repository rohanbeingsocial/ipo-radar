"""Self-updating IPO radar: the daily job behind the GitHub Pages dashboard.

Steps (each degrades gracefully — a source being down never breaks the site):
  1. refresh Chittorgarh (issue structure / subscription / listing) for the
     current + previous year, upserted into the canonical CSVs in data/
  2. refresh Yahoo outcomes for IPOs listed within the last ~25 months
  3. fetch + analyze RHPs from SEBI for new IPOs (bounded per run), storing
     compact report JSONs in docs/data/reports/
  4. recompute forecasts (rules + horizon GBMs) for every IPO in the site
     window and write docs/data/ipos.json
  5. rebuild ipodata/finalipodata_expanded_20yr.xlsx

Local seeding (uses the local API's already-analyzed corpus instead of
re-analyzing hundreds of PDFs):  python automation/update.py --seed
CI run:                          python automation/update.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SITE_DATA = ROOT / "docs" / "data"
REPORTS = SITE_DATA / "reports"
BACKEND = ROOT / "backend"
for p in (DATA, SITE_DATA, REPORTS):
    p.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(BACKEND))

from app.pipeline import listing_predictor  # noqa: E402
from tools.analyze_standalone import analyze_pdf  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
CG_BASE = "https://webnodejs.chittorgarh.com/cloud/report/data-read"
SEBI_AJAX = "https://www.sebi.gov.in/sebiweb/ajax/home/getnewslistinfo.jsp"
NOW = pd.Timestamp.now().normalize()
SITE_WINDOW_DAYS = 550          # how far back the dashboard looks
MAX_NEW_RHP_PER_RUN = 6         # keep CI runs bounded
MAX_NEW_DETAILS_PER_RUN = 40    # Chittorgarh detail-page / Yahoo sector fetches per run
DETAILS_WINDOW_DAYS = 1825      # Chittorgarh only carries the KPI + reservation block
                                # for recent IPOs (~2021 on); older pages have neither
DATE_FMT = "%d-%b-%Y"


def http(url, data=None, timeout=60, tries=3, referer="https://www.chittorgarh.com/"):
    for i in range(tries):
        try:
            hdrs = UA | {"Referer": referer}
            if data:
                hdrs["Content-Type"] = "application/x-www-form-urlencoded"
            req = urllib.request.Request(url, data=data.encode() if isinstance(data, str) else data,
                                         headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def strip_html(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", str(s or ""))).strip()


def norm_tokens(name):
    s = re.sub(r"[^a-z0-9 ]", " ", str(name).lower())
    drop = {"limited", "ltd", "private", "pvt", "rhp", "the", "and", "of", "red",
            "herring", "prospectus", "dated", "co", "company"}
    return [w for w in s.split() if w and w not in drop]


def num(x):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return np.nan
    try:
        v = float(str(x).replace(",", "").replace("%", "").strip())
        return v if np.isfinite(v) else np.nan
    except ValueError:
        return np.nan


def pdate(s):
    return pd.to_datetime(s, format=DATE_FMT, errors="coerce")


# ─────────────────────────── 1. Chittorgarh refresh ───────────────────────────

def cg_pages(report_id, year):
    seen = set()
    for pg in range(1, 61):
        url = f"{CG_BASE}/{report_id}/{pg}/1/{year}/2024-25/0/mainboard/0?search="
        d = json.loads(http(url))
        rows = d.get("reportTableData") or []
        if not rows:
            return
        key = strip_html(rows[0].get("Company", "")) + str(rows[0])[:120]
        if key in seen:
            return
        seen.add(key)
        yield from rows
        if len(rows) < 5:
            return
        time.sleep(0.4)


def cg_clean(rows, id_from_field=None):
    out = []
    for r in rows:
        rec = {}
        comp = str(r.get("Company", ""))
        m = re.search(r"/ipo/[^/]+/(\d+)/", comp)
        rec["cg_ipo_id"] = m.group(1) if m else ""
        m2 = re.search(r'href="(https://www\.chittorgarh\.com/ipo/[^"]+)"', comp)
        rec["detail_url"] = m2.group(1) if m2 else ""
        rec["company"] = strip_html(comp)
        for k, v in r.items():
            if k == "Company":
                continue
            kk = k.lstrip("~").split("<")[0].strip()
            rec[kk] = strip_html(v) if isinstance(v, str) else v
        if not rec["cg_ipo_id"] and id_from_field and rec.get(id_from_field):
            try:
                rec["cg_ipo_id"] = str(int(float(rec[id_from_field])))
            except (ValueError, TypeError):
                pass
        out.append(rec)
    return [r for r in out if r["company"] and r["cg_ipo_id"]]


def upsert(csv_path, fresh_rows):
    fresh = pd.DataFrame(fresh_rows)
    if fresh.empty:
        return pd.read_csv(csv_path) if csv_path.exists() else fresh
    fresh["cg_ipo_id"] = fresh["cg_ipo_id"].astype(str)
    if csv_path.exists():
        old = pd.read_csv(csv_path)
        old["cg_ipo_id"] = old["cg_ipo_id"].map(
            lambda v: str(int(float(v))) if pd.notna(v) and str(v).replace(".", "").isdigit() else str(v))
        merged = pd.concat([fresh, old[~old["cg_ipo_id"].isin(set(fresh["cg_ipo_id"]))]],
                           ignore_index=True)
    else:
        merged = fresh
    merged.to_csv(csv_path, index=False)
    return merged


def refresh_chittorgarh():
    years = [NOW.year, NOW.year - 1]
    for rid, name, idf in [(82, "cg_issue", None), (21, "cg_subs", "id"), (25, "cg_listing", "id")]:
        rows = []
        for yr in years:
            try:
                rows += list(cg_pages(rid, yr))
            except Exception as e:
                print(f"  chittorgarh r{rid} y{yr} failed: {e}")
        merged = upsert(DATA / f"{name}.csv", cg_clean(rows, id_from_field=idf))
        print(f"  {name}: +{len(rows)} fetched, {len(merged)} total")


# ─────────────────────────── 2. Yahoo outcomes ───────────────────────────

def yahoo_chart(sym, start, end):
    p1, p2 = int(start.timestamp()), int(min(end, pd.Timestamp.now()).timestamp())
    d = json.loads(http(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
                        f"?period1={p1}&period2={p2}&interval=1d", referer=""))
    res = d["chart"]["result"][0]
    if not res.get("timestamp"):
        return pd.Series(dtype=float)
    ts = pd.to_datetime(res["timestamp"], unit="s", utc=True).tz_convert("Asia/Kolkata").normalize()
    s = pd.Series(res["indicators"]["quote"][0]["close"], index=ts.tz_localize(None)).dropna()
    return s[~s.index.duplicated(keep="last")]


def yahoo_outcomes_row(s, listing_date, issue_price, listing_gain, window_end):
    s = s[s.index >= listing_date - timedelta(days=5)]
    if len(s) < 5 or not issue_price or issue_price <= 0 or (s.index[0] - listing_date).days > 20:
        return None
    day1 = s.iloc[0]
    base = (1 + listing_gain / 100) if listing_gain is not None and np.isfinite(listing_gain) \
        else day1 / issue_price
    pct = lambda px: round((base * (px / day1) - 1) * 100, 1)
    delisted = s.index[-1] < min(window_end, pd.Timestamp.now()) - timedelta(days=30)
    o = {"ret_1d": pct(day1), "delisted": bool(delisted),
         "current_ret": pct(s.iloc[-1]), "yahoo_last_date": str(s.index[-1].date())}
    for label, days in [("ret_6m", 182), ("ret_12m", 365), ("ret_24m", 730)]:
        horizon = listing_date + timedelta(days=days)
        w = s[s.index <= horizon]
        if len(w) and w.index[-1] >= horizon - timedelta(days=21):
            o[label] = pct(w.iloc[-1])
        elif len(w) and delisted:
            o[label] = pct(w.iloc[-1])
        else:
            o[label] = np.nan
    w12 = s[s.index <= listing_date + timedelta(days=365)]
    if len(w12) >= 10:
        i_bot = int(np.argmin(w12.values))
        o["bottom_so_far_pct"] = pct(w12.iloc[i_bot])
        o["bottom_so_far_session"] = i_bot
        if delisted or w12.index[-1] >= listing_date + timedelta(days=300):
            o["bottom_12m_pct"], o["sessions_to_bottom"] = o["bottom_so_far_pct"], i_bot
            after = s[(s.index >= w12.index[i_bot]) & (s.index <= listing_date + timedelta(days=730))]
            if len(after):
                i_pk = int(np.argmax(after.values))
                o["peak_24m_pct"] = pct(after.iloc[i_pk])
                o["sessions_to_peak"] = i_bot + i_pk
    return o


def refresh_outcomes():
    lst = pd.read_csv(DATA / "cg_listing.csv")
    lst.columns = [c.split("<")[0].strip() for c in lst.columns]
    out_path = DATA / "ipo_outcomes.csv"
    outc = pd.read_csv(out_path) if out_path.exists() else pd.DataFrame(columns=["cg_ipo_id"])
    outc["cg_ipo_id"] = outc.get("cg_ipo_id", pd.Series(dtype=str)).astype(str)
    rows, refreshed = {r.get("cg_ipo_id"): dict(r) for _, r in outc.iterrows()}, 0
    for _, r in lst.iterrows():
        cid = str(r.get("cg_ipo_id", "")).split(".")[0]
        ldate = pdate(r.get("Listing Date"))
        if not cid or pd.isna(ldate):
            continue
        age = (NOW - ldate).days
        done = rows.get(cid, {})
        # final numbers never change; only refresh IPOs still inside 25 months
        if age > 760 and not pd.isna(num(done.get("ret_24m", np.nan))):
            continue
        if age > 760 and done.get("company"):
            continue
        price = num(r.get("Issue Price (Rs.)"))
        lg = num(r.get("% Gain/Loss (Issue price v/s close price on Listing)"))
        start, end = ldate - timedelta(days=7), ldate + timedelta(days=800)
        syms = []
        nse = str(r.get("NSE Symbol") or "").strip()
        if nse and nse.lower() not in ("nan", "na", "-", ""):
            syms.append(nse.upper() + ".NS")
        got = None
        for sym in syms:
            try:
                got = yahoo_outcomes_row(yahoo_chart(sym, start, end), ldate, price, lg, end)
            except Exception:
                got = None
            if got:
                break
            time.sleep(0.3)
        if not got:      # search fallback
            try:
                q = urllib.parse.quote(re.sub(r"\b(ltd|limited)\.?\b", "", r["company"], flags=re.I).strip(" .,&"))
                d = json.loads(http(f"https://query1.finance.yahoo.com/v1/finance/search?q={q}"
                                    f"&quotesCount=6&newsCount=0", referer=""))
                for c in [x["symbol"] for x in d.get("quotes", [])
                          if str(x.get("symbol", "")).endswith((".NS", ".BO"))]:
                    try:
                        got = yahoo_outcomes_row(yahoo_chart(c, start, end), ldate, price, lg, end)
                    except Exception:
                        got = None
                    if got:
                        break
                    time.sleep(0.3)
            except Exception:
                pass
        if got:
            rows[cid] = {"cg_ipo_id": cid, "company": r["company"],
                         "listing_date": str(ldate.date()), "issue_price": price, **got}
            refreshed += 1
        time.sleep(0.4)
    pd.DataFrame(rows.values()).to_csv(out_path, index=False)
    print(f"  outcomes: {refreshed} refreshed, {len(rows)} total")


# ─────────────────────────── 3. SEBI RHP fetch + analyze ───────────────────────────

def compact_report(rep: dict) -> dict:
    """Everything the forecaster and the dashboard need, nothing else."""
    keep = {}
    for k in ("scoring", "snapshot", "valuation", "risk", "forensic", "verdict",
              "meta", "industry"):
        if k in rep:
            keep[k] = rep[k]
    if "industry" in keep:
        keep["industry"] = {"excerpt": (keep["industry"].get("excerpt") or "")[:500]}
    if "risk" in keep:
        keep["risk"] = {"score": keep["risk"].get("score"),
                        "boilerplate": keep["risk"].get("boilerplate"),
                        "top": [{"title": f.get("title"), "severity": f.get("severity")}
                                for f in (keep["risk"].get("findings") or [])[:5]]}
    if "forensic" in keep:
        keep["forensic"] = {"flags": [{"name": f.get("name"), "detail": str(f.get("detail"))[:160]}
                                      for f in (keep["forensic"].get("flags") or [])]}
    cases = rep.get("cases") or {}
    keep["cases"] = {side: [{"text": c.get("text")} for c in (cases.get(side) or [])[:3]]
                     for side in ("bull", "bear")}
    # retain the latest-FY ratios the analyzer already computes (financial_extractor
    # -> valuation.compute_ratios) so the dataset/dashboard can surface fundamentals
    # without re-parsing the PDF. Ratios are fractions here; consumers scale to %.
    ratios = (rep.get("financials") or {}).get("ratios") or {}
    fund = {"pat_margin": ratios.get("net_margin"), "ebitda_margin": ratios.get("operating_margin"),
            "roe": ratios.get("roe"), "roce": ratios.get("roce"),
            "debt_equity": ratios.get("debt_equity"),
            "post_ipo_pe": (rep.get("valuation") or {}).get("issuer_pe")}
    if any(v is not None for v in fund.values()):
        keep["fundamentals"] = fund
    return keep


def sebi_recent_entries(max_pages=8):
    row_re = re.compile(
        r'<td>([A-Z][a-z]{2} \d{2}, \d{4})</td>\s*<td><a href=[\'"]'
        r'(https://www\.sebi\.gov\.in/filings/[^\'"]+)[\'"][^>]*title=["\']([^"\']+)["\']', re.S)
    entries = []
    for page in range(1, max_pages + 1):
        body = (f"nextValue={page}&next=n&search=&fromDate=&toDate=&fromYear=&toYear="
                f"&deptId=&sid=3&ssid=15&smid=11&ssidhidden=15&intmid=-1&sText=Filings")
        html = http(SEBI_AJAX, data=body, referer="https://www.sebi.gov.in/sebiweb/home/HomeAction.do").decode("utf-8", "replace")
        for d, u, t in row_re.findall(html):
            entries.append({"date": pd.to_datetime(d), "url": u, "title": t.strip(),
                            "toks": set(norm_tokens(t))})
        time.sleep(1.0)
    return [e for e in entries
            if not re.search(r"corrigendum|addendum|\bnotice\b|clarification", e["title"], re.I)]


PDF_RES = [re.compile(r'href=[\'"](https://www\.sebi\.gov\.in/sebi_data/[^\'"]+\.pdf)[\'"]', re.I),
           re.compile(r"file=([^&\"']+\.pdf)", re.I),
           re.compile(r'href=[\'"]([^\'"]+\.pdf)[\'"]', re.I)]


def fetch_and_analyze(entry_url, out_json: Path) -> bool:
    detail = http(entry_url, referer="https://www.sebi.gov.in/").decode("utf-8", "replace")
    pdf_url = None
    for rx in PDF_RES:
        hit = rx.search(detail)
        if hit:
            pdf_url = urllib.parse.urljoin(entry_url, hit.group(1))
            break
    if not pdf_url:
        raise RuntimeError("no pdf link")
    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / "rhp.pdf"
        r = subprocess.run(["curl", "-sL", "-A", UA["User-Agent"], "--retry", "4",
                            "--retry-all-errors", "-C", "-", "--max-time", "600",
                            "-o", str(dest), pdf_url], capture_output=True)
        if r.returncode != 0 or not dest.exists() or dest.stat().st_size < 100_000 \
                or not dest.read_bytes()[:5].startswith(b"%PDF"):
            raise RuntimeError("bad pdf download")
        rep = analyze_pdf(str(dest))
        out_json.write_text(json.dumps(compact_report(rep), ensure_ascii=False), encoding="utf-8")
    return True


def ensure_rhp_reports():
    issue = pd.read_csv(DATA / "cg_issue.csv")
    issue["cg_ipo_id"] = issue["cg_ipo_id"].astype(str)
    issue["open_dt"] = pdate(issue["Opening Date"])
    recent = issue[issue["open_dt"] >= NOW - timedelta(days=270)]
    missing = [r for _, r in recent.iterrows()
               if not (REPORTS / f"{r['cg_ipo_id']}.json").exists()]
    if not missing:
        print("  rhp reports: nothing new")
        return
    try:
        entries = sebi_recent_entries()
    except Exception as e:
        print(f"  sebi listing failed ({e}); will retry next run")
        return
    done = 0
    for r in missing:
        if done >= MAX_NEW_RHP_PER_RUN:
            break
        toks = norm_tokens(r["company"])
        cands = [e for e in entries if set(toks) <= e["toks"]
                 and (pd.isna(r["open_dt"]) or
                      r["open_dt"] - timedelta(days=200) <= e["date"] <= r["open_dt"] + timedelta(days=45))]
        if not cands:
            continue
        cands.sort(key=lambda e: abs((e["date"] - (r["open_dt"] or e["date"])).days))
        try:
            fetch_and_analyze(cands[0]["url"], REPORTS / f"{r['cg_ipo_id']}.json")
            done += 1
            print(f"  analyzed RHP: {r['company'][:45]}")
        except Exception as e:
            print(f"  RHP failed {r['company'][:40]}: {e}")
    print(f"  rhp reports: {done} new")


# ─────────────── 3b. enrichment: sector · reservation · GMP ───────────────
# Sector (Yahoo assetProfile) and the QIB/Retail/NII reservation split
# (Chittorgarh detail page) are per-IPO facts that don't change once known, so
# they're fetched once and cached in data/cg_details.csv. GMP is live-only
# (grey market, third-party) and only meaningful while an IPO is open, so it's
# refreshed every run into data/cg_gmp.csv for currently-open issues.

DETAILS_CSV = DATA / "cg_details.csv"
GMP_CSV = DATA / "cg_gmp.csv"


def sid_col(s):
    """Stable string ids. A single NaN would make the column float64 and turn every
    id into "2624.0", which would silently duplicate every cached row."""
    return s.map(lambda v: "" if pd.isna(v)
                 else str(int(float(v))) if str(v).replace(".", "").isdigit() else str(v))


def yahoo_session():
    """Cookie + crumb handshake; quoteSummary now 401s without it."""
    import http.cookiejar
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    op.addheaders = [("User-Agent", UA["User-Agent"])]
    for seed in ("https://fc.yahoo.com/", "https://finance.yahoo.com/"):
        try:
            op.open(seed, timeout=30)
            break
        except Exception:
            continue
    crumb = op.open("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=30).read().decode()
    return op, crumb


def yahoo_sector(op, crumb, sym):
    u = (f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{sym}"
         f"?modules=assetProfile&crumb={urllib.parse.quote(crumb)}")
    try:
        d = json.loads(op.open(u, timeout=30).read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 404):     # bad/renamed symbol or crumb gone stale
            return None
        raise
    res = (d.get("quoteSummary") or {}).get("result") or []
    prof = res[0].get("assetProfile", {}) if res else {}
    return prof.get("sector") or None


def _space_text(html):
    """Strip tags to spaces (not empties) so adjacent table cells stay separated."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def parse_reservation(html):
    """QIB/Retail/NII reserved shares from the detail page. Chittorgarh serves this
    two ways depending on the IPO: a rendered issue-structure table ("QIB Shares
    Offered  Not more than 50%") and an embedded JSON blob
    ("qib_percentage_temp":"Not more than 50%"), so try both.
    Anchor is the regulatory 60% of the QIB portion (matches the source sheet)."""
    txt = _space_text(html)

    def pct(*patterns):
        for p in patterns:
            m = re.search(p, txt, re.I)
            if not m:
                continue
            try:
                v = round(float(m.group(1)), 2)
            except ValueError:
                continue
            if 0 < v <= 100:
                return v
        return None

    # the shares_offered_* keys carry the actual reserved split (QIB 75 / NII 15 /
    # RII 10); the "Not more/less than" wording is the regulatory cap and is only a
    # fallback. Retail is keyed "rii".
    qib = pct(r"shares_offered_qib_percentage[^%]{0,60}?([\d.]+)\s*%",
              r"QIB\b[^%]{0,40}?Not (?:more|less) than\s+([\d.]+)\s*%",
              r"qib_percentage[^%]{0,60}?([\d.]+)\s*%")
    retail = pct(r"shares_offered_rii_percentage[^%]{0,60}?([\d.]+)\s*%",
                 r"Retail\b[^%]{0,40}?Not (?:more|less) than\s+([\d.]+)\s*%",
                 r"(?:retail|rii)_percentage[^%]{0,60}?([\d.]+)\s*%")
    nii = pct(r"shares_offered_nii_percentage[^%]{0,60}?([\d.]+)\s*%",
              r"\bNII\b[^%]{0,40}?Not (?:more|less) than\s+([\d.]+)\s*%",
              r"(?<![a-z])nii_percentage[^%]{0,60}?([\d.]+)\s*%")
    anchor = round(qib * 0.6, 2) if qib is not None else None
    return {"pct_qib": qib, "pct_retail": retail, "pct_nii": nii, "pct_anchor": anchor}


KPI_ROWS = [("kpi_roe", r"\bROE\b"), ("kpi_roce", r"\bROCE\b"),
            ("kpi_debt_equity", r"Debt\s*/\s*Equity"), ("kpi_pat_margin", r"PAT\s+Margin"),
            ("kpi_ebitda_margin", r"EBITDA\s+Margin"), ("kpi_pb", r"Price to Book Value")]


def parse_kpis(html):
    """Chittorgarh's published KPI block — the same figures the source sheet was
    filled from by hand. Already in sheet units (ROE/margins as %, D-E and P/B as
    ratios), so no scaling. Multi-year rows list latest first; the Pre-IPO/Post-IPO
    pair lists pre then post, so P/E takes the second figure."""
    txt = _space_text(html)
    i = txt.find("Key Performance Indicator")
    if i < 0:
        return {}
    blk = txt[i:i + 800]
    out = {}
    for key, pat in KPI_ROWS:
        m = re.search(pat + r"\s+(-?[\d.]+)\s*%?", blk, re.I)
        if m:
            try:
                out[key] = round(float(m.group(1)), 2)
            except ValueError:
                pass
    m = re.search(r"P\s*/\s*E\s*\(x\)\s+(-?[\d.]+)(?:\s+(-?[\d.]+))?", blk, re.I)
    if m:
        try:
            out["kpi_post_ipo_pe"] = round(float(m.group(2) or m.group(1)), 2)
        except ValueError:
            pass
    return out


def refresh_details():
    issue = pd.read_csv(DATA / "cg_issue.csv")
    issue["cg_ipo_id"] = sid_col(issue["cg_ipo_id"])
    lst = pd.read_csv(DATA / "cg_listing.csv")
    lst.columns = [c.split("<")[0].strip() for c in lst.columns]
    lst["cg_ipo_id"] = sid_col(lst["cg_ipo_id"])
    sym_by = {r["cg_ipo_id"]: str(r.get("NSE Symbol") or "").strip()
              for _, r in lst.iterrows()}

    cache = pd.read_csv(DETAILS_CSV, dtype={"cg_ipo_id": str}) if DETAILS_CSV.exists() \
        else pd.DataFrame(columns=["cg_ipo_id"])
    if len(cache):
        cache["cg_ipo_id"] = sid_col(cache["cg_ipo_id"])
    have = {r["cg_ipo_id"]: dict(r) for _, r in cache.iterrows()}
    issue["open_dt"] = pdate(issue["Opening Date"])
    window = issue[issue["open_dt"] >= NOW - timedelta(days=DETAILS_WINDOW_DAYS)] \
        .sort_values("open_dt", ascending=False)

    ysess = None
    done = 0
    for _, r in window.iterrows():
        cid = r["cg_ipo_id"]
        rec = have.get(cid, {"cg_ipo_id": cid})
        need_res = not any(rec.get(k) not in (None, "") and pd.notna(rec.get(k))
                           for k in ("pct_qib", "pct_retail", "kpi_roe", "kpi_pb"))
        need_sec = rec.get("sector") in (None, "") or pd.isna(rec.get("sector"))
        if not (need_res or need_sec):
            continue
        # An upcoming IPO has no reservation table yet and an unlisted one has no
        # Yahoo profile, so a miss is normal. Retry those weekly rather than every
        # run, or they'd eat the whole per-run budget and new IPOs would never
        # get fetched.
        last = rec.get("details_fetched")
        if last not in (None, "") and pd.notna(last):
            try:
                if (NOW - pd.to_datetime(last)).days < 7:
                    continue
            except (ValueError, TypeError):
                pass
        if done >= MAX_NEW_DETAILS_PER_RUN:
            break
        if need_res and str(r.get("detail_url") or "").startswith("http"):
            try:
                html = http(r["detail_url"], referer="https://www.chittorgarh.com/").decode("utf-8", "replace")
                rec.update(parse_reservation(html))
                rec.update(parse_kpis(html))
            except Exception as e:
                print(f"  detail {r['company'][:32]}: {e}")
        if need_sec:
            sym = sym_by.get(cid, "")
            if sym and sym.lower() not in ("nan", "na", "-", ""):
                try:
                    if ysess is None:
                        ysess = yahoo_session()
                    rec["sector"] = yahoo_sector(ysess[0], ysess[1], sym.upper() + ".NS")
                except Exception as e:
                    print(f"  sector {r['company'][:32]}: {e}")
        rec["details_fetched"] = str(NOW.date())
        have[cid] = rec
        done += 1
        time.sleep(0.4)
    pd.DataFrame(have.values()).to_csv(DETAILS_CSV, index=False)
    print(f"  details: {done} fetched, {len(have)} cached")


def parse_ipowatch(html):
    """Mainboard IPO -> GMP (₹) from the ipowatch GMP table."""
    out = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        txt = _space_text(tr)
        if "Mainboard" not in txt:
            continue
        m = re.match(r"(.+?)\s+₹\s*([\d,]+)", txt)
        if not m:
            continue
        name = re.sub(r"\s+IPO$", "", m.group(1)).strip()
        gmp = num(m.group(2))
        key = " ".join(norm_tokens(name))
        if key and np.isfinite(gmp):
            out[key] = gmp
    return out


def refresh_gmp():
    issue = pd.read_csv(DATA / "cg_issue.csv")
    issue["cg_ipo_id"] = sid_col(issue["cg_ipo_id"])
    issue["open_dt"] = pdate(issue["Opening Date"])
    issue["close_dt"] = pdate(issue["Closing Date"])
    # "open" = subscription window is current or imminent (GMP only exists then)
    live = issue[(issue["open_dt"] <= NOW + timedelta(days=10))
                 & (issue["close_dt"] >= NOW - timedelta(days=3))]
    if live.empty:
        print("  gmp: no open IPOs")
        return
    try:
        html = http("https://ipowatch.in/ipo-grey-market-premium-latest-ipo-gmp/",
                    referer="https://ipowatch.in/").decode("utf-8", "replace")
        table = parse_ipowatch(html)
    except Exception as e:
        print(f"  gmp source failed ({e}); skipping")
        return
    cache = pd.read_csv(GMP_CSV, dtype={"cg_ipo_id": str}) if GMP_CSV.exists() \
        else pd.DataFrame(columns=["cg_ipo_id"])
    have = {r["cg_ipo_id"]: dict(r) for _, r in cache.iterrows()}
    hits = 0
    for _, r in live.iterrows():
        toks = set(norm_tokens(r["company"]))
        best = None
        for key, gmp in table.items():
            kt = set(key.split())
            if kt and (kt <= toks or toks <= kt or len(kt & toks) >= max(2, len(kt) - 1)):
                best = gmp
                break
        if best is None:
            continue
        offer = num(r.get("Issue Price (Rs.)"))
        have[r["cg_ipo_id"]] = {
            "cg_ipo_id": r["cg_ipo_id"], "company": r["company"], "gmp": best,
            "estimated_price": round(offer + best, 2) if np.isfinite(offer) else None,
            "gmp_fetched": str(NOW.date())}
        hits += 1
    pd.DataFrame(have.values()).to_csv(GMP_CSV, index=False)
    print(f"  gmp: {hits} open IPOs matched, {len(have)} cached")


# ─────────────────────────── 4. site payload ───────────────────────────

def build_site():
    issue = pd.read_csv(DATA / "cg_issue.csv")
    subs = pd.read_csv(DATA / "cg_subs.csv")
    lst = pd.read_csv(DATA / "cg_listing.csv")
    lst.columns = [c.split("<")[0].strip() for c in lst.columns]
    outc_p = DATA / "ipo_outcomes.csv"
    outc = pd.read_csv(outc_p) if outc_p.exists() else pd.DataFrame(columns=["cg_ipo_id"])
    for df in (issue, subs, lst, outc):
        if "cg_ipo_id" in df.columns:
            df["cg_ipo_id"] = df["cg_ipo_id"].map(
                lambda v: str(int(float(v))) if pd.notna(v) and str(v).replace(".", "").isdigit() else str(v))
    subs_by = {r["cg_ipo_id"]: r for _, r in subs.iterrows()}
    lst_by = {r["cg_ipo_id"]: r for _, r in lst.iterrows()}
    outc_by = {r["cg_ipo_id"]: r for _, r in outc.iterrows()}

    issue["open_dt"] = pdate(issue["Opening Date"])
    issue["list_dt"] = pdate(issue["Listing Date"])
    window = issue[(issue["list_dt"] >= NOW - timedelta(days=SITE_WINDOW_DAYS))
                   | (issue["list_dt"].isna() & (issue["open_dt"] >= NOW - timedelta(days=120)))
                   | (issue["open_dt"] >= NOW)]
    ipos = []
    for _, r in window.sort_values("open_dt", ascending=False).iterrows():
        cid = r["cg_ipo_id"]
        s, l, o = subs_by.get(cid), lst_by.get(cid), outc_by.get(cid)
        rep_p = REPORTS / f"{cid}.json"
        rep = json.loads(rep_p.read_text(encoding="utf-8")) if rep_p.exists() else None
        # report 25 carries a 0.00 placeholder until a company actually lists
        actually_listed = pd.notna(r["list_dt"]) and r["list_dt"] <= NOW
        day1 = num(l.get("% Gain/Loss (Issue price v/s close price on Listing)")) \
            if (l is not None and actually_listed) else np.nan
        signals = {}
        if s is not None:
            for src, key in (("sub_qib", "QIB (x)"), ("sub_bnii", "bNII (x)"),
                             ("sub_snii", "sNII (x)"), ("sub_nii", "NII (x)"),
                             ("sub_rii", "Retail (x)")):
                v = num(s.get(key))
                if np.isfinite(v):
                    signals[src] = v
        if np.isfinite(day1):
            signals["day1_gain"] = day1
        fc = None
        if rep:
            try:
                full = listing_predictor.forecast(rep, signals=signals or None)
                fc = {"rules": full.get("rules"), "horizons": (full.get("ml_horizons") or {}).get("horizons"),
                      "entry": (full.get("ml_horizons") or {}).get("entry"),
                      "exit": (full.get("ml_horizons") or {}).get("exit"),
                      "inputs_used": (full.get("ml_horizons") or {}).get("inputs_used")}
            except Exception as e:
                print(f"  forecast failed {r['company'][:40]}: {e}")
        row = {
            "cg_ipo_id": cid, "name": r["company"], "url": r.get("detail_url"),
            "open": str(r["open_dt"].date()) if pd.notna(r["open_dt"]) else None,
            "close": r.get("Closing Date") or None,
            "listing": str(r["list_dt"].date()) if pd.notna(r["list_dt"]) else None,
            "offer_price": num(r.get("Issue Price (Rs.)")),
            "issue_cr": num(r.get("Total Issue Amount (Incl.Firm reservations) (Rs.cr.)")),
            "fresh_cr": num(r.get("Fresh Capital (Rs.cr.)")), "ofs_cr": num(r.get("Offer for sale (Rs.cr.)")),
            "lead": r.get("Left Lead Manager") or None,
            "subs": {k.replace("sub_", ""): v for k, v in signals.items() if k.startswith("sub_")} or None,
            "day1_gain": day1 if np.isfinite(day1) else None,
            "rhp": ({"score": (rep.get("scoring") or {}).get("overall"),
                     "verdict": rep.get("verdict"),
                     "valuation": (rep.get("valuation") or {}).get("call"),
                     "risk_score": (rep.get("risk") or {}).get("score"),
                     "coverage": (rep.get("meta") or {}).get("coverage"),
                     "flags": [f.get("name") for f in (rep.get("forensic") or {}).get("flags") or []][:4],
                     "bull": [c.get("text") for c in (rep.get("cases") or {}).get("bull") or []][:2],
                     "bear": [c.get("text") for c in (rep.get("cases") or {}).get("bear") or []][:2]}
                    if rep else None),
            "forecast": fc,
            "actual": ({"current_ret": num(o.get("current_ret")),
                        "ret_6m": num(o.get("ret_6m")), "ret_12m": num(o.get("ret_12m")),
                        "bottom_so_far_pct": num(o.get("bottom_so_far_pct")),
                        "bottom_so_far_session": num(o.get("bottom_so_far_session"))}
                       if o is not None else None),
        }
        ipos.append(row)

    def _clean(v):
        if isinstance(v, dict):
            return {k: _clean(x) for k, x in v.items()}
        if isinstance(v, list):
            return [_clean(x) for x in v]
        if isinstance(v, (np.floating, np.integer)):
            v = float(v)
        if isinstance(v, float):
            return round(v, 2) if np.isfinite(v) else None
        return v

    payload = {"generated_at": datetime.utcnow().isoformat() + "Z",
               "count": len(ipos), "ipos": _clean(ipos),
               "disclaimer": "Automated document analysis for research and education. Not investment "
                             "advice, not a recommendation, not a SEBI-registered research report."}
    (SITE_DATA / "ipos.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"  site payload: {len(ipos)} IPOs")


# ─────────────────────────── 5. Excel rebuild ───────────────────────────

def rebuild_excel():
    from build_dataset import build
    build()


# ─────────────────────────── seeding from the local API ───────────────────────────

def seed_from_local_api():
    """One-time: map the locally analyzed corpus onto cg ids so CI never has to
    re-analyze history. Requires the local backend on :8001."""
    api = "http://localhost:8001/api"
    analyses = json.loads(http(api + "/analyses", referer=""))
    by_key = {}
    for a in analyses:
        if a["status"] == "completed" and a.get("company_name") and not a.get("is_demo"):
            by_key.setdefault(" ".join(norm_tokens(a["company_name"])), a["id"])
    issue = pd.read_csv(DATA / "cg_issue.csv")
    issue["cg_ipo_id"] = issue["cg_ipo_id"].astype(str)
    issue["open_dt"] = pdate(issue["Opening Date"])
    recent = issue[issue["open_dt"] >= NOW - timedelta(days=SITE_WINDOW_DAYS + 300)]
    got = 0
    for _, r in recent.iterrows():
        out = REPORTS / f"{r['cg_ipo_id']}.json"
        if out.exists():
            continue
        aid = by_key.get(" ".join(norm_tokens(r["company"])))
        if not aid:
            continue
        try:
            rep = json.loads(http(f"{api}/analyses/{aid}/report", referer=""))
            out.write_text(json.dumps(compact_report(rep), ensure_ascii=False), encoding="utf-8")
            got += 1
        except Exception as e:
            print(f"  seed failed {r['company'][:40]}: {e}")
    print(f"  seeded {got} reports from local corpus")


def main():
    seed = "--seed" in sys.argv
    print("1) chittorgarh refresh")
    try:
        refresh_chittorgarh()
    except Exception as e:
        print(f"  FAILED: {e}")
    print("2) yahoo outcomes")
    try:
        refresh_outcomes()
    except Exception as e:
        print(f"  FAILED: {e}")
    if seed:
        print("3) seeding reports from local API")
        seed_from_local_api()
    else:
        print("3) sebi rhp fetch+analyze")
        try:
            ensure_rhp_reports()
        except Exception as e:
            print(f"  FAILED: {e}")
    print("3b) sector / reservation")
    try:
        refresh_details()
    except Exception as e:
        print(f"  FAILED: {e}")
    print("3c) grey-market premium")
    try:
        refresh_gmp()
    except Exception as e:
        print(f"  FAILED: {e}")
    print("4) site payload")
    build_site()
    print("5) excel rebuild")
    try:
        rebuild_excel()
    except Exception as e:
        print(f"  FAILED: {e}")
    print("done")


if __name__ == "__main__":
    main()
