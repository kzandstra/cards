"""Exports: CSV of the collection, a copy of the SQLite file, full price list."""
from __future__ import annotations

import csv
import io
import sqlite3
from datetime import date

from fastapi import APIRouter, Depends
from fastapi.responses import Response, StreamingResponse

from app.db import db_path, get_db, rows

router = APIRouter(prefix="/api/export", tags=["export"])


def _csv(data: list[dict], filename: str) -> Response:
    buf = io.StringIO()
    buf.write("﻿")  # BOM so Excel opens UTF-8 (French accents) correctly
    if data:
        w = csv.DictWriter(buf, fieldnames=list(data[0].keys()))
        w.writeheader(); w.writerows(data)
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/collection.csv")
def collection_csv(con: sqlite3.Connection = Depends(get_db)):
    return _csv(rows(con.execute("SELECT * FROM v_my_collection")), f"ma_collection_{date.today()}.csv")


@router.get("/prices.csv")
def prices_csv(con: sqlite3.Connection = Depends(get_db)):
    return _csv(rows(con.execute("SELECT * FROM v_card_prices")), f"prix_cartes_{date.today()}.csv")


@router.get("/sqlite")
def sqlite_copy():
    """Consistent snapshot of the DB using the online backup API."""
    src = sqlite3.connect(db_path())
    mem = sqlite3.connect(":memory:")
    src.backup(mem); src.close()
    data = mem.serialize()  # raw file bytes (Python 3.11+)
    mem.close()
    return StreamingResponse(io.BytesIO(data), media_type="application/vnd.sqlite3",
                             headers={"Content-Disposition": f'attachment; filename="pokemon_collection_{date.today()}.db"'})
