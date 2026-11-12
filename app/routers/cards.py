"""Sets, cards, conditions, settings. Reference rows are read-only; rows the user added
(`is_custom = 1`) can be created, edited and deleted here."""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app import pricing
from app.db import get_db, rows

router = APIRouter(prefix="/api", tags=["cards"])

CARD_SELECT = """
SELECT ca.id, ca.set_id, s.name_fr AS set_fr, s.name_en AS set_en, s.printed_total, s.released_in_french,
       ca.number, ca.number_sort, ca.name_fr, ca.name_en, ca.supertype, ca.rarity, ca.is_holo, ca.types, ca.hp,
       ca.artist, ca.dex_number, ca.image_small, ca.image_large, ca.is_custom,
       p.price_nm_cad, p.ref_price_eur, p.ref_source, p.price_date,
       col.owned, col.quantity, col.condition, col.language, col.first_edition, col.notes, col.updated_at
FROM cards ca
JOIN sets s ON s.id = ca.set_id
LEFT JOIN prices p ON p.card_id = ca.id
LEFT JOIN collection col ON col.card_id = ca.id
"""


SETS_SELECT = """SELECT v.*, s.printed_total, COALESCE(s.is_custom, 0) AS is_custom
FROM v_set_progress v JOIN sets s ON s.id = v.id"""


def _decorate(con: sqlite3.Connection, cards: list[dict]) -> list[dict]:
    st = pricing.load_settings(con)
    mult = pricing.load_multipliers(con)
    for c in cards:
        c["price_nm_cad_adj"] = pricing.price_for(c["price_nm_cad"], "NM", mult, "FR", st.fr_factor)
        c["unit_value_cad"] = (
            pricing.price_for(c["price_nm_cad"], c["condition"], mult, c["language"] or "FR", st.fr_factor)
            if c.get("owned")
            else None
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
    return rows(con.execute(SETS_SELECT + " ORDER BY s.sort_order"))


class SetFields(BaseModel):
    name_fr: str = Field(min_length=1, max_length=80)
    name_en: str | None = Field(None, max_length=80)
    release_date: date | None = None
    printed_total: int | None = Field(None, ge=0, le=9999)
    released_in_french: bool = True


def _custom_set(con: sqlite3.Connection, set_id: str) -> dict:
    r = con.execute("SELECT * FROM sets WHERE id=?", (set_id,)).fetchone()
    if not r:
        raise HTTPException(404, "set not found")
    if not r["is_custom"]:
        raise HTTPException(403, "only sets you created can be changed")
    return dict(r)


def _check_set_name(con: sqlite3.Connection, name_fr: str, except_id: str | None = None) -> None:
    if con.execute(
        "SELECT 1 FROM sets WHERE name_fr = ? COLLATE NOCASE AND id IS NOT ?", (name_fr, except_id)
    ).fetchone():
        raise HTTPException(409, f"a set named {name_fr} already exists")


def _get_set(con: sqlite3.Connection, set_id: str) -> dict:
    return dict(con.execute(SETS_SELECT + " WHERE s.id = ?", (set_id,)).fetchone())


@router.post("/sets", status_code=201)
def create_set(body: SetFields, con: sqlite3.Connection = Depends(get_db)):
    name_fr = body.name_fr.strip()
    _check_set_name(con, name_fr)
    slug = re.sub(r"[^a-z0-9]+", "", name_fr.lower())[:20] or "set"
    set_id, n = f"custom{slug}", 2
    while con.execute("SELECT 1 FROM sets WHERE id=?", (set_id,)).fetchone():
        set_id, n = f"custom{slug}{n}", n + 1
    order = con.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM sets").fetchone()[0]
    con.execute(
        """INSERT INTO sets(id, name_en, name_fr, release_date, printed_total, released_in_french, sort_order, is_custom)
           VALUES(?,?,?,?,?,?,?,1)""",
        (set_id, (body.name_en or "").strip() or name_fr, name_fr, (body.release_date or date.today()).isoformat(),
         body.printed_total, int(body.released_in_french), order),
    )  # fmt: skip
    return _get_set(con, set_id)


@router.put("/sets/{set_id}")
def update_set(set_id: str, body: SetFields, con: sqlite3.Connection = Depends(get_db)):
    cur = _custom_set(con, set_id)
    name_fr = body.name_fr.strip()
    _check_set_name(con, name_fr, set_id)
    con.execute(
        "UPDATE sets SET name_fr=?, name_en=?, release_date=?, printed_total=?, released_in_french=? WHERE id=?",
        (name_fr, (body.name_en or "").strip() or name_fr,
         body.release_date.isoformat() if body.release_date else cur["release_date"], body.printed_total,
         int(body.released_in_french), set_id),
    )  # fmt: skip
    return _get_set(con, set_id)


@router.delete("/sets/{set_id}")
def delete_set(set_id: str, con: sqlite3.Connection = Depends(get_db)):
    _custom_set(con, set_id)
    n = con.execute("SELECT COUNT(*) FROM cards WHERE set_id=?", (set_id,)).fetchone()[0]
    if n:
        raise HTTPException(409, f"this set still holds {n} card(s): delete them first")
    con.execute("DELETE FROM sets WHERE id=?", (set_id,))
    return {"deleted": set_id}


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
        con.execute(
            "UPDATE prices SET price_nm_cad = ROUND(ref_price_eur * ?, 2) WHERE ref_price_eur IS NOT NULL",
            (value,),
        )
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
        where.append("ca.set_id = ?")
        params.append(set)
    if q:
        like = f"%{q.strip()}%"
        where.append("(ca.name_fr LIKE ? OR ca.name_en LIKE ? OR ca.number = ?)")
        params += [like, like, q.strip()]
    if owned == 1:
        where.append("col.owned = 1")
    elif owned == 0:
        where.append("COALESCE(col.owned,0) = 0")
    if rarity:
        where.append("ca.rarity = ?")
        params.append(rarity)
    if fr_only:
        where.append("s.released_in_french = 1")
    sql = CARD_SELECT + (" WHERE " + " AND ".join(where) if where else "")
    total = con.execute(f"SELECT COUNT(*) FROM ({sql})", params).fetchone()[0]
    sql += " ORDER BY s.sort_order, ca.number_sort LIMIT ? OFFSET ?"
    items = _decorate(con, rows(con.execute(sql, [*params, limit, offset])))
    return {"total": total, "limit": limit, "offset": offset, "items": items}


class CardFields(BaseModel):
    """Editable fields of a user-added card. `price_nm_cad` is an optional manual Near-Mint price in CAD."""

    number: str = Field(min_length=1, max_length=10, pattern=r"^[A-Za-z0-9]+$")
    name_fr: str = Field(min_length=1, max_length=80)
    name_en: str | None = Field(None, max_length=80)
    supertype: Literal["Pokémon", "Trainer", "Energy"] = "Pokémon"
    rarity: str = Field("", max_length=30)
    is_holo: bool = False
    types: str = Field("", max_length=40)
    hp: str | None = Field(None, max_length=4, pattern=r"^[0-9]*$")
    price_nm_cad: float | None = Field(None, ge=0)


class NewCard(CardFields):
    """A card missing from the reference data, added by hand. `quantity` > 0 also puts it in the collection."""

    set_id: str
    quantity: int = Field(0, ge=0, le=999)
    condition: str | None = None
    first_edition: bool = False


def _number_sort(number: str) -> int:
    digits = re.match(r"\d+", number)
    return int(digits.group()) if digits else 9999


def _check_number(con: sqlite3.Connection, set_id: str, number: str, except_id: str | None = None) -> None:
    dup = con.execute(
        "SELECT name_fr FROM cards WHERE set_id=? AND number=? COLLATE NOCASE AND id IS NOT ?",
        (set_id, number, except_id),
    ).fetchone()
    if dup:
        raise HTTPException(409, f"card #{number} already exists in this set ({dup['name_fr']})")


def _set_manual_price(con: sqlite3.Connection, card_id: str, price_nm_cad: float | None) -> None:
    """Manual price of a user-added card (ref_price_eur stays NULL so FX updates never touch it)."""
    con.execute("DELETE FROM prices WHERE card_id=?", (card_id,))
    if price_nm_cad is not None:
        con.execute(
            "INSERT INTO prices(card_id, ref_source, price_nm_cad, price_date) VALUES(?,?,?,?)",
            (card_id, "manual", round(price_nm_cad, 2), date.today().isoformat()),
        )


def _custom_card(con: sqlite3.Connection, card_id: str) -> dict:
    r = con.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
    if not r:
        raise HTTPException(404, "card not found")
    if not r["is_custom"]:
        raise HTTPException(403, "only cards you added can be changed")
    return dict(r)


@router.put("/cards/{card_id}")
def update_card(card_id: str, body: CardFields, con: sqlite3.Connection = Depends(get_db)):
    """Correct a user-added card. The id and the set never change."""
    cur = _custom_card(con, card_id)
    number = body.number.strip()
    _check_number(con, cur["set_id"], number, card_id)
    name_fr = body.name_fr.strip()
    con.execute(
        """UPDATE cards SET number=?, number_sort=?, name_fr=?, name_en=?, supertype=?, rarity=?, is_holo=?, types=?, hp=?
           WHERE id=?""",
        (number, _number_sort(number), name_fr, (body.name_en or "").strip() or name_fr, body.supertype,
         body.rarity.strip(), int(body.is_holo or "Holo" in body.rarity), body.types.strip(), body.hp or None, card_id),
    )  # fmt: skip
    _set_manual_price(con, card_id, body.price_nm_cad)
    return get_card(card_id, con)


@router.delete("/cards/{card_id}")
def delete_card(card_id: str, con: sqlite3.Connection = Depends(get_db)):
    """Remove a user-added card, its collection entry and its unsold listings. Refused once it has sales."""
    _custom_card(con, card_id)
    if con.execute("SELECT 1 FROM sales WHERE card_id=?", (card_id,)).fetchone():
        raise HTTPException(409, "this card has recorded sales: undo them first to delete it")
    tables = ["listings", "collection", "prices"]
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='price_history'").fetchone():
        tables.append("price_history")
    for t in tables:  # fixed names, never user input
        con.execute(f"DELETE FROM {t} WHERE card_id=?", (card_id,))
    con.execute("DELETE FROM cards WHERE id=?", (card_id,))
    return {"deleted": card_id}


@router.post("/cards", status_code=201)
def create_card(body: NewCard, con: sqlite3.Connection = Depends(get_db)):
    if not con.execute("SELECT 1 FROM sets WHERE id=?", (body.set_id,)).fetchone():
        raise HTTPException(404, f"unknown set {body.set_id}")
    number = body.number.strip()
    _check_number(con, body.set_id, number)
    card_id, n = f"{body.set_id}-{number}", 2
    while con.execute(
        "SELECT 1 FROM cards WHERE id=?", (card_id,)
    ).fetchone():  # id of a since-renumbered card
        card_id, n = f"{body.set_id}-{number}-{n}", n + 1
    condition = body.condition or ("NM" if body.quantity else None)
    if condition and not con.execute("SELECT 1 FROM conditions WHERE code=?", (condition,)).fetchone():
        raise HTTPException(422, f"unknown condition {condition}")
    name_fr = body.name_fr.strip()
    con.execute(
        """INSERT INTO cards(id, set_id, number, number_sort, name_en, name_fr, supertype, rarity, is_holo, types, hp, is_custom)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,1)""",
        (card_id, body.set_id, number, _number_sort(number), (body.name_en or "").strip() or name_fr,
         name_fr, body.supertype, body.rarity.strip(), int(body.is_holo or "Holo" in body.rarity), body.types.strip(),
         body.hp or None),
    )  # fmt: skip
    _set_manual_price(con, card_id, body.price_nm_cad)
    # Every card has a collection row (v_set_progress relies on it).
    owned = 1 if body.quantity else 0
    con.execute(
        """INSERT INTO collection(card_id, owned, quantity, condition, language, first_edition, updated_at)
           VALUES(?,?,?,?,'FR',?,?)""",
        (card_id, owned, body.quantity, condition if owned else None, int(body.first_edition and owned),
         datetime.now(UTC).isoformat(timespec="seconds")),
    )  # fmt: skip
    return get_card(card_id, con)


@router.get("/cards/{card_id}")
def get_card(card_id: str, con: sqlite3.Connection = Depends(get_db)):
    r = con.execute(CARD_SELECT + " WHERE ca.id = ?", (card_id,)).fetchone()
    if not r:
        raise HTTPException(404, "card not found")
    card = _decorate(con, [dict(r)])[0]
    st = pricing.load_settings(con)
    card["prices_by_condition"] = pricing.price_table(
        card["price_nm_cad"], pricing.load_multipliers(con), st.fr_factor
    )
    return card


@router.get("/rarities")
def list_rarities(con: sqlite3.Connection = Depends(get_db)):
    return [r[0] for r in con.execute("SELECT DISTINCT rarity FROM cards WHERE rarity<>'' ORDER BY rarity")]
