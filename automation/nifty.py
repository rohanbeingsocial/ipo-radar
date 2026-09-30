"""NIFTY 50 levels for the IPO sheet's market columns, cached in data/.

  data/nifty_daily.csv    date, open, high, low, close, source   (Dhan, else Yahoo ^NSEI)
  data/nifty_listing.csv  date, at_10am, source                  (Dhan 1-minute candles)

The hand-built sheet recorded "Nifty 50" during the listing morning (IPOs start
trading at 10:00), "Prev Closing" as the previous session's close, and a monthly
High / Neutral / Low mood. Only Dhan has minute data, so the 10:00 value exists
only where Dhan's intraday history reaches; elsewhere the listing day's open is
used and the source column says so.
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

import dhan

DATA = Path(__file__).resolve().parent.parent / "data"
DAILY = DATA / "nifty_daily.csv"
LISTING = DATA / "nifty_listing.csv"
IST = timezone(timedelta(hours=5, minutes=30))
MOOD_WINDOW = 22            # sessions: ~one month of trading
MOOD_HIGH, MOOD_LOW = 0.65, 0.35


def _to_frame(rows, source):
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "source"])
    idx = [datetime.fromtimestamp(t, IST).date() for t, *_ in rows]
    df = pd.DataFrame([r[1:] for r in rows], columns=["open", "high", "low", "close"],
                      index=pd.to_datetime(idx))
    df["source"] = source
    return df[~df.index.duplicated(keep="last")]


def _nse_daily(start: pd.Timestamp, end: pd.Timestamp):
    """NSE's own index history (the only free source that reaches 2005; Yahoo starts
    Sep 2007, Dhan Jan 2007). NSE wants a browser-like session and serves Indian IPs,
    so this works from the VM and usually not from CI runners."""
    import http.cookiejar
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    op.addheaders = [("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
                     ("Accept-Language", "en-US,en;q=0.9"), ("Accept", "*/*")]
    op.open("https://www.nseindia.com/reports-indices-historical-index-data", timeout=30).read()
    rows = []
    a = start
    while a <= end:
        b = min(a + pd.Timedelta(days=89), end)       # NSE returns at most ~70 rows a call
        url = ("https://www.nseindia.com/api/historicalOR/indicesHistory?indexType=NIFTY%2050"
               f"&from={a:%d-%m-%Y}&to={b:%d-%m-%Y}")
        d = json.loads(op.open(url, timeout=60).read())
        for x in d.get("data") or []:
            t = pd.to_datetime(x["EOD_TIMESTAMP"], format="%d-%b-%Y")
            rows.append((t, x["EOD_OPEN_INDEX_VAL"], x["EOD_HIGH_INDEX_VAL"],
                         x["EOD_LOW_INDEX_VAL"], x["EOD_CLOSE_INDEX_VAL"]))
        a = b + pd.Timedelta(days=1)
        time.sleep(0.3)
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close"]).set_index("date")
    df["source"] = "nse"
    return df[~df.index.duplicated(keep="last")].sort_index()


def _yahoo_daily(start: pd.Timestamp, end: pd.Timestamp):
    p1, p2 = int(start.timestamp()), int(end.timestamp())
    req = urllib.request.Request(f"https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI"
                                 f"?period1={p1}&period2={p2}&interval=1d",
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read())
    res = d["chart"]["result"][0]
    if not res.get("timestamp"):
        return _to_frame([], "yahoo")
    q = res["indicators"]["quote"][0]
    rows = [(t, q["open"][i], q["high"][i], q["low"][i], q["close"][i])
            for i, t in enumerate(res["timestamp"])
            if None not in (q["open"][i], q["high"][i], q["low"][i], q["close"][i])]
    return _to_frame(rows, "yahoo")


def load_daily() -> pd.DataFrame:
    if not DAILY.exists():
        return pd.DataFrame(columns=["open", "high", "low", "close", "source"])
    return pd.read_csv(DAILY, index_col=0, parse_dates=True)


def refresh_daily(since="2004-06-01", log=print) -> pd.DataFrame:
    """Top up the daily cache to today, from NSE (official, back to 2005), else Dhan,
    else Yahoo. Rows already cached from a better source are kept, and a cache that
    is missing the early years (built from Yahoo) gets them from NSE when it can."""
    have = load_daily()
    today = pd.Timestamp.now(tz=IST).tz_localize(None).normalize()
    start = pd.Timestamp(since)
    if not have.empty and (have["source"] == "nse").any() and have.index.min() <= start + pd.Timedelta(days=10):
        start = have.index.max() - pd.Timedelta(days=7)
    parts = [have]
    got = pd.DataFrame()
    try:
        got = _nse_daily(start, today)
    except Exception as e:  # noqa: BLE001
        log(f"  nifty: NSE history unavailable ({type(e).__name__}); trying Dhan / Yahoo")
    if got.empty:
        got = _to_frame(dhan.nifty_daily(start.strftime("%Y-%m-%d"),
                                         (today + pd.Timedelta(days=1)).strftime("%Y-%m-%d")), "dhan")
    parts.append(got)
    if got.empty or got.index.max() < today - pd.Timedelta(days=5):
        try:
            parts.append(_yahoo_daily(max(start, pd.Timestamp("2007-09-01")), today + pd.Timedelta(days=1)))
        except Exception as e:  # noqa: BLE001
            log(f"  nifty: yahoo failed ({e})")
    df = pd.concat([p for p in parts if not p.empty])
    # on overlapping days the better source wins: NSE > Dhan > Yahoo
    df["rank"] = df["source"].map({"nse": 2, "dhan": 1, "yahoo": 0}).fillna(0)
    df = df.sort_values("rank").loc[lambda x: ~x.index.duplicated(keep="last")].drop(columns="rank")
    df = df.sort_index()
    df.to_csv(DAILY, index_label="date")
    log(f"  nifty daily: {len(df)} sessions ({df.index.min().date()} .. {df.index.max().date()})")
    return df


def load_listing() -> pd.DataFrame:
    if not LISTING.exists():
        return pd.DataFrame(columns=["at_10am", "source"])
    return pd.read_csv(LISTING, index_col=0, parse_dates=True)


def refresh_listing(dates, log=print, max_new=400) -> pd.DataFrame:
    """Fetch the 10:00 NIFTY level for listing dates not cached yet (Dhan only).
    Days Dhan has no minutes for are cached as empty so they aren't retried daily."""
    have = load_listing()
    want = sorted({pd.Timestamp(d).normalize() for d in dates if pd.notna(d)} - set(have.index))
    today = pd.Timestamp.now(tz=IST).tz_localize(None).normalize()
    want = [d for d in want if d <= today and (d < today or
            datetime.now(IST).hour >= 10)][:max_new]
    if not want or not dhan.creds():
        return have
    rows = {}
    for d in want:
        mins = dhan.nifty_minutes(d.strftime("%Y-%m-%d"))
        # the hand sheet's 2024 rows equal the close of the 10:00 one-minute candle
        # exactly (16 of 16); fall back to the last candle before it if 10:00 is missing
        at = None
        for t, o, h, lo, c in mins:
            ts = datetime.fromtimestamp(t, IST)
            if (ts.hour, ts.minute) <= (10, 0):
                at = c
            else:
                break
        rows[d] = {"at_10am": at, "source": "dhan_1m" if at is not None else "none"}
    new = pd.DataFrame.from_dict(rows, orient="index")
    df = (new if have.empty else pd.concat([have, new])).sort_index()
    df.to_csv(LISTING, index_label="date")
    log(f"  nifty at listing: {sum(r['at_10am'] is not None for r in rows.values())}"
        f"/{len(rows)} new days from Dhan")
    return df


def listing_row(day, daily: pd.DataFrame, at10: pd.DataFrame) -> dict:
    """The four market columns for one listing date."""
    out = {"nifty": None, "prev_close": None, "change": None, "mood": None, "nifty_source": None}
    if day is None or pd.isna(day) or daily.empty:
        return out
    day = pd.Timestamp(day).normalize()
    if day not in daily.index:
        return out
    i = daily.index.get_loc(day)
    if i == 0:
        return out
    prev = float(daily["close"].iloc[i - 1])
    v, src = None, None
    if day in at10.index and pd.notna(at10.loc[day, "at_10am"]):
        v, src = float(at10.loc[day, "at_10am"]), "dhan_10am"
    elif abs(float(daily["open"].iloc[i]) - prev) > 0.01:
        v, src = float(daily["open"].iloc[i]), "daily_open"
    else:
        # before the 2010 pre-open session NSE printed the index open as the previous
        # close, which would make every old Change 0; the day's close says more
        v, src = float(daily["close"].iloc[i]), "daily_close"
    win = daily.iloc[max(0, i - MOOD_WINDOW + 1):i + 1]
    lo, hi = float(win["low"].min()), float(win["high"].max())
    pos = (v - lo) / (hi - lo) if hi > lo else 0.5
    out.update(nifty=round(v, 2), prev_close=round(prev, 2),
               change=round((v / prev - 1) * 100, 2),
               mood="High" if pos >= MOOD_HIGH else "Low" if pos <= MOOD_LOW else "Neutral",
               nifty_source=src)
    return out
