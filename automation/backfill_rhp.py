"""Backfill RHP reports for IPOs that already have an outcome, so the RHP score can be TESTED.

The daily job only looks 270 days back and takes 6 RHPs a run, so the corpus (163 reports)
is almost entirely recent IPOs — and recent IPOs are the ones whose 12m/24m returns do not
exist yet. That left the central question unanswerable: *does our own RHP score predict
anything?* You cannot check a score against outcomes it has never been paired with.

So this walks SEBI's filings archive (which reaches back to 2007) and analyzes prospectuses
for IPOs that ALREADY have a matured return label. Priority is by label value, not recency:
an IPO with a 24m return teaches more than one that listed last week.

    python automation/backfill_rhp.py --limit 200        # bounded, resumable
    python automation/backfill_rhp.py --pages 60         # how deep to walk SEBI
    python automation/backfill_rhp.py --list             # show the queue, fetch nothing
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "automation"))
sys.path.insert(0, str(ROOT / "backend"))
from update import (DATA, REPORTS, SEBI_AJAX, fetch_and_analyze, http,  # noqa: E402
                    norm_tokens, pdate)

ROW_RE = re.compile(
    r'<td>([A-Z][a-z]{2} \d{2}, \d{4})</td>\s*<td><a href=[\'"]'
    r'(https://www\.sebi\.gov\.in/filings/[^\'"]+)[\'"][^>]*title=["\']([^"\']+)["\']', re.S)
SKIP_RE = re.compile(r"corrigendum|addendum|\bnotice\b|clarification|abridged", re.I)


def sebi_archive(pages):
    """Walk the filings list deep. Page 1 is ~today; page 40 is ~2007."""
    entries, seen = [], set()
    for page in range(1, pages + 1):
        body = (f"nextValue={page}&next=n&search=&fromDate=&toDate=&fromYear=&toYear="
                f"&deptId=&sid=3&ssid=15&smid=11&ssidhidden=15&intmid=-1&sText=Filings")
        try:
            html = http(SEBI_AJAX, data=body,
                        referer="https://www.sebi.gov.in/sebiweb/home/HomeAction.do").decode("utf-8", "replace")
        except Exception as e:                                   # noqa: BLE001
            print(f"  sebi page {page} failed ({e}); stopping walk")
            break
        rows = ROW_RE.findall(html)
        if not rows:
            print(f"  sebi page {page}: end of archive")
            break
        for d, u, t in rows:
            if u in seen or SKIP_RE.search(t):
                continue
            seen.add(u)
            entries.append({"date": pd.to_datetime(d), "url": u, "title": t.strip(),
                            "toks": set(norm_tokens(t))})
        if page % 10 == 0:
            print(f"  sebi page {page}: {len(entries)} filings so far "
                  f"(oldest {min(e['date'] for e in entries).date()})")
        time.sleep(0.8)
    return entries


def queue():
    """IPOs with no report, ordered by how much they can teach us: a matured 24m label
    beats a 12m, beats a 6m, beats a listing gain, beats nothing."""
    issue = pd.read_csv(DATA / "cg_issue.csv", dtype={"cg_ipo_id": str})
    issue.columns = [c.split("<")[0].strip() for c in issue.columns]
    outc = pd.read_csv(DATA / "ipo_outcomes.csv", dtype={"cg_ipo_id": str}).set_index("cg_ipo_id")
    issue["open_dt"] = pdate(issue["Opening Date"])

    rows = []
    for _, r in issue.iterrows():
        cid = r["cg_ipo_id"]
        if (REPORTS / f"{cid}.json").exists():
            continue
        o = outc.loc[cid] if cid in outc.index else None
        has = lambda k: o is not None and pd.notna(o.get(k))   # noqa: E731
        weight = (3 if has("ret_24m") else 2 if has("ret_12m") else
                  1 if has("ret_6m") else 0)
        if weight == 0:
            continue                       # no outcome => cannot test the score against it
        rows.append({"cg_ipo_id": cid, "company": r["company"], "open_dt": r["open_dt"],
                     "weight": weight})
    q = pd.DataFrame(rows)
    if q.empty:
        return q

    # STRATIFY BY YEAR, round-robin — do not just take the newest first.
    #
    # The whole point of this backfill is to test whether the RHP score predicts anything,
    # and you cannot test that on a single era: the existing corpus is 100% 2023-2026, so
    # any relationship found in it is confounded with one particular market regime. Taking
    # the newest-first would have deepened exactly the era we already have. Interleaving
    # by listing year means that even a partial run (it is slow — one prospectus is a
    # 400-page PDF) yields a sample spread across a decade, which is the sample that can
    # actually answer the question.
    q["year"] = q["open_dt"].dt.year
    q = q.sort_values(["year", "weight", "open_dt"], ascending=[False, False, False])
    q["rank_in_year"] = q.groupby("year").cumcount()
    return (q.sort_values(["rank_in_year", "weight", "year"], ascending=[True, False, False])
              .drop(columns=["rank_in_year"]).reset_index(drop=True))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=150)
    ap.add_argument("--pages", type=int, default=50)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    q = queue()
    have = len(list(REPORTS.glob("*.json")))
    print(f"reports on disk: {have}   missing-with-an-outcome: {len(q)}")
    if q.empty:
        return
    print(q["weight"].value_counts().rename({3: "has 24m", 2: "has 12m", 1: "has 6m"}).to_string())
    if a.list:
        print(q.head(30).to_string())
        return

    print(f"\nwalking SEBI archive ({a.pages} pages)...")
    entries = sebi_archive(a.pages)
    print(f"  {len(entries)} filings, {min(e['date'] for e in entries).date()} .. "
          f"{max(e['date'] for e in entries).date()}")

    done = failed = nomatch = 0
    t0 = time.time()
    for _, r in q.iterrows():
        if done >= a.limit:
            break
        toks = set(norm_tokens(r["company"]))
        if not toks:
            continue
        # the RHP is filed shortly BEFORE the issue opens; require the name tokens to be a
        # subset of the filing title, which is what keeps "XYZ Ltd" off "XYZ Cement Ltd"
        cands = [e for e in entries if toks <= e["toks"]
                 and (pd.isna(r["open_dt"]) or
                      r["open_dt"] - timedelta(days=280) <= e["date"] <= r["open_dt"] + timedelta(days=45))]
        if not cands:
            nomatch += 1
            continue
        cands.sort(key=lambda e: abs((e["date"] - r["open_dt"]).days))
        try:
            fetch_and_analyze(cands[0]["url"], REPORTS / f"{r['cg_ipo_id']}.json")
            done += 1
            rate = (time.time() - t0) / done
            print(f"  [{done}/{a.limit}] {r['company'][:44]:46} "
                  f"({r['open_dt'].date() if pd.notna(r['open_dt']) else '?'})  {rate:.0f}s/report")
        except Exception as e:                                   # noqa: BLE001
            failed += 1
            print(f"  FAIL {r['company'][:40]}: {str(e)[:60]}")

    print(f"\nanalyzed {done} new  |  {failed} failed  |  {nomatch} had no SEBI filing match")
    print(f"reports on disk now: {len(list(REPORTS.glob('*.json')))}")


if __name__ == "__main__":
    main()
