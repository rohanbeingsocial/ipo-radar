"""Final pre-listing grey-market premium for past IPOs, from InvestorGain's
"IPO GMP Performance Tracker" (report 377), into data/ig_gmp.csv.

The daily ipowatch capture (update.refresh_gmp) only sees IPOs while they are open,
so it can't fill history. InvestorGain keeps the last GMP before listing for
mainboard IPOs from late 2019 on (~475 issues); nothing public goes further back.
Rows are matched to Chittorgarh ids by listing date + company-name tokens.

    python automation/gmp_history.py            # current + previous year
    python automation/gmp_history.py --all      # 2019 .. now
"""
from __future__ import annotations

import re
import sys
import time
import urllib.request
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cg_record  # noqa: E402  (same Next.js flight-data format as Chittorgarh)

DATA = Path(__file__).resolve().parent.parent / "data"
OUT = DATA / "ig_gmp.csv"
URL = "https://www.investorgain.com/report/ipo-gmp-performance-tracker/377/ipo/?year={year}"
FIRST_YEAR = 2019
STOP = {"ltd", "limited", "the", "and", "of", "india", "ipo", "co", "company"}


def _tokens(name):
    return {w for w in re.sub(r"[^a-z0-9 ]", " ", str(name).lower()).split() if w and w not in STOP}


def _rupees(v):
    # cells carry markup ("<img ... thumbs-up_20x20.png ...>&#8377;115.5"): drop tags first
    s = re.sub(r"<[^>]+>", "", str(v or ""))
    m = re.search(r"(-?[\d,]+(?:\.\d+)?)", re.sub(r"&#8377;|₹", "", s))
    return float(m.group(1).replace(",", "")) if m else None


def fetch_year(year: int) -> list[dict]:
    req = urllib.request.Request(URL.format(year=year), headers=cg_record.UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        html = r.read().decode("utf-8", "replace")
    o = cg_record._object_with(cg_record._flight(html), "reportTableData") or {}
    out = []
    for x in o.get("reportTableData") or []:
        if str(x.get("~IPO_Category", "IPO")).upper() != "IPO":
            continue
        out.append({"ig_name": re.sub(r"<[^>]+>", "", str(x.get("IPO", ""))).strip(),
                    "listing_date": pd.to_datetime(x.get("Listing Dt"), format="%d-%b-%y", errors="coerce"),
                    "gmp": _rupees(x.get("GMP")), "offer": _rupees(x.get("Price")),
                    "est_price": _rupees(x.get("Est Price")),
                    "ig_updated": x.get("~Last Updated")})
    return out


def refresh(all_years=False, log=print) -> int:
    now = pd.Timestamp.now()
    years = range(FIRST_YEAR, now.year + 1) if all_years or not OUT.exists() else (now.year - 1, now.year)
    fresh = []
    for y in years:
        fresh += fetch_year(y)
        time.sleep(1.0)
    issue = pd.read_csv(DATA / "cg_issue.csv", dtype={"cg_ipo_id": str})
    issue["list_dt"] = pd.to_datetime(issue["Listing Date"], format="%d-%b-%Y", errors="coerce")
    by_day = {d: g for d, g in issue.groupby("list_dt")}
    matched = []
    for f in fresh:
        best, score = None, 0.0
        near = [by_day[d] for d in (f["listing_date"] + pd.Timedelta(days=k) for k in (0, -1, 1, -2, 2))
                if d in by_day] if pd.notna(f["listing_date"]) else []
        cands = pd.concat(near) if near else pd.DataFrame(columns=issue.columns)
        t = _tokens(f["ig_name"])
        for _, r in cands.iterrows():
            u = _tokens(r["company"])
            s = len(t & u) / max(1, min(len(t), len(u)))
            if s > score:
                best, score = r["cg_ipo_id"], s
        if best is not None and score >= 0.5:
            matched.append({"cg_ipo_id": str(best), **f})
    new = pd.DataFrame(matched)
    if OUT.exists() and len(new):
        old = pd.read_csv(OUT, dtype={"cg_ipo_id": str})
        new = pd.concat([new, old[~old["cg_ipo_id"].isin(set(new["cg_ipo_id"]))]], ignore_index=True)
    elif OUT.exists():
        new = pd.read_csv(OUT, dtype={"cg_ipo_id": str})
    if len(new):
        new["listing_date"] = pd.to_datetime(new["listing_date"]).dt.strftime("%Y-%m-%d")
        new.sort_values("listing_date", ascending=False).to_csv(OUT, index=False)
    log(f"  gmp history: {len(fresh)} InvestorGain rows, {len(matched)} matched to Chittorgarh, "
        f"{len(new)} kept")
    return len(matched)


if __name__ == "__main__":
    refresh(all_years="--all" in sys.argv)
