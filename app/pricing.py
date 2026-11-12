"""All pricing logic lives here. Money is CAD, rounded to 2 decimals."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

CONDITION_ORDER = ["MT", "NM", "EX", "GD", "LP", "PL", "PO"]


@dataclass(frozen=True)
class Settings:
    eur_cad: float
    usd_cad: float
    fr_factor: float
    price_date: str


def load_settings(con: sqlite3.Connection) -> Settings:
    s = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM settings")}
    return Settings(
        eur_cad=float(s.get("eur_cad", 1.0)),
        usd_cad=float(s.get("usd_cad", 1.0)),
        fr_factor=float(s.get("fr_factor", 1.0)),
        price_date=s.get("price_date", ""),
    )


def load_multipliers(con: sqlite3.Connection) -> dict[str, float]:
    return {r["code"]: float(r["multiplier"]) for r in con.execute("SELECT code, multiplier FROM conditions")}


def reference_price_eur(avg30: float | None, trend: float | None, tcg_usd: float | None,
                        usd_cad: float, eur_cad: float) -> tuple[float | None, str]:
    """Pick the reference EUR price. Values <= 0.05 are Cardmarket placeholders and ignored.
    A trend more than 3x the 30-day average is a spike and ignored in favour of the average."""
    a = avg30 if avg30 and avg30 > 0.05 else None
    t = trend if trend and trend > 0.05 else None
    if a is not None:
        return a, "cardmarket_avg30"
    if t is not None:
        return t, "cardmarket_trend"
    if tcg_usd:
        return round(tcg_usd * usd_cad / eur_cad, 2), "tcgplayer_market_usd"
    return None, "none"


def price_for(price_nm_cad: float | None, condition: str | None, multipliers: dict[str, float],
              language: str = "FR", fr_factor: float = 1.0, first_edition: bool = False) -> float | None:
    """Estimated unit price in CAD for a card in a given condition.
    `first_edition` is accepted for API symmetry but deliberately not priced (see CLAUDE.md)."""
    if price_nm_cad is None:
        return None
    m = multipliers.get(condition or "NM", 1.0)
    lang = fr_factor if (language or "FR").upper() == "FR" else 1.0
    return round(price_nm_cad * m * lang, 2)


def price_table(price_nm_cad: float | None, multipliers: dict[str, float], fr_factor: float = 1.0) -> dict[str, float | None]:
    return {code: price_for(price_nm_cad, code, multipliers, "FR", fr_factor) for code in CONDITION_ORDER}
