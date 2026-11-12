# Pokémon collection — inventory & sale prep (WotC era, FR, prices in CAD)

Local web app + SQLite database to inventory a French WotC-era Pokémon card collection (1999–2003),
value it by condition in Canadian dollars, and prepare sales to collectors.

## Quick start

```bash
# 1. Python 3.12+ and uv (https://docs.astral.sh/uv/)  — or use pip: pip install -e ".[dev]"
uv sync
# 2. run
make run            # or: uv run uvicorn app.main:app --reload
# open http://127.0.0.1:8000     (API docs: http://127.0.0.1:8000/docs)
# 3. tests
make test
```

Windows without make: `uv run uvicorn app.main:app --reload`, `uv run pytest -q`, `uv run python scripts/backup_db.py`.

## What's inside

| Path | Purpose |
|---|---|
| `data/pokemon_collection.db` | **The database.** 18 sets, 1,789 cards (French + English names), Cardmarket-based prices in CAD, condition table, your collection. |
| `app/schema.sql` | Schema (tables + views), applied idempotently at startup. |
| `app/pricing.py` | The only place pricing rules live (reference price, condition multipliers, FR adjustment). |
| `app/routers/cards.py` | Read API: `/api/sets`, `/api/cards`, `/api/cards/{id}`, `/api/conditions`, `/api/settings`, `/api/rarities`. |
| `app/routers/collection.py` | Write API: `PUT/DELETE /api/collection/{card_id}`, `POST /api/collection/bulk`, `GET /api/collection/summary`. |
| `app/routers/export.py` | `/api/export/collection.csv`, `/api/export/prices.csv`, `/api/export/sqlite`. |
| `app/static/index.html` | The UI: set browser, tick cards, condition & quantity, value report, condition guide, exports. |
| `scripts/refresh_prices.py` | Pull fresh Cardmarket/TCGplayer prices and EUR/USD→CAD rates (`make refresh-prices`). |
| `scripts/backup_db.py` | Snapshot the DB into `backups/` (`make backup`). |
| `tests/` | pytest suite on a temp copy of the DB. |

## Database tables

- `sets`, `cards`, `prices`, `conditions` — reference data (don't edit by hand; `refresh_prices.py` updates `prices`).
- `collection` — one row per card: `owned, quantity, condition, language, first_edition, notes`.
- `settings` — `eur_cad`, `usd_cad`, `fr_factor`, `price_date`, `price_basis`.
- Views: `v_card_prices` (CAD price for all 7 conditions), `v_my_collection` (what you own, valued), `v_set_progress`.

Open it with [DB Browser for SQLite](https://sqlitebrowser.org/) or `sqlite3 data/pokemon_collection.db`.

## Pricing model

Reference = Cardmarket 30-day average sell price (EUR) → CAD at `settings.eur_cad`. Market prices describe Near-Mint cards;
conditions scale them: MT ×1.05 · NM ×1.00 · EX ×0.85 · GD ×0.70 · LP ×0.55 · PL ×0.40 · PO ×0.25.
`fr_factor` applies to French-language copies (default 1.0). 1st Edition is tracked but not priced automatically.

## Roadmap (use with Claude Code — see CLAUDE.md)

1. **Sales module**: `listings` + `sales` tables, `/api/listings`, `/api/sales`, report by month/platform, "create listing from card" in the UI.
2. **Lot sheet export**: printable HTML/PDF grouping cards by set with condition and price, for selling bundles.
3. **Price history chart**: `price_history` is already filled by `refresh_prices.py`; show a sparkline per card.
4. **Images**: `cards.image_small/large` hold URLs (English prints); add `scripts/download_images.py` to cache them locally.
5. **Auth / multi-device**: run behind Tailscale or add a simple password if exposed beyond localhost.

Suggested first Claude Code prompt:
> Read CLAUDE.md, README.md and app/schema.sql. Implement roadmap item 1 (sales module) with a migration in schema.sql + db.migrate(), routers/sales.py, tests, and a "Sales" tab in app/static/index.html. Keep existing tests green.
