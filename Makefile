.PHONY: install run test lint backup refresh-prices

install:        ## install deps (uv) — or: pip install -e ".[dev]"
	uv sync

run:            ## start the app on http://127.0.0.1:8000
	uv run uvicorn app.main:app --reload

test:
	uv run pytest -q

lint:
	uv run ruff check . && uv run ruff format --check .

backup:         ## copy the DB to backups/
	uv run python scripts/backup_db.py

refresh-prices: backup   ## pull fresh Cardmarket/TCGplayer prices + FX rates
	uv run python scripts/refresh_prices.py
