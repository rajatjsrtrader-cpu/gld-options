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
PAGE_B64 = (
    "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"
)
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
    ap.add_argument("--page-out", default="index.html", help="also (re)write the chart page from the copy embedded in this file")
    ap.add_argument("--now", default="", help="override the clock (ISO, UTC) for testing")
    a = ap.parse_args()

    if a.page_out:
        import base64, zlib
        with open(a.page_out, "wb") as f:
            f.write(zlib.decompress(base64.b64decode("".join(PAGE_B64))))

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
