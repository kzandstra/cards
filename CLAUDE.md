# Pokémon collection app — project rules

Inventory + sale preparation for a French WotC-era Pokémon card collection (1999–2003), priced in CAD.

## Stack & commands
- Python 3.12, FastAPI, stdlib `sqlite3`, vanilla-JS frontend in `app/static/index.html` (no build step).
- Install: `uv sync` (or `pip install -e ".[dev]"`).
- Run: `uv run uvicorn app.main:app --reload` → http://127.0.0.1:8000
- Test: `uv run pytest -q` — must be green before every commit.
- Lint: `uv run ruff check . && uv run ruff format .`
- Backup DB: `make backup` (copies `data/pokemon_collection.db` to `backups/`). Do it before any migration or price refresh.

## Data rules
- `data/pokemon_collection.db` is the single source of truth. Schema lives in `app/schema.sql` and is applied idempotently by `app.db.migrate()` at startup. Add new tables/columns there (use `CREATE TABLE IF NOT EXISTS`; for new columns add an `ALTER TABLE` guarded in `migrate()`).
- Tables `cards`, `sets`, `prices`, `conditions` are reference data: never delete rows; `scripts/refresh_prices.py` is the only writer of `prices`.
- Exception: `POST /api/cards` lets the user add a missing card (`cards.is_custom = 1`, id `{set_id}-{number}`), with an optional manual price (`prices.ref_source = 'manual'`, `ref_price_eur` NULL so FX changes and refreshes leave it alone). Every card must have a `collection` row.
- Tables `collection`, `listings`, `sales` are the user's data: migrations must preserve them.
- All money is CAD, stored as REAL rounded to 2 decimals. Reference price = Cardmarket 30-day average sell price (EUR) × `settings.eur_cad`; fallback Cardmarket trend, then TCGplayer market USD × `settings.usd_cad`.
- Condition codes and multipliers (Cardmarket scale) live in table `conditions`: MT 1.05, NM 1.00, EX 0.85, GD 0.70, LP 0.55, PL 0.40, PO 0.25. Pricing logic is in `app/pricing.py` — never duplicate it in SQL or JS.
- `settings.fr_factor` is a global multiplier for French-language copies (default 1.0).
- `name_fr` is the display name everywhere; `name_en` is secondary.
- 1st Edition is a flag on the collection row; it is NOT priced automatically.

## Code rules
- SQL is always parameterised (`?` placeholders). Never format user input into SQL.
- Routers in `app/routers/`, one file per resource; return plain dicts/lists; use `Depends(get_db)`.
- Every endpoint gets a pytest test in `tests/` using the `client` fixture (temp copy of the DB).
- Keep the frontend one file; new tabs go in `index.html`; call the API, never read data files directly.
- Don't add frameworks (no React, no ORM) without asking.

## Roadmap (see README)
Done: setup, read API, collection API, UI wired to API, exports (CSV/SQLite), price-refresh script,
sales module (`listings` + `sales` tables, `app/routers/sales.py`, "Sales" tab, report by month/platform).
Next: lot sheet export, price history, images cache.

## Sales rules
- `listings.quantity` = copies still for sale; status `active | sold | cancelled`. Active listings can never exceed the owned quantity.
- `sales` money columns (`sale_price_cad`, `fees_cad`, `shipping_cad`) are totals for the line; net = price − fees − shipping.
- Recording a sale removes the copies from `collection` (`sales.removed_qty`); `DELETE /api/sales/{id}` puts them back and reopens the listing.
- `DELETE /api/listings/{id}` cancels (never deletes) a listing.
