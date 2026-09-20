#!/usr/bin/env python3
"""SuMeTra Index price fetcher — runs on GitHub Actions cron.

Fetches the lowest CSFloat listing for each index constituent plus Steam
price history, and writes prices.json / history.json for sumetra.org.

Required env: CSFLOAT_API_KEY (repo secret — never committed).
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# NOTE: env var name is CSFLOAT_API_KEY.
API_KEY = os.environ.get("CSFLOAT_API_KEY", "")

ITEMS = [
    "Elite Trapper Solman | Guerrilla Warfare",
    "\u2605 Hand Wraps | Giraffe (Field-Tested)",
    "StatTrak\u2122 AK-47 | Slate (Factory New)",
    "StatTrak\u2122 Galil AR | Eco (Field-Tested)",
    "Markus Delrow | FBI HRT",
    "Souvenir Charm | Austin 2025 Highlight | Strikes Back",
    "Souvenir Charm | Austin 2025 Highlight | Don't Bring A Knife To A Gunfight",
    "M4A1-S | Black Lotus (Factory New)",
]

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
}


def http_get(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def csfloat_lowest(name):
    """Lowest CSFloat listing in USD, or None."""
    q = urllib.parse.quote(name)
    url = (
        "https://csfloat.com/api/v1/listings?limit=1&sort_by=lowest_price"
        f"&sort_dir=asc&market_hash_name={q}"
    )
    try:
        status, body = http_get(url, {"Authorization": API_KEY})
        if status == 429:
            print(f"  csfloat 429 (throttled) for {name}", file=sys.stderr)
            return None
        if status != 200:
            print(f"  csfloat HTTP {status} for {name}", file=sys.stderr)
            return None
        data = json.loads(body).get("data") or []
        if not data or not isinstance(data[0].get("price"), int):
            return None
        return round(data[0]["price"] / 100, 2)
    except Exception as e:  # noqa: BLE001 - keep the cron green
        print(f"  csfloat error for {name}: {e}", file=sys.stderr)
        return None


def steam_history(name):
    """Steam price history downsampled to one point per day: [(iso, usd)]."""
    q = urllib.parse.quote(name)
    url = f"https://steamcommunity.com/market/pricehistory/?appid=730&market_hash_name={q}"
    try:
        status, body = http_get(url)
        if status != 200:
            print(f"  steam history HTTP {status} for {name}", file=sys.stderr)
            return None
        pts = json.loads(body).get("prices") or []
        by_day = {}
        for p in pts:
            try:
                # Steam format: "Sep 18 2026 01: +0"
                toks = p[0].replace(":", " ").split()[:4]
                dt = datetime.strptime(" ".join(toks), "%b %d %Y %H")
                dt = dt.replace(tzinfo=timezone.utc)
                by_day[dt.date().isoformat()] = (dt.isoformat(), float(p[1]))
            except (ValueError, IndexError, TypeError):
                continue
        return sorted(by_day.values())
    except Exception as e:  # noqa: BLE001
        print(f"  steam history error for {name}: {e}", file=sys.stderr)
        return None


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def main():
    if not API_KEY:
        print("CSFLOAT_API_KEY env var is empty — aborting.", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(timezone.utc).isoformat()
    prices = {}
    for name in ITEMS:
        usd = csfloat_lowest(name)
        print(f"{'OK ' if usd else 'MISS'} csfloat {usd} | {name}")
        prices[name] = {
            "usd": usd,
            "source": "csfloat" if usd is not None else None,
            "asOf": now if usd is not None else None,
        }
        time.sleep(1.2)

    with open("prices.json", "w", encoding="utf-8") as f:
        json.dump(
            {"updated": now, "source": "csfloat", "prices": prices},
            f,
            ensure_ascii=False,
        )

    # Merge histories: keep old days, overwrite with fresh ones.
    old = load_json("history.json") or {}
    old_items = old.get("items", {})
    merged = {}
    for name in ITEMS:
        series = {iso: usd for iso, usd in (old_items.get(name) or [])}
        fresh = steam_history(name)
        print(f"{'OK ' if fresh else 'MISS'} steam history | {name}")
        if fresh:
            for iso, usd in fresh:
                series[iso] = usd
        merged[name] = sorted(series.items())
        time.sleep(2)

    with open("history.json", "w", encoding="utf-8") as f:
        json.dump(
            {"updated": now, "items": merged}, f, ensure_ascii=False
        )

    print("wrote prices.json + history.json")


if __name__ == "__main__":
    main()
