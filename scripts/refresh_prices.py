"""Refresh market prices from the pokemontcg.io API and exchange rates from frankfurter.app.

Usage:
  python scripts/refresh_prices.py              # refresh everything
  python scripts/refresh_prices.py --sets base1 base2
  python scripts/refresh_prices.py --dry-run    # fetch and report, write nothing

Set POKEMONTCG_API_KEY in your environment for higher rate limits (free key at pokemontcg.io).
Always run `make backup` first.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import pricing  # noqa: E402
from app.db import connect  # noqa: E402

API = "https://api.pokemontcg.io/v2/cards"
FX = "https://api.frankfurter.app/latest"


def get_json(client: httpx.Client, url: str, params: dict | None = None, tries: int = 5) -> dict:
    for i in range(tries):
        try:
            r = client.get(url, params=params, timeout=60)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(5 * (i + 1)); continue
        except httpx.HTTPError:
            pass
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"failed after {tries} tries: {url} {params}")


def fetch_rates(client: httpx.Client) -> tuple[float, float]:
    j = get_json(client, FX, {"from": "EUR", "to": "CAD,USD"})
    eur_cad = float(j["rates"]["CAD"]); eur_usd = float(j["rates"]["USD"])
    return eur_cad, round(eur_cad / eur_usd, 4)  # USD->CAD


def fetch_set_prices(client: httpx.Client, set_id: str) -> list[dict]:
    out, page = [], 1
    while True:
        j = get_json(client, API, {"q": f"set.id:{set_id}", "pageSize": 250, "page": page,
                                   "select": "id,cardmarket,tcgplayer"})
        for c in j["data"]:
            cm = (c.get("cardmarket") or {}).get("prices") or {}
            tp = (c.get("tcgplayer") or {}).get("prices") or {}
            first = next(iter(tp.values()), {}) if tp else {}
            out.append({"id": c["id"], "avg30": cm.get("avg30"), "trend": cm.get("trendPrice"),
                        "tcg": first.get("market"), "updated": (c.get("cardmarket") or {}).get("updatedAt")})
        if len(j["data"]) < 250:
            return out
        page += 1


def ensure_history_table(con: sqlite3.Connection) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS price_history(
        card_id TEXT, price_date TEXT, ref_price_eur REAL, price_nm_cad REAL, PRIMARY KEY(card_id, price_date))""")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", nargs="*", help="set ids to refresh (default: all)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    con = connect()
    ensure_history_table(con)
    set_ids = args.sets or [r[0] for r in con.execute("SELECT id FROM sets ORDER BY sort_order")]
    headers = {"X-Api-Key": os.environ["POKEMONTCG_API_KEY"]} if os.environ.get("POKEMONTCG_API_KEY") else {}
    today = date.today().isoformat()

    with httpx.Client(headers=headers) as client:
        eur_cad, usd_cad = fetch_rates(client)
        print(f"rates: 1 EUR = {eur_cad} CAD, 1 USD = {usd_cad} CAD")
        updated = missing = 0
        for sid in set_ids:
            prices = fetch_set_prices(client, sid)
            for p in prices:
                ref, src = pricing.reference_price_eur(p["avg30"], p["trend"], p["tcg"], usd_cad, eur_cad)
                nm = round(ref * eur_cad, 2) if ref is not None else None
                if ref is None:
                    missing += 1
                if args.dry_run:
                    continue
                con.execute("""INSERT INTO prices(card_id, cardmarket_avg30_eur, cardmarket_trend_eur, tcgplayer_market_usd,
                                 ref_price_eur, ref_source, price_nm_cad, price_date) VALUES(?,?,?,?,?,?,?,?)
                               ON CONFLICT(card_id) DO UPDATE SET cardmarket_avg30_eur=excluded.cardmarket_avg30_eur,
                                 cardmarket_trend_eur=excluded.cardmarket_trend_eur, tcgplayer_market_usd=excluded.tcgplayer_market_usd,
                                 ref_price_eur=excluded.ref_price_eur, ref_source=excluded.ref_source,
                                 price_nm_cad=excluded.price_nm_cad, price_date=excluded.price_date""",
                            (p["id"], p["avg30"], p["trend"], p["tcg"], ref, src, nm, today))
                con.execute("INSERT OR REPLACE INTO price_history VALUES(?,?,?,?)", (p["id"], today, ref, nm))
                updated += 1
            print(f"{sid}: {len(prices)} cards")
        if not args.dry_run:
            con.execute("UPDATE settings SET value=? WHERE key='eur_cad'", (str(eur_cad),))
            con.execute("UPDATE settings SET value=? WHERE key='usd_cad'", (str(usd_cad),))
            con.execute("UPDATE settings SET value=? WHERE key='price_date'", (today,))
            con.commit()
    print(f"{'(dry run) ' if args.dry_run else ''}updated {updated} prices, {missing} without market price")
    return 0


if __name__ == "__main__":
    sys.exit(main())
