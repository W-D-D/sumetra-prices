#!/usr/bin/env python3
"""SuMeTra Index price fetcher — runs on GitHub Actions cron.

Fetches the lowest CSFloat listing for each index constituent plus CSFloat
daily sales history, and writes prices.json / history.json for sumetra.org.

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


def csfloat_history(name):
    """CSFloat daily average sale prices: [(iso, usd)] ascending.

    Uses the public, unauthenticated graph endpoint:
        GET /api/v1/history/{market_hash_name}/graph
    -> [{"count": n, "day": "2026-09-22T00:00:00Z", "avg_price": 7616}, ...]
    (avg_price is in cents.) Returns None on failure.
    """
    q = urllib.parse.quote(name, safe="")
    url = f"https://csfloat.com/api/v1/history/{q}/graph"
    try:
        status, body = http_get(url, timeout=45)
        if status != 200:
            print(f"  csfloat graph HTTP {status} for {name}", file=sys.stderr)
            return None
        pts = []
        for row in json.loads(body):
            try:
                usd = round(float(row["avg_price"]) / 100, 2)
                dt = datetime.fromisoformat(
                    row["day"].replace("Z", "+00:00")
                )
                pts.append(
                    (dt.strftime("%Y-%m-%dT%H:%M:%SZ"), usd)
                )
            except (KeyError, ValueError, TypeError):
                continue
        pts.sort(key=lambda p: p[0])
        return pts
    except Exception as e:  # noqa: BLE001 - keep the cron green
        print(f"  csfloat graph error for {name}: {e}", file=sys.stderr)
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
    # Primary source is CSFloat daily sales averages. If the graph endpoint
    # fails for an item (e.g. newer charms), fall back to recording today's
    # lowest CSFloat listing as that day's point so history still accumulates.
    old = load_json("history.json") or {}
    old_items = old.get("items", {})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
    merged = {}
    for name in ITEMS:
        series = {iso: usd for iso, usd in (old_items.get(name) or [])}
        fresh = csfloat_history(name)
        print(f"{'OK ' if fresh else 'MISS'} csfloat history | {name}")
        if fresh:
            for iso, usd in fresh:
                series[iso] = usd
        elif prices.get(name, {}).get("usd") is not None:
            # Fallback: today's lowest listing becomes today's history point.
            series[today] = prices[name]["usd"]
            print(f"  fallback: listing snapshot for {name}")
        merged[name] = sorted(series.items())
        time.sleep(2)

    with open("history.json", "w", encoding="utf-8") as f:
        json.dump(
            {"updated": now, "items": merged}, f, ensure_ascii=False
        )

    print("wrote prices.json + history.json")


if __name__ == "__main__":
    main()
