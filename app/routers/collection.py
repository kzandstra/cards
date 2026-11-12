"""Write API: the user's collection."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import pricing
from app.db import get_db, rows

router = APIRouter(prefix="/api/collection", tags=["collection"])


class CollectionEntry(BaseModel):
    quantity: int = Field(ge=0, le=999)
    condition: str | None = None
    first_edition: bool = False
    language: str = "FR"
    notes: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate(con: sqlite3.Connection, card_id: str, entry: CollectionEntry) -> None:
    if not con.execute("SELECT 1 FROM cards WHERE id=?", (card_id,)).fetchone():
        raise HTTPException(404, f"unknown card {card_id}")
    if entry.condition and not con.execute("SELECT 1 FROM conditions WHERE code=?", (entry.condition,)).fetchone():
        raise HTTPException(422, f"unknown condition {entry.condition}")


def upsert(con: sqlite3.Connection, card_id: str, e: CollectionEntry) -> dict:
    _validate(con, card_id, e)
    owned = 1 if e.quantity > 0 else 0
    con.execute(
        """INSERT INTO collection(card_id, owned, quantity, condition, language, first_edition, notes, updated_at)
           VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(card_id) DO UPDATE SET owned=excluded.owned, quantity=excluded.quantity,
             condition=excluded.condition, language=excluded.language, first_edition=excluded.first_edition,
             notes=excluded.notes, updated_at=excluded.updated_at""",
        (card_id, owned, e.quantity, e.condition if owned else None, e.language.upper(), int(e.first_edition),
         e.notes, _now()),
    )
    return dict(con.execute("SELECT * FROM collection WHERE card_id=?", (card_id,)).fetchone())


@router.get("")
def list_collection(con: sqlite3.Connection = Depends(get_db)):
    return rows(con.execute("SELECT * FROM v_my_collection"))


@router.get("/summary")
def summary(con: sqlite3.Connection = Depends(get_db)):
    st = pricing.load_settings(con)
    mult = pricing.load_multipliers(con)
    per_set: dict[str, dict] = {}
    tot = {"cards": 0, "distinct": 0, "value_cad": 0.0, "value_if_nm_cad": 0.0}
    for r in con.execute(
        """SELECT col.card_id, col.quantity, col.condition, col.language, ca.set_id, s.name_fr AS set_fr, p.price_nm_cad
           FROM collection col JOIN cards ca ON ca.id=col.card_id JOIN sets s ON s.id=ca.set_id
           LEFT JOIN prices p ON p.card_id=col.card_id WHERE col.owned=1"""
    ):
        u = pricing.price_for(r["price_nm_cad"], r["condition"], mult, r["language"], st.fr_factor) or 0.0
        nm = pricing.price_for(r["price_nm_cad"], "NM", mult, r["language"], st.fr_factor) or 0.0
        ps = per_set.setdefault(r["set_id"], {"set_id": r["set_id"], "set_fr": r["set_fr"], "cards": 0, "distinct": 0,
                                               "value_cad": 0.0, "value_if_nm_cad": 0.0})
        for d in (ps, tot):
            d["cards"] += r["quantity"]; d["distinct"] += 1
            d["value_cad"] += u * r["quantity"]; d["value_if_nm_cad"] += nm * r["quantity"]
    for d in [tot, *per_set.values()]:
        d["value_cad"] = round(d["value_cad"], 2); d["value_if_nm_cad"] = round(d["value_if_nm_cad"], 2)
    return {"total": tot, "sets": list(per_set.values()), "settings": st.__dict__}


@router.put("/{card_id}")
def put_entry(card_id: str, entry: CollectionEntry, con: sqlite3.Connection = Depends(get_db)):
    return upsert(con, card_id, entry)


@router.delete("/{card_id}")
def delete_entry(card_id: str, con: sqlite3.Connection = Depends(get_db)):
    return upsert(con, card_id, CollectionEntry(quantity=0))


class BulkImport(BaseModel):
    """Format of the web-app export: {"items": {"base1-4": {"q": 1, "c": "EX", "fe": 0}}}"""
    items: dict[str, dict]
    replace: bool = False


@router.post("/bulk")
def bulk_import(body: BulkImport, con: sqlite3.Connection = Depends(get_db)):
    if body.replace:
        con.execute("UPDATE collection SET owned=0, quantity=0, condition=NULL, first_edition=0, updated_at=?", (_now(),))
    n = 0
    for card_id, it in body.items.items():
        q = int(it.get("q", it.get("quantity", 0)) or 0)
        if q <= 0:
            continue
        upsert(con, card_id, CollectionEntry(quantity=q, condition=it.get("c", it.get("condition", "NM")),
                                             first_edition=bool(it.get("fe", it.get("first_edition", 0))),
                                             language=it.get("language", "FR"), notes=it.get("n", it.get("notes"))))
        n += 1
    return {"imported": n}
