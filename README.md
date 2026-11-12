# Pokémon collection — inventory & sale prep (WotC era, FR, prices in CAD)

Local web app + SQLite database to inventory a French WotC-era Pokémon card collection (1999–2003),
value it by condition in Canadian dollars, and prepare sales to collectors.

## Install and run (Windows)

Full step-by-step guide with troubleshooting: **[docs/install-guide.pdf](docs/install-guide.pdf)**
(source: `docs/install-guide.html`). In short, in PowerShell:

**1. Tools (once).** Git and uv; uv installs Python 3.12+ for you. Reopen PowerShell after installing.

```powershell
winget install --id Git.Git -e
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**2. Create a folder and download the app (once).** `$HOME` is your own user folder, so this works as written
for anyone; replace `$HOME\Documents\Dev_projects` if you prefer another location.

```powershell
New-Item -ItemType Directory -Force "$HOME\Documents\Dev_projects"
cd "$HOME\Documents\Dev_projects"
git clone https://github.com/kzandstra/cards.git Pokemon
cd Pokemon
uv sync
```

**3. Start the app (every time).** Point PowerShell at the app folder, start the server, keep the window open.

```powershell
cd "$HOME\Documents\Dev_projects\Pokemon"
uv run uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000 (API docs: http://127.0.0.1:8000/docs). Stop with Ctrl + C. If the browser says
"Unable to connect", the server is not running: repeat step 3.

**4. Update.** Stop the server, then back up, pull and restart:

```powershell
uv run python scripts/backup_db.py
git pull
uv sync
uv run uvicorn app.main:app --reload
```

If `git pull` refuses because of `data/pokemon_collection.db`, see section 5 of the guide — do not delete the file,
it holds your collection.

### Other commands

| To do this | Command (in the app folder) |
|---|---|
| Back up the database to `backups/` | `uv run python scripts/backup_db.py` |
| Run the tests | `uv run pytest -q` |
| Lint | `uv run ruff check .` and `uv run ruff format --check .` |
| Refresh market prices (back up first) | `uv run python scripts/refresh_prices.py` |

macOS / Linux: same `git clone`, `uv sync` and `uv run …` commands in a terminal; `make run`, `make test`,
`make backup` and `make lint` are shortcuts for them. Without uv: `pip install -e ".[dev]"`, then
`python -m uvicorn app.main:app --reload`.

## What's inside

| Path | Purpose |
|---|---|
| `data/pokemon_collection.db` | **The database.** 18 sets, 1,789 cards (French + English names), Cardmarket-based prices in CAD, condition table, your collection. |
| `app/schema.sql` | Schema (tables + views), applied idempotently at startup. |
| `app/pricing.py` | The only place pricing rules live (reference price, condition multipliers, FR adjustment). |
| `app/routers/cards.py` | Read API: `/api/sets`, `/api/cards`, `/api/cards/{id}`, `POST/PUT/DELETE /api/cards` and `/api/sets` (add, correct or delete your own cards and sets), `/api/conditions`, `/api/settings`, `/api/rarities`. |
| `app/routers/collection.py` | Write API: `PUT/DELETE /api/collection/{card_id}`, `POST /api/collection/bulk`, `GET /api/collection/summary`. |
| `app/routers/sales.py` | Sales: `GET/POST /api/listings`, `PATCH/DELETE /api/listings/{id}`, `POST /api/listings/{id}/sell`, `GET/POST /api/sales`, `DELETE /api/sales/{id}` (undo), `GET /api/sales/report`. |
| `app/routers/export.py` | `/api/export/collection.csv`, `/api/export/prices.csv`, `/api/export/sales.csv`, `/api/export/sqlite`. |
| `app/static/index.html` | The UI: set browser, tick cards, condition & quantity, value report, condition guide, exports. |
| `scripts/refresh_prices.py` | Pull fresh Cardmarket/TCGplayer prices and EUR/USD→CAD rates (`make refresh-prices`). |
| `docs/install-guide.pdf` | Step-by-step local installation guide (Windows), with troubleshooting. |
| `scripts/backup_db.py` | Snapshot the DB into `backups/` (`make backup`). |
| `tests/` | pytest suite on a temp copy of the DB. |

## Database tables

- `sets`, `cards`, `prices`, `conditions` — reference data (don't edit by hand; `refresh_prices.py` updates `prices`).
- `collection` — one row per card: `owned, quantity, condition, language, first_edition, notes`.
- `listings`, `sales` — cards put up for sale and what actually sold (price, fees, shipping, platform, buyer). View `v_sales`.
- `settings` — `eur_cad`, `usd_cad`, `fr_factor`, `price_date`, `price_basis`.
- Views: `v_card_prices` (CAD price for all 7 conditions), `v_my_collection` (what you own, valued), `v_set_progress`.

Open it with [DB Browser for SQLite](https://sqlitebrowser.org/) or `sqlite3 data/pokemon_collection.db`.

## Pricing model

Reference = Cardmarket 30-day average sell price (EUR) → CAD at `settings.eur_cad`. Market prices describe Near-Mint cards;
conditions scale them: MT ×1.05 · NM ×1.00 · EX ×0.85 · GD ×0.70 · LP ×0.55 · PL ×0.40 · PO ×0.25.
`fr_factor` applies to French-language copies (default 1.0). 1st Edition is tracked but not priced automatically.

## Roadmap (use with Claude Code — see CLAUDE.md)

1. ✅ **Sales module** (done): `listings` + `sales` tables, `/api/listings`, `/api/sales`, report by month/platform, "create listing from card" in the UI.
2. **Lot sheet export**: printable HTML/PDF grouping cards by set with condition and price, for selling bundles.
3. **Price history chart**: `price_history` is already filled by `refresh_prices.py`; show a sparkline per card.
4. **Images**: `cards.image_small/large` hold URLs (English prints); add `scripts/download_images.py` to cache them locally.
5. **Auth / multi-device**: run behind Tailscale or add a simple password if exposed beyond localhost.

Suggested first Claude Code prompt:
> Read CLAUDE.md, README.md and app/schema.sql. Implement roadmap item 1 (sales module) with a migration in schema.sql + db.migrate(), routers/sales.py, tests, and a "Sales" tab in app/static/index.html. Keep existing tests green.
