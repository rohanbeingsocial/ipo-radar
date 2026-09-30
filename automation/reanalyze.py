"""Re-analyze every IPO's prospectus with the current analyzer (the VM's full pass).

RHP scores are only comparable within one analyzer version (see retrain.py), so after
any change under backend/app or backend/tools the whole corpus is redone before the
dataset is rebuilt and the models retrained. Every report records the version that wrote
it (meta.analyzer_version), which makes this resumable: reports already on the current
version are skipped, so an interrupted pass just runs again.

The prospectus comes from the IPO's Chittorgarh page (cg_records.json): the RHP link,
else the final prospectus. A failed IPO keeps its previous report and is listed at the end.

    python automation/reanalyze.py --workers 3 --until 08:30
    python automation/reanalyze.py --list          # how many are stale, fetch nothing
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "automation"))
DATA = ROOT / "data"
REPORTS = ROOT / "docs" / "data" / "reports"


def stale(version: str):
    recs = json.loads((DATA / "cg_records.json").read_text(encoding="utf-8"))
    issue = pd.read_csv(DATA / "cg_issue.csv", dtype={"cg_ipo_id": str})
    issue["open_dt"] = pd.to_datetime(issue["Opening Date"], format="%d-%b-%Y", errors="coerce")
    now = pd.Timestamp.now()
    todo = []
    for _, r in issue.sort_values("open_dt", ascending=False).iterrows():
        cid = str(r["cg_ipo_id"])
        if pd.isna(r["open_dt"]) or r["open_dt"] > now + pd.Timedelta(days=10):
            continue
        p = REPORTS / f"{cid}.json"
        if p.exists():
            meta = json.loads(p.read_text(encoding="utf-8")).get("meta") or {}
            if meta.get("analyzer_version") == version:
                continue
        rec = recs.get(cid, {})
        urls = [u for u in (rec.get("prospectus_rhp"), rec.get("final_prospectus"))
                if isinstance(u, str) and u.startswith("http")]
        if urls:
            todo.append((cid, r["company"], urls, r.get("Issue Price (Rs.)")))
    return todo


def work(item):
    import update  # heavy import (analyzer), once per worker process
    cid, name, urls, offer = item
    err = None
    for u in urls:
        try:
            update.fetch_and_analyze(u, REPORTS / f"{cid}.json", offer_price=offer)
            return cid, name, None
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {str(e)[:150]}"
    return cid, name, err


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--until", metavar="HH:MM", help="stop taking new prospectuses after this time")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    import update
    version = update.code_version()
    todo = stale(version)
    print(f"analyzer {version}: {len(todo)} prospectuses to (re)analyze", flush=True)
    if a.list or not todo:
        return 0
    deadline = None
    if a.until:
        h, m = map(int, a.until.split(":"))
        deadline = datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
        if deadline <= datetime.now():
            deadline += timedelta(days=1)
    done, failed = 0, []
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        pending = iter(todo)
        futs = set()
        for _ in range(a.workers):
            nxt = next(pending, None)
            if nxt:
                futs.add(ex.submit(work, nxt))
        while futs:
            f = next(as_completed(futs))
            futs.remove(f)
            cid, name, err = f.result()
            if err:
                failed.append((cid, name, err))
            else:
                done += 1
            if (done + len(failed)) % 25 == 0:
                print(f"  {done} done, {len(failed)} failed, {len(todo) - done - len(failed)} left "
                      f"({datetime.now():%H:%M})", flush=True)
            if deadline is None or datetime.now() < deadline:
                nxt = next(pending, None)
                if nxt:
                    futs.add(ex.submit(work, nxt))
    left = len(stale(version))
    print(f"reanalyzed {done}, failed {len(failed)}, still stale {left}", flush=True)
    for cid, name, err in failed:
        print(f"  FAILED {cid} {name[:40]}: {err}")
    (DATA / "reanalysis_status.json").write_text(json.dumps(
        {"analyzer_version": version, "finished_at": datetime.now().isoformat(timespec="seconds"),
         "reanalyzed": done, "failed": [{"cg_ipo_id": c, "company": n, "error": e} for c, n, e in failed],
         "still_stale": left}, indent=1), encoding="utf-8")
    return 0 if left == len(failed) else 2   # 2 = stopped at the deadline with work left


if __name__ == "__main__":
    sys.exit(main())
