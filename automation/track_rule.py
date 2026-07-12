"""Score a FROZEN hypothesis forward, on IPOs it has never seen.

The rule — "institutions keen, retail cold": QIB oversubscription above the era median AND
retail below it — was found by SEARCHING the history. That is the weakest kind of evidence
there is. It survived the checks I could throw at it (positive in all four eras at both 6m
and 12m; bootstrap edge +14.1pp with a 95% CI of [+1.0, +27.0]; sitting at the 98th
percentile of a shuffled-outcome placebo) — but I tested roughly fifteen conditions before
landing on it, and cohorts run 12-27 IPOs per era. Corrected for that search, it is a
hypothesis, not an edge.

There is exactly one way to find out which: freeze it, and let it call IPOs it has never
seen. This file does that and nothing else. It writes down the call BEFORE the outcome
exists, so the record cannot be revised later, and it scores the calls whose outcomes have
since matured.

Deliberately measured FROM THE LISTING PRICE, not the offer price: allotment is a lottery,
and a return measured from the offer bakes in the day-1 pop that most buyers never got.

    python automation/track_rule.py           # record calls on open/new IPOs, score matured ones

Writes data/rule_ledger.json — an append-only record of calls and, once known, outcomes.
NOT INVESTMENT ADVICE. This is a research instrument for finding out whether a pattern is
real; publishing its record honestly includes publishing it failing.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LEDGER = DATA / "rule_ledger.json"

RULE = {
    "name": "institutions-in-retail-out",
    "frozen_at": "2026-07-12",
    "statement": ("QIB subscription above the trailing median AND retail subscription below "
                  "it, at the close of the issue."),
    "claim": ("Higher 6- and 12-month return measured FROM THE LISTING PRICE than IPOs that "
              "fail the rule."),
    "backtest": {"edge_6m_pp": 14.1, "ci95": [1.0, 27.0], "eras_positive": "4/4",
                 "caveat": "found by searching ~15 conditions; cohorts of 12-27 per era"},
    "horizon_months": [6, 12],
}
TRAILING = 60          # "the median" = the last 60 IPOs before this one, never the future


def load():
    if LEDGER.exists():
        try:
            return json.loads(LEDGER.read_text())
        except ValueError:
            pass
    return {"rule": RULE, "calls": []}


def main():
    led = load()
    led["rule"] = RULE
    by_id = {c["cg_ipo_id"]: c for c in led["calls"]}

    issue = pd.read_csv(DATA / "cg_issue.csv", dtype={"cg_ipo_id": str})
    issue.columns = [c.split("<")[0].strip() for c in issue.columns]
    subs = pd.read_csv(DATA / "cg_subs.csv", dtype={"cg_ipo_id": str})
    subs.columns = [c.split("<")[0].strip() for c in subs.columns]
    outc = pd.read_csv(DATA / "ipo_outcomes.csv", dtype={"cg_ipo_id": str}).set_index("cg_ipo_id")
    lst = pd.read_csv(DATA / "cg_listing.csv", dtype={"cg_ipo_id": str})
    lst.columns = [c.split("<")[0].strip() for c in lst.columns]

    num = lambda s: pd.to_numeric(s, errors="coerce")      # noqa: E731
    # dedupe before indexing: a repeated cg_ipo_id makes .get() return a Series, not a value
    s = subs.drop_duplicates("cg_ipo_id").set_index("cg_ipo_id")
    qib = num(s["QIB (x)"]) if "QIB (x)" in s.columns else pd.Series(dtype=float)
    rii = num(s["Retail (x)"]) if "Retail (x)" in s.columns else pd.Series(dtype=float)
    l = lst.drop_duplicates("cg_ipo_id").set_index("cg_ipo_id")
    col = "% Gain/Loss (Issue price v/s close price on Listing)"
    day1 = num(l[col]) if col in l.columns else pd.Series(dtype=float)
    outc = outc[~outc.index.duplicated(keep="last")]
    issue = issue.drop_duplicates("cg_ipo_id")

    issue["open_dt"] = pd.to_datetime(issue["Opening Date"], errors="coerce", dayfirst=True)
    issue = issue.sort_values("open_dt")

    made = scored = 0
    for _, r in issue.iterrows():
        cid = r["cg_ipo_id"]
        q, t = qib.get(cid), rii.get(cid)
        if not (pd.notna(q) and pd.notna(t)):
            continue

        # The thresholds use only IPOs that CLOSED BEFORE this one. Using the full-sample
        # median would let the future set the bar — the same look-ahead that made three
        # other "signals" in this project evaporate under honest testing.
        past = issue[issue["open_dt"] < r["open_dt"]]["cg_ipo_id"].tolist()[-TRAILING:]
        pq = qib.reindex(past).dropna()
        pr = rii.reindex(past).dropna()
        if len(pq) < 20 or len(pr) < 20:
            continue
        calls = bool(q > pq.median() and t <= pr.median())

        rec = by_id.get(cid) or {"cg_ipo_id": cid, "company": r["company"],
                                 "opened": str(r["open_dt"].date()) if pd.notna(r["open_dt"]) else None,
                                 "recorded_at": datetime.now(timezone.utc).isoformat()}
        # a call is never rewritten once made — that is the whole point of a ledger
        if "rule_says" not in rec:
            rec.update(rule_says="BUY-CANDIDATE" if calls else "no",
                       qib=round(float(q), 2), retail=round(float(t), 2),
                       qib_bar=round(float(pq.median()), 2), retail_bar=round(float(pr.median()), 2))
            made += 1

        # outcome, once it exists — from the LISTING price, which is what a buyer earns
        d1 = day1.get(cid)
        if cid in outc.index and pd.notna(d1) and d1 > -95:
            o = outc.loc[cid]
            for h in ("ret_6m", "ret_12m"):
                v = pd.to_numeric(o.get(h), errors="coerce")
                if pd.notna(v) and f"{h}_from_listing" not in rec:
                    rec[f"{h}_from_listing"] = round(((1 + v / 100) / (1 + d1 / 100) - 1) * 100, 1)
                    scored += 1
        by_id[cid] = rec

    led["calls"] = sorted(by_id.values(), key=lambda c: c.get("opened") or "")

    # ---- the scoreboard: how is the frozen rule actually doing? ----
    df = pd.DataFrame(led["calls"])
    board = {}
    for h in ("ret_6m_from_listing", "ret_12m_from_listing"):
        if h not in df.columns:
            continue
        d = df[df[h].notna()]
        hit, miss = d[d["rule_says"] == "BUY-CANDIDATE"][h], d[d["rule_says"] == "no"][h]
        if len(hit) < 5:
            continue
        board[h] = {"n_rule": int(len(hit)), "n_rest": int(len(miss)),
                    "rule_median": round(float(hit.median()), 1),
                    "rest_median": round(float(miss.median()), 1),
                    "edge_pp": round(float(hit.median() - miss.median()), 1),
                    "rule_win_rate": round(float((hit > 0).mean()), 2),
                    "rest_win_rate": round(float((miss > 0).mean()), 2)}
    led["scoreboard"] = board
    led["scoreboard_note"] = (
        "Includes the backtest period, so it is NOT yet an out-of-sample record. The calls "
        "recorded from 2026-07-12 onward are the ones that count; until enough of those "
        "mature, this rule is unproven. Research only — not investment advice.")
    LEDGER.write_text(json.dumps(led, indent=1), encoding="utf-8")

    print(f"ledger -> {LEDGER.relative_to(ROOT)}")
    print(f"  calls recorded: {len(led['calls'])} ({made} new)   outcomes filled: {scored}")
    live = [c for c in led["calls"] if c.get("rule_says") == "BUY-CANDIDATE"
            and "ret_6m_from_listing" not in c]
    print(f"  open calls awaiting an outcome: {len(live)}")
    for h, b in board.items():
        print(f"  {h:22} rule {b['rule_median']:+.1f}% (n={b['n_rule']}, win {b['rule_win_rate']:.0%})  "
              f"vs rest {b['rest_median']:+.1f}% (n={b['n_rest']}, win {b['rest_win_rate']:.0%})  "
              f"edge {b['edge_pp']:+.1f}pp")


if __name__ == "__main__":
    main()
