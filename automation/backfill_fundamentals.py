"""Retrofit the `fundamentals` block onto report JSONs analyzed before it existed.

update.compact_report() now keeps the latest-FY ratios the analyzer already
computes (ROE / ROCE / D-E / PAT + EBITDA margin / post-issue P/E), so every
newly analyzed RHP carries them. This backfills the ones stored earlier, reading
the analyzer's own SQLite corpus directly — the backend does NOT need to be running.

    python automation/backfill_fundamentals.py
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "backend" / "rhp.db"
DATA = ROOT / "data"
REPORTS = ROOT / "docs" / "data" / "reports"


def norm_tokens(name):
    s = re.sub(r"[^a-z0-9 ]", " ", str(name).lower())
    drop = {"limited", "ltd", "private", "pvt", "rhp", "the", "and", "of", "red",
            "herring", "prospectus", "dated", "co", "company"}
    return [w for w in s.split() if w and w not in drop]


def fundamentals(rep: dict) -> dict | None:
    """Same shape update.compact_report() writes; ratios stay fractions."""
    ratios = (rep.get("financials") or {}).get("ratios") or {}
    f = {"pat_margin": ratios.get("net_margin"), "ebitda_margin": ratios.get("operating_margin"),
         "roe": ratios.get("roe"), "roce": ratios.get("roce"),
         "debt_equity": ratios.get("debt_equity"),
         "post_ipo_pe": (rep.get("valuation") or {}).get("issuer_pe")}
    return f if any(v is not None for v in f.values()) else None


def main():
    if not DB.exists():
        print(f"no local corpus at {DB} — nothing to backfill")
        return
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT a.company_name, r.report_json FROM reports r "
        "JOIN analyses a ON a.id = r.analysis_id "
        "WHERE a.status = 'completed' AND COALESCE(a.is_demo, 0) = 0").fetchall()
    con.close()

    by_key: dict[str, str] = {}
    for name, rj in rows:
        if name and rj:
            by_key.setdefault(" ".join(norm_tokens(name)), rj)

    issue = pd.read_csv(DATA / "cg_issue.csv")
    patched = had = nomatch = 0
    for _, r in issue.iterrows():
        cid = str(r["cg_ipo_id"]).split(".")[0]
        path = REPORTS / f"{cid}.json"
        if not path.exists():
            continue
        rep = json.loads(path.read_text(encoding="utf-8"))
        if rep.get("fundamentals"):
            had += 1
            continue
        rj = by_key.get(" ".join(norm_tokens(r["company"])))
        if not rj:
            nomatch += 1
            continue
        fund = fundamentals(json.loads(rj))
        if not fund:
            continue
        rep["fundamentals"] = fund
        path.write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")
        patched += 1
    print(f"  fundamentals: {patched} patched, {had} already had, {nomatch} unmatched")


if __name__ == "__main__":
    main()
