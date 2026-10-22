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
    "Gn/rT/0Yy+me+pW/Pf7XI/8bSJNngA=="
)
HIST = 5                     # snapshots of volume history kept per contract
ROTATE_MIN_GAP = 14          # minutes; runs closer together than this refresh the newest snapshot instead of adding one


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
    ap.add_argument("--out", default="")
    ap.add_argument("--check-session", action="store_true", help="exit 0 while the US session is still ahead or running, 1 once it is over")
    ap.add_argument("--fixture", default="", help="read this Cboe-format JSON instead of the network")
    ap.add_argument("--page-out", default="index.html", help="also (re)write the chart page from the copy embedded in this file")
    ap.add_argument("--now", default="", help="override the clock (ISO, UTC) for testing")
    a = ap.parse_args()
    if a.check_session:
        t = datetime.now(timezone.utc).astimezone(ET)
        sys.exit(0 if t.weekday() < 5 and t.hour * 60 + t.minute <= 16 * 60 + 25 else 1)
    if not a.out:
        ap.error("--out is required")

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
