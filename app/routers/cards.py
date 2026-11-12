"""Read API: sets, cards, conditions, settings."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from app import pricing
from app.db import get_db, rows

router = APIRouter(prefix="/api", tags=["cards"])

CARD_SELECT = """
SELECT ca.id, ca.set_id, s.name_fr AS set_fr, s.name_en AS set_en, s.printed_total, s.released_in_french,
       ca.number, ca.number_sort, ca.name_fr, ca.name_en, ca.supertype, ca.rarity, ca.is_holo, ca.types, ca.hp,
       ca.artist, ca.dex_number, ca.image_small, ca.image_large,
       p.price_nm_cad, p.ref_price_eur, p.ref_source, p.price_date,
       col.owned, col.quantity, col.condition, col.language, col.first_edition, col.notes, col.updated_at
FROM cards ca
JOIN sets s ON s.id = ca.set_id
LEFT JOIN prices p ON p.card_id = ca.id
LEFT JOIN collection col ON col.card_id = ca.id
"""


def _decorate(con: sqlite3.Connection, cards: list[dict]) -> list[dict]:
    st = pricing.load_settings(con)
    mult = pricing.load_multipliers(con)
    for c in cards:
        c["price_nm_cad_adj"] = pricing.price_for(c["price_nm_cad"], "NM", mult, "FR", st.fr_factor)
        c["unit_value_cad"] = (
            pricing.price_for(c["price_nm_cad"], c["condition"], mult, c["language"] or "FR", st.fr_factor)
            if c.get("owned") else None
        )
        c["total_value_cad"] = (
            round(c["unit_value_cad"] * (c["quantity"] or 0), 2) if c["unit_value_cad"] is not None else None
        )
    return cards


@router.get("/health")
def health(con: sqlite3.Connection = Depends(get_db)):
    n = con.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    s = con.execute("SELECT COUNT(*) FROM sets").fetchone()[0]
    return {"status": "ok", "cards": n, "sets": s, "settings": pricing.load_settings(con).__dict__}


@router.get("/sets")
def list_sets(con: sqlite3.Connection = Depends(get_db)):
    return rows(con.execute("SELECT * FROM v_set_progress"))


@router.get("/conditions")
def list_conditions(con: sqlite3.Connection = Depends(get_db)):
    return rows(con.execute("SELECT * FROM conditions ORDER BY sort_order"))


@router.get("/settings")
def get_settings(con: sqlite3.Connection = Depends(get_db)):
    return rows(con.execute("SELECT key, value, note FROM settings"))


@router.put("/settings/{key}")
def put_setting(key: str, body: dict, con: sqlite3.Connection = Depends(get_db)):
    if key not in {"eur_cad", "usd_cad", "fr_factor"}:
        raise HTTPException(400, "Only eur_cad, usd_cad and fr_factor can be edited here")
    try:
        value = float(body["value"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, "body must be {'value': number}") from None
    con.execute("UPDATE settings SET value=? WHERE key=?", (str(value), key))
    if key == "eur_cad":
        # price_nm_cad is derived; recompute it so the views stay consistent
        con.execute("UPDATE prices SET price_nm_cad = ROUND(ref_price_eur * ?, 2) WHERE ref_price_eur IS NOT NULL", (value,))
    return {"key": key, "value": value}


@router.get("/cards")
def list_cards(
    con: sqlite3.Connection = Depends(get_db),
    set: str | None = Query(None, alias="set"),
    q: str | None = None,
    owned: int | None = Query(None, ge=0, le=1),
    rarity: str | None = None,
    fr_only: int = Query(0, ge=0, le=1),
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    where, params = [], []
    if set:
        where.append("ca.set_id = ?"); params.append(set)
    if q:
        like = f"%{q.strip()}%"
        where.append("(ca.name_fr LIKE ? OR ca.name_en LIKE ? OR ca.number = ?)"); params += [like, like, q.strip()]
    if owned == 1:
        where.append("col.owned = 1")
    elif owned == 0:
        where.append("COALESCE(col.owned,0) = 0")
    if rarity:
        where.append("ca.rarity = ?"); params.append(rarity)
    if fr_only:
        where.append("s.released_in_french = 1")
    sql = CARD_SELECT + (" WHERE " + " AND ".join(where) if where else "")
    total = con.execute(f"SELECT COUNT(*) FROM ({sql})", params).fetchone()[0]
    sql += " ORDER BY s.sort_order, ca.number_sort LIMIT ? OFFSET ?"
    items = _decorate(con, rows(con.execute(sql, [*params, limit, offset])))
    return {"total": total, "limit": limit, "offset": offset, "items": items}


@router.get("/cards/{card_id}")
def get_card(card_id: str, con: sqlite3.Connection = Depends(get_db)):
    r = con.execute(CARD_SELECT + " WHERE ca.id = ?", (card_id,)).fetchone()
    if not r:
        raise HTTPException(404, "card not found")
    card = _decorate(con, [dict(r)])[0]
    st = pricing.load_settings(con)
    card["prices_by_condition"] = pricing.price_table(card["price_nm_cad"], pricing.load_multipliers(con), st.fr_factor)
    return card


@router.get("/rarities")
def list_rarities(con: sqlite3.Connection = Depends(get_db)):
    return [r[0] for r in con.execute("SELECT DISTINCT rarity FROM cards WHERE rarity<>'' ORDER BY rarity")]
