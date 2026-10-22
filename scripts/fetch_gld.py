#!/usr/bin/env python3
"""
fetch_gld.py - pull the full GLD (SPDR Gold ETF) options chain, every expiry and strike, from free delayed
public sources and write one compact JSON file for the chain page.

  python scripts/fetch_gld.py --prev prev.json --out site/data/latest.json
  python scripts/fetch_gld.py --fixture tests/fixture_cboe.json ...   # offline test, no network

Sources, tried in order:  1) Cboe delayed quotes JSON   2) Yahoo Finance options (fallback)
Standard library only, so the GitHub runner needs no pip install.

Output cell per contract:  [last, bid, ask, iv%, delta, oi, oiPrev, vh]
    vh = volumes at the last 5 snapshots (oldest -> newest), so the page can show 15-min / 1-hour volume change.
    oiPrev = open interest as of the previous trading day, so the page can show day-over-day OI change.
Turnover is NOT in these feeds; the page estimates it as volume x price x 100.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import math
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SYMBOL = "GLD"
ET = ZoneInfo("America/New_York")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json"
OCC = re.compile(r"^(?P<root>[A-Z0-9]{1,6}?)(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})(?P<cp>[CP])(?P<k>\d{8})$")
HIST = 5                     # snapshots of volume history kept per contract
ROTATE_MIN_GAP = 10          # minutes; a manual re-run inside this gap overwrites instead of adding a snapshot


# ----------------------------------------------------------------------------- tiny helpers
def num(v):
    try:
        if v is None or v == "":
            return None
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def rnd(v, n=4):
    return None if v is None else round(v, n)


def in_session(now_utc: datetime) -> bool:
    t = now_utc.astimezone(ET)
    if t.weekday() >= 5:
        return False
    mins = t.hour * 60 + t.minute
    return 9 * 60 + 30 <= mins <= 16 * 60 + 20


def get(url, headers=None, opener=None, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json,text/plain,*/*", **(headers or {})})
    op = opener.open if opener else urllib.request.urlopen
    with op(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


# ----------------------------------------------------------------------------- source 1: Cboe
def parse_cboe(j):
    d = j.get("data", j)
    spot = num(d.get("current_price")) or num(d.get("close")) or num(d.get("last_trade_price"))
    prev_close = num(d.get("prev_day_close"))
    out = {}
    for o in d.get("options", []):
        m = OCC.match(str(o.get("option", "")).replace(" ", ""))
        if not m:
            continue
        exp = date(2000 + int(m["yy"]), int(m["mm"]), int(m["dd"]))
        iv = num(o.get("iv"))
        if iv is not None and iv <= 5:        # Cboe sends a fraction (0.20), we want percent
            iv *= 100
        last = num(o.get("last_trade_price"))
        out[(exp, int(m["k"]) / 1000.0, m["cp"])] = {
            "last": last if last else None, "bid": num(o.get("bid")), "ask": num(o.get("ask")), "iv": iv,
            "delta": num(o.get("delta")), "vol": int(num(o.get("volume")) or 0), "oi": int(num(o.get("open_interest")) or 0),
        }
    ts = j.get("timestamp") or d.get("timestamp") or d.get("last_trade_time")
    return {"spot": spot, "prevClose": prev_close, "contracts": out, "srcTime": ts, "source": "cboe"}


def fetch_cboe():
    return parse_cboe(json.loads(get(CBOE_URL.format(sym=SYMBOL))))


# ----------------------------------------------------------------------------- source 2: Yahoo (fallback)
def fetch_yahoo():
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    try:
        get("https://fc.yahoo.com", opener=op)
    except Exception:
        pass                                   # the cookie is set even when this returns 404
    crumb = get("https://query1.finance.yahoo.com/v1/test/getcrumb", opener=op).strip()
    base = f"https://query2.finance.yahoo.com/v7/finance/options/{SYMBOL}"
    first = json.loads(get(f"{base}?crumb={crumb}", opener=op))["optionChain"]["result"][0]
    spot = num(first.get("quote", {}).get("regularMarketPrice"))
    prev_close = num(first.get("quote", {}).get("regularMarketPreviousClose"))
    out = {}
    for ts in first.get("expirationDates", []):
        res = json.loads(get(f"{base}?date={ts}&crumb={crumb}", opener=op))["optionChain"]["result"][0]
        opt = (res.get("options") or [{}])[0]
        exp = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        for side, cp in (("calls", "C"), ("puts", "P")):
            for c in opt.get(side, []):
                k = num(c.get("strike"))
                if k is None:
                    continue
                iv = num(c.get("impliedVolatility"))
                out[(exp, k, cp)] = {
                    "last": num(c.get("lastPrice")) or None, "bid": num(c.get("bid")), "ask": num(c.get("ask")),
                    "iv": None if iv is None else iv * 100, "delta": None,
                    "vol": int(num(c.get("volume")) or 0), "oi": int(num(c.get("openInterest")) or 0),
                }
    return {"spot": spot, "prevClose": prev_close, "contracts": out, "srcTime": None, "source": "yahoo"}


# ----------------------------------------------------------------------------- merge with previous run
def third_friday(d: date) -> bool:
    return d.weekday() == 4 and 15 <= d.day <= 21


def build(snap, prev, now: datetime):
    today = now.astimezone(ET).date()
    sess = today.isoformat()
    prev_ok = isinstance(prev, dict) and prev.get("expiries")
    prev_cells = {}
    if prev_ok:
        for e in prev["expiries"]:
            for k, c, p in e["s"]:
                if c:
                    prev_cells[(e["d"], k, "C")] = c
                if p:
                    prev_cells[(e["d"], k, "P")] = p
    same_day = bool(prev_ok and prev.get("sessionDate") == sess)
    hist = list(prev.get("hist", [])) if same_day else []
    last_rot = prev.get("lastRot") if same_day else None
    rotate = False
    if in_session(now):
        if not last_rot:
            rotate = True
        else:
            gap = (now - datetime.fromisoformat(last_rot)).total_seconds() / 60
            rotate = gap >= ROTATE_MIN_GAP
    now_iso = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if rotate or not hist:
        hist = (hist + [now_iso])[-HIST:]
        last_rot = now_iso
    # hist[i] is the timestamp of vh[HIST - len(hist) + i]; older slots are unknown (zero-filled)

    by_exp = {}
    for (exp, k, cp), c in snap["contracts"].items():
        key = (exp.isoformat(), k, cp)
        pc = prev_cells.get(key)
        vol = c["vol"]
        if pc and same_day:
            vh = list(pc[7])
            if vol < vh[-1]:                              # volume reset (new session) -> forget the old history
                vh = [0] * (HIST - 1) + [vol]
            elif rotate:
                vh = vh[1:] + [vol]
            else:
                vh = vh[:-1] + [vol]
            oi_prev = pc[6]
        else:
            vh = [0] * (HIST - 1) + [vol]
            oi_prev = pc[5] if pc else None               # first run of a new day: yesterday's last OI is the baseline
        if c["oi"] == 0 and vol == 0 and not any(vh):
            continue                                      # keep the file small: skip dead contracts
        cell = [rnd(c["last"], 3), rnd(c["bid"], 3), rnd(c["ask"], 3), rnd(c["iv"], 2), rnd(c["delta"], 4), c["oi"], oi_prev, vh]
        by_exp.setdefault(exp.isoformat(), {}).setdefault(k, {})[cp] = cell

    expiries = []
    for d_iso in sorted(by_exp):
        d = date.fromisoformat(d_iso)
        if d < today:
            continue
        strikes = [[k, sides.get("C"), sides.get("P")] for k, sides in sorted(by_exp[d_iso].items())]
        expiries.append({"d": d_iso, "dte": (d - today).days, "tag": "M" if third_friday(d) else "W", "s": strikes})
    if not expiries:
        raise RuntimeError("source returned no usable contracts")
    return {
        "schema": 1, "symbol": SYMBOL, "asOf": now_iso, "srcTime": snap.get("srcTime"), "source": snap["source"],
        "spot": snap["spot"], "prevClose": snap.get("prevClose"), "sessionDate": sess, "inSession": in_session(now),
        "hist": hist, "lastRot": last_rot,
        "fields": ["last", "bid", "ask", "iv", "delta", "oi", "oiPrev", "vh"],
        "expiries": expiries,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prev", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--fixture", default="", help="read this Cboe-format JSON instead of the network")
    ap.add_argument("--now", default="", help="override the clock (ISO, UTC) for testing")
    a = ap.parse_args()

    now = datetime.fromisoformat(a.now.replace("Z", "+00:00")) if a.now else datetime.now(timezone.utc)
    prev = {}
    if a.prev:
        try:
            prev = json.load(open(a.prev))
        except Exception:
            prev = {}

    errors, snap = [], None
    if a.fixture:
        snap = parse_cboe(json.load(open(a.fixture)))
    else:
        for name, fn in (("cboe", fetch_cboe), ("yahoo", fetch_yahoo)):
            try:
                snap = fn()
                if snap["contracts"]:
                    break
                errors.append(f"{name}: empty chain")
                snap = None
            except Exception as e:                       # noqa: BLE001 - try the next source, report all at the end
                errors.append(f"{name}: {e!r}")
    if not snap:
        sys.exit("all sources failed: " + " | ".join(errors))
    if errors:
        print("note, earlier source failed:", " | ".join(errors))

    out = build(snap, prev, now)
    with open(a.out, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    n = sum(len(e["s"]) for e in out["expiries"])
    print(f"ok: source={out['source']} spot={out['spot']} expiries={len(out['expiries'])} strike-rows={n} "
          f"snapshots={len(out['hist'])} bytes={len(json.dumps(out, separators=(',', ':')))}")


if __name__ == "__main__":
    main()
