"""Sales module: listings (cards put up for sale), sales (what actually sold) and the sales report."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import pricing
from app.db import get_db, rows

router = APIRouter(prefix="/api", tags=["sales"])

LISTING_SELECT = """
SELECT l.*, ca.name_fr, ca.name_en, ca.number, ca.set_id, s.name_fr AS set_fr, p.price_nm_cad,
       col.quantity AS owned_quantity,
       (SELECT COALESCE(SUM(sa.quantity), 0) FROM sales sa WHERE sa.listing_id = l.id) AS quantity_sold
FROM listings l
JOIN cards ca ON ca.id = l.card_id
JOIN sets s ON s.id = ca.set_id
LEFT JOIN prices p ON p.card_id = l.card_id
LEFT JOIN collection col ON col.card_id = l.card_id
"""


class ListingIn(BaseModel):
    """condition / language / first_edition default to the collection row; asking price to the estimate."""

    card_id: str
    quantity: int = Field(1, ge=1, le=999)
    condition: str | None = None
    language: str | None = None
    first_edition: bool | None = None
    asking_price_cad: float | None = Field(None, ge=0)
    platform: str | None = None
    url: str | None = None
    notes: str | None = None


class ListingPatch(BaseModel):
    quantity: int | None = Field(None, ge=1, le=999)
    condition: str | None = None
    asking_price_cad: float | None = Field(None, ge=0)
    platform: str | None = None
    url: str | None = None
    notes: str | None = None
    status: Literal["active", "cancelled"] | None = None


class SaleDetails(BaseModel):
    """Money fields are totals for the whole line, in CAD."""

    fees_cad: float = Field(0, ge=0)
    shipping_cad: float = Field(0, ge=0)
    platform: str | None = None
    buyer: str | None = None
    sold_at: date | None = None
    notes: str | None = None
    remove_from_collection: bool = True


class SaleIn(SaleDetails):
    card_id: str
    quantity: int = Field(1, ge=1, le=999)
    condition: str | None = None
    language: str | None = None
    first_edition: bool | None = None
    sale_price_cad: float = Field(ge=0)


class SellListing(SaleDetails):
    quantity: int | None = Field(None, ge=1, le=999)  # default: everything still listed
    sale_price_cad: float | None = Field(None, ge=0)  # default: asking price x quantity


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _check_card(con: sqlite3.Connection, card_id: str) -> None:
    if not con.execute("SELECT 1 FROM cards WHERE id=?", (card_id,)).fetchone():
        raise HTTPException(404, f"unknown card {card_id}")


def _check_condition(con: sqlite3.Connection, code: str | None) -> None:
    if code and not con.execute("SELECT 1 FROM conditions WHERE code=?", (code,)).fetchone():
        raise HTTPException(422, f"unknown condition {code}")


def _collection_row(con: sqlite3.Connection, card_id: str) -> dict:
    r = con.execute("SELECT * FROM collection WHERE card_id=?", (card_id,)).fetchone()
    return (
        dict(r) if r else {"owned": 0, "quantity": 0, "condition": None, "language": "FR", "first_edition": 0}
    )


def _estimate(con: sqlite3.Connection, card_id: str, condition: str | None, language: str) -> float | None:
    """Estimated unit value in CAD, straight from app/pricing.py."""
    p = con.execute("SELECT price_nm_cad FROM prices WHERE card_id=?", (card_id,)).fetchone()
    st = pricing.load_settings(con)
    return pricing.price_for(
        p["price_nm_cad"] if p else None, condition, pricing.load_multipliers(con), language, st.fr_factor
    )


def _listed_elsewhere(con: sqlite3.Connection, card_id: str, except_id: int | None = None) -> int:
    return con.execute(
        "SELECT COALESCE(SUM(quantity),0) FROM listings WHERE card_id=? AND status='active' AND id IS NOT ?",
        (card_id, except_id),
    ).fetchone()[0]


def _check_available(
    con: sqlite3.Connection, card_id: str, quantity: int, except_id: int | None = None
) -> None:
    owned = _collection_row(con, card_id)["quantity"] or 0
    free = owned - _listed_elsewhere(con, card_id, except_id)
    if quantity > free:
        raise HTTPException(409, f"only {max(free, 0)} unlisted copy(ies) of {card_id} in the collection")


def _decorate(con: sqlite3.Connection, items: list[dict]) -> list[dict]:
    st = pricing.load_settings(con)
    mult = pricing.load_multipliers(con)
    for it in items:
        it["suggested_price_cad"] = pricing.price_for(
            it.pop("price_nm_cad"), it["condition"], mult, it["language"] or "FR", st.fr_factor
        )
    return items


def _get_listing(con: sqlite3.Connection, listing_id: int) -> dict:
    r = con.execute(LISTING_SELECT + " WHERE l.id = ?", (listing_id,)).fetchone()
    if not r:
        raise HTTPException(404, "listing not found")
    return _decorate(con, [dict(r)])[0]


def _get_sale(con: sqlite3.Connection, sale_id: int) -> dict:
    r = con.execute("SELECT * FROM v_sales WHERE id=?", (sale_id,)).fetchone()
    if not r:
        raise HTTPException(404, "sale not found")
    return dict(r)


def _record_sale(
    con: sqlite3.Connection,
    *,
    card_id: str,
    quantity: int,
    condition: str | None,
    language: str,
    first_edition: bool,
    sale_price_cad: float,
    d: SaleDetails,
    listing_id: int | None = None,
) -> dict:
    ref = _estimate(con, card_id, condition, language)
    removed = 0
    if d.remove_from_collection:
        owned = _collection_row(con, card_id)["quantity"] or 0
        removed = min(owned, quantity)
        if removed:
            left = owned - removed
            con.execute(
                """UPDATE collection SET quantity=?, owned=?, condition=CASE WHEN ?>0 THEN condition END,
                   updated_at=? WHERE card_id=?""",
                (left, 1 if left else 0, left, _now(), card_id),
            )
    cur = con.execute(
        """INSERT INTO sales(listing_id, card_id, quantity, condition, language, first_edition, sale_price_cad,
                             fees_cad, shipping_cad, ref_value_cad, removed_qty, platform, buyer, sold_at, notes, created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            listing_id,
            card_id,
            quantity,
            condition,
            language,
            int(first_edition),
            round(sale_price_cad, 2),
            round(d.fees_cad, 2),
            round(d.shipping_cad, 2),
            round(ref * quantity, 2) if ref is not None else None,
            removed,
            d.platform,
            d.buyer,
            (d.sold_at or date.today()).isoformat(),
            d.notes,
            _now(),
        ),
    )
    return _get_sale(con, cur.lastrowid)


# ---------- listings ----------
@router.get("/listings")
def list_listings(
    status: Literal["active", "sold", "cancelled"] | None = None, con: sqlite3.Connection = Depends(get_db)
):
    sql, params = LISTING_SELECT, []
    if status:
        sql += " WHERE l.status = ?"
        params.append(status)
    return _decorate(
        con, rows(con.execute(sql + " ORDER BY l.status='active' DESC, l.created_at DESC, l.id DESC", params))
    )


@router.post("/listings", status_code=201)
def create_listing(body: ListingIn, con: sqlite3.Connection = Depends(get_db)):
    _check_card(con, body.card_id)
    col = _collection_row(con, body.card_id)
    condition = body.condition or col["condition"] or "NM"
    _check_condition(con, condition)
    language = (body.language or col["language"] or "FR").upper()
    first_edition = bool(col["first_edition"]) if body.first_edition is None else body.first_edition
    _check_available(con, body.card_id, body.quantity)
    asking = body.asking_price_cad
    if asking is None:
        asking = _estimate(con, body.card_id, condition, language)
    now = _now()
    cur = con.execute(
        """INSERT INTO listings(card_id, quantity, condition, language, first_edition, asking_price_cad, platform,
                                url, notes, status, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,'active',?,?)""",
        (
            body.card_id,
            body.quantity,
            condition,
            language,
            int(first_edition),
            round(asking, 2) if asking is not None else None,
            body.platform,
            body.url,
            body.notes,
            now,
            now,
        ),
    )
    return _get_listing(con, cur.lastrowid)


@router.get("/listings/{listing_id}")
def get_listing(listing_id: int, con: sqlite3.Connection = Depends(get_db)):
    return _get_listing(con, listing_id)


@router.patch("/listings/{listing_id}")
def patch_listing(listing_id: int, body: ListingPatch, con: sqlite3.Connection = Depends(get_db)):
    cur = _get_listing(con, listing_id)
    if cur["status"] == "sold":
        raise HTTPException(409, "listing is already sold")
    ch = body.model_dump(exclude_unset=True)
    if "condition" in ch:
        _check_condition(con, ch["condition"])
    status = ch.get("status") or cur["status"]
    if status == "active" and ("quantity" in ch or cur["status"] != "active"):
        _check_available(con, cur["card_id"], ch.get("quantity") or cur["quantity"], listing_id)
    if ch.get("asking_price_cad") is not None:
        ch["asking_price_cad"] = round(ch["asking_price_cad"], 2)
    for col in ("quantity", "status"):  # NOT NULL columns: ignore explicit nulls
        if ch.get(col) is None:
            ch.pop(col, None)
    if ch:
        sets = ", ".join(f"{k}=?" for k in ch)  # keys come from the pydantic model, never from user input
        con.execute(
            f"UPDATE listings SET {sets}, updated_at=? WHERE id=?", [*ch.values(), _now(), listing_id]
        )
    return _get_listing(con, listing_id)


@router.delete("/listings/{listing_id}")
def cancel_listing(listing_id: int, con: sqlite3.Connection = Depends(get_db)):
    """Cancel a listing (kept for history). Sold listings cannot be cancelled."""
    if _get_listing(con, listing_id)["status"] == "sold":
        raise HTTPException(409, "listing is already sold")
    con.execute("UPDATE listings SET status='cancelled', updated_at=? WHERE id=?", (_now(), listing_id))
    return _get_listing(con, listing_id)


@router.post("/listings/{listing_id}/sell", status_code=201)
def sell_listing(listing_id: int, body: SellListing, con: sqlite3.Connection = Depends(get_db)):
    lst = _get_listing(con, listing_id)
    if lst["status"] != "active":
        raise HTTPException(409, f"listing is {lst['status']}")
    qty = body.quantity or lst["quantity"]
    if qty > lst["quantity"]:
        raise HTTPException(409, f"only {lst['quantity']} copy(ies) left on this listing")
    price = body.sale_price_cad
    if price is None:
        if lst["asking_price_cad"] is None:
            raise HTTPException(422, "sale_price_cad is required (the listing has no asking price)")
        price = lst["asking_price_cad"] * qty
    if body.platform is None:
        body.platform = lst["platform"]
    sale = _record_sale(
        con,
        card_id=lst["card_id"],
        quantity=qty,
        condition=lst["condition"],
        language=lst["language"],
        first_edition=bool(lst["first_edition"]),
        sale_price_cad=price,
        d=body,
        listing_id=listing_id,
    )
    left = lst["quantity"] - qty
    con.execute(
        "UPDATE listings SET quantity=?, status=?, updated_at=? WHERE id=?",
        (left, "active" if left else "sold", _now(), listing_id),
    )
    return {"sale": sale, "listing": _get_listing(con, listing_id)}


# ---------- sales ----------
@router.get("/sales")
def list_sales(con: sqlite3.Connection = Depends(get_db)):
    return rows(con.execute("SELECT * FROM v_sales"))


@router.post("/sales", status_code=201)
def create_sale(body: SaleIn, con: sqlite3.Connection = Depends(get_db)):
    """Record a direct sale (no listing)."""
    _check_card(con, body.card_id)
    col = _collection_row(con, body.card_id)
    condition = body.condition or col["condition"] or "NM"
    _check_condition(con, condition)
    return _record_sale(
        con,
        card_id=body.card_id,
        quantity=body.quantity,
        condition=condition,
        language=(body.language or col["language"] or "FR").upper(),
        first_edition=bool(col["first_edition"]) if body.first_edition is None else body.first_edition,
        sale_price_cad=body.sale_price_cad,
        d=body,
    )


def _bucket(r: sqlite3.Row | dict) -> dict:
    d = dict(r)
    for k in ("gross_cad", "fees_cad", "shipping_cad", "ref_value_cad"):
        d[k] = round(d[k] or 0, 2)
    d["net_cad"] = round(d["gross_cad"] - d["fees_cad"] - d["shipping_cad"], 2)
    return d


_AGG = """COUNT(*) AS sales, COALESCE(SUM(quantity),0) AS cards, SUM(sale_price_cad) AS gross_cad,
          SUM(fees_cad) AS fees_cad, SUM(shipping_cad) AS shipping_cad, SUM(ref_value_cad) AS ref_value_cad"""


@router.get("/sales/report")
def sales_report(con: sqlite3.Connection = Depends(get_db)):
    """Totals, by month (YYYY-MM) and by platform. net = gross - fees - shipping."""
    total = _bucket(con.execute(f"SELECT {_AGG} FROM sales").fetchone())
    by_month = [
        _bucket(r)
        for r in con.execute(
            f"SELECT substr(sold_at,1,7) AS month, {_AGG} FROM sales GROUP BY month ORDER BY month DESC"
        )
    ]
    by_platform = [
        _bucket(r)
        for r in con.execute(
            f"SELECT COALESCE(NULLIF(platform,''),'—') AS platform, {_AGG} FROM sales GROUP BY 1 ORDER BY gross_cad DESC"
        )
    ]
    active = con.execute(
        """SELECT COUNT(*) AS listings, COALESCE(SUM(quantity),0) AS cards,
                  ROUND(COALESCE(SUM(asking_price_cad*quantity),0),2) AS asking_cad
           FROM listings WHERE status='active'"""
    ).fetchone()
    return {"total": total, "by_month": by_month, "by_platform": by_platform, "active_listings": dict(active)}


@router.delete("/sales/{sale_id}")
def undo_sale(sale_id: int, con: sqlite3.Connection = Depends(get_db)):
    """Undo a sale entered by mistake: cards go back to the collection and to the listing."""
    r = con.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
    if not r:
        raise HTTPException(404, "sale not found")
    if r["removed_qty"]:
        con.execute(
            """INSERT INTO collection(card_id, owned, quantity, condition, language, first_edition, updated_at)
               VALUES(?,1,?,?,?,?,?)
               ON CONFLICT(card_id) DO UPDATE SET owned=1, quantity=quantity+excluded.quantity,
                 condition=COALESCE(condition, excluded.condition), updated_at=excluded.updated_at""",
            (r["card_id"], r["removed_qty"], r["condition"], r["language"], r["first_edition"], _now()),
        )
    if r["listing_id"]:
        con.execute(
            "UPDATE listings SET quantity=quantity+?, status='active', updated_at=? WHERE id=?",
            (r["quantity"], _now(), r["listing_id"]),
        )
    con.execute("DELETE FROM sales WHERE id=?", (sale_id,))
    return {"deleted": sale_id}
