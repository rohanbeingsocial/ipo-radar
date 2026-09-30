"""Minimal Dhan v2 REST client: index candles and last traded prices.

Credentials are never stored in this repo. They are read from the environment
(DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN) or, on the VM, from IPOSeller's .env
(DHAN_ENV_FILE, default ~/iposeller/.env) — IPOSeller already rotates that
token daily, so this pipeline rides on it instead of keeping its own.
Everything here is optional: with no (or an expired) token every call returns
None and callers fall back to Yahoo / Chittorgarh.
"""
from __future__ import annotations

import csv
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.dhan.co/v2"
SCRIP_MASTER = "https://images.dhan.co/api-data/api-scrip-master.csv"
NIFTY_ID = "13"            # NIFTY 50 on the IDX_I segment

_creds = None


def creds():
    """(client_id, access_token) or None."""
    global _creds
    if _creds is not None:
        return _creds or None
    cid, tok = os.environ.get("DHAN_CLIENT_ID"), os.environ.get("DHAN_ACCESS_TOKEN")
    if not (cid and tok):
        env = Path(os.environ.get("DHAN_ENV_FILE", Path.home() / "iposeller" / ".env"))
        if env.exists():
            kv = {}
            for line in env.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    kv[k.strip()] = v.strip().strip('"').strip("'")
            cid, tok = kv.get("DHAN_CLIENT_ID"), kv.get("DHAN_ACCESS_TOKEN")
    _creds = (cid, tok) if cid and tok else ()
    return _creds or None


def _post(path: str, body: dict, timeout=60):
    c = creds()
    if not c:
        return None
    req = urllib.request.Request(
        API + path, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "client-id": c[0], "access-token": c[1]})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def token_ok() -> bool:
    c = creds()
    if not c:
        return False
    try:
        req = urllib.request.Request(API + "/profile", headers={"access-token": c[1],
                                                               "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001 - expired token is a 401; any failure means "no Dhan"
        return False


def _candles(d):
    if not d or not d.get("timestamp"):
        return []
    keys = ("open", "high", "low", "close")
    return [(int(t), *(float(d[k][i]) for k in keys)) for i, t in enumerate(d["timestamp"])]


def nifty_daily(from_date: str, to_date: str):
    """[(epoch, o, h, l, c)] daily candles for NIFTY 50, or [] without Dhan."""
    try:
        d = _post("/charts/historical", {"securityId": NIFTY_ID, "exchangeSegment": "IDX_I",
                                         "instrument": "INDEX", "expiryCode": 0, "oi": False,
                                         "fromDate": from_date, "toDate": to_date})
    except Exception:  # noqa: BLE001
        return []
    return _candles(d)


def nifty_minutes(day: str):
    """[(epoch, o, h, l, c)] 1-minute candles for NIFTY 50 on one day; [] when Dhan has
    none for that day; None when the request itself failed (rate limit, expired token),
    so callers don't mistake a throttled call for a day without data."""
    try:
        d = _post("/charts/intraday", {"securityId": NIFTY_ID, "exchangeSegment": "IDX_I",
                                       "instrument": "INDEX", "interval": "1", "oi": False,
                                       "fromDate": f"{day} 09:15:00", "toDate": f"{day} 15:30:00"})
    except urllib.error.HTTPError as e:
        # Dhan answers a day outside its history with a 400 "no data" error
        body = e.read().decode("utf-8", "replace") if e.fp else ""
        return [] if e.code == 400 and re.search(r"(?i)no data|DH-905|DH-907", body) else None
    except Exception:  # noqa: BLE001
        return None
    return _candles(d)


def scrip_ids(cache: Path):
    """{("NSE", symbol): security_id, ("BSE", scrip_code): security_id} for equities.
    The scrip master is ~25 MB, so it is cached for a day."""
    if not cache.exists() or time.time() - cache.stat().st_mtime > 86400:
        req = urllib.request.Request(SCRIP_MASTER, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(r.read())
    out = {}
    with cache.open(encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            if row.get("SEM_INSTRUMENT_NAME") != "EQUITY":
                continue
            exch, sid = row.get("SEM_EXM_EXCH_ID"), row.get("SEM_SMST_SECURITY_ID")
            if exch == "NSE" and row.get("SEM_SERIES") in ("EQ", "BE", "BZ", "SM", "ST"):
                out.setdefault(("NSE", row.get("SEM_TRADING_SYMBOL", "").upper()), sid)
            elif exch == "BSE":
                out.setdefault(("BSE", sid), sid)
    return out


def ltp(instruments):
    """instruments: [("NSE_EQ" | "BSE_EQ", security_id)] -> {(seg, id): last_price}.
    1,000 instruments per request, 1 request per second (Dhan's quote limit)."""
    out = {}
    items = list(dict.fromkeys(instruments))
    for i in range(0, len(items), 1000):
        body = {}
        for seg, sid in items[i:i + 1000]:
            body.setdefault(seg, []).append(int(sid))
        try:
            d = _post("/marketfeed/ltp", body)
        except Exception:  # noqa: BLE001
            return out
        for seg, rows in ((d or {}).get("data") or {}).items():
            for sid, q in rows.items():
                p = q.get("last_price")
                if p:
                    out[(seg, str(sid))] = float(p)
        time.sleep(1.1)
    return out
