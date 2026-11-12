from app import pricing


def test_health(client):
    r = client.get("/api/health").json()
    assert r["status"] == "ok" and r["cards"] == 1789 and r["sets"] == 18


def test_sets_and_french_flag(client):
    sets = client.get("/api/sets").json()
    assert len(sets) == 18
    assert {s["id"] for s in sets if not s["released_in_french"]} == {"base4", "si1", "base6", "bp"}


def test_cards_filters(client):
    base = client.get("/api/cards", params={"set": "base1"}).json()
    assert base["total"] == 102 and base["items"][0]["name_fr"] == "Alakazam"
    search = client.get("/api/cards", params={"q": "dracaufeu", "fr_only": 1}).json()
    assert search["total"] >= 5 and all("Dracaufeu" in c["name_fr"] for c in search["items"])
    holo = client.get("/api/cards", params={"set": "base1", "rarity": "Rare Holo"}).json()
    assert holo["total"] == 16


def test_card_detail_prices_by_condition(client):
    c = client.get("/api/cards/base1-4").json()
    assert c["name_fr"] == "Dracaufeu" and c["price_nm_cad"] > 100
    p = c["prices_by_condition"]
    assert p["NM"] == c["price_nm_cad"] and p["EX"] < p["NM"] < p["MT"] and p["PO"] < p["PL"]


def test_collection_roundtrip_and_summary(client):
    r = client.put("/api/collection/base1-4", json={"quantity": 2, "condition": "EX", "first_edition": False})
    assert r.status_code == 200 and r.json()["owned"] == 1
    card = client.get("/api/cards/base1-4").json()
    assert card["quantity"] == 2 and card["unit_value_cad"] == round(card["price_nm_cad"] * 0.85, 2)
    s = client.get("/api/collection/summary").json()
    assert s["total"]["cards"] == 2 and s["total"]["value_cad"] == round(card["unit_value_cad"] * 2, 2)
    assert s["sets"][0]["set_id"] == "base1"
    client.delete("/api/collection/base1-4")
    assert client.get("/api/collection/summary").json()["total"]["cards"] == 0


def test_collection_validation(client):
    assert client.put("/api/collection/nope-1", json={"quantity": 1}).status_code == 404
    assert client.put("/api/collection/base1-4", json={"quantity": 1, "condition": "ZZ"}).status_code == 422


def test_bulk_import_webapp_format(client):
    body = {
        "items": {
            "base1-4": {"q": 1, "c": "NM", "fe": 1},
            "base2-1": {"q": 3, "c": "LP"},
            "base3-1": {"q": 0},
        }
    }
    assert client.post("/api/collection/bulk", json=body).json()["imported"] == 2
    col = client.get("/api/collection").json()
    assert {c["card_id"] for c in col} == {"base1-4", "base2-1"}


def test_exports(client):
    client.put("/api/collection/base1-4", json={"quantity": 1, "condition": "NM"})
    csv = client.get("/api/export/collection.csv")
    assert csv.status_code == 200 and "Dracaufeu" in csv.text
    db = client.get("/api/export/sqlite")
    assert db.status_code == 200 and db.content[:16] == b"SQLite format 3\x00"


def test_settings_update_recomputes_prices(client):
    before = client.get("/api/cards/base1-4").json()["price_nm_cad"]
    client.put("/api/settings/eur_cad", json={"value": 2.0})
    after = client.get("/api/cards/base1-4").json()
    assert after["price_nm_cad"] == round(after["ref_price_eur"] * 2.0, 2) and after["price_nm_cad"] != before
    assert client.put("/api/settings/price_date", json={"value": 1}).status_code == 400


def test_pricing_rules():
    assert pricing.reference_price_eur(10, 50, None, 1.4, 1.6) == (10, "cardmarket_avg30")
    assert pricing.reference_price_eur(0.02, 5, None, 1.4, 1.6) == (5, "cardmarket_trend")
    assert pricing.reference_price_eur(None, None, 8, 1.4, 1.6) == (7.0, "tcgplayer_market_usd")
    m = {"NM": 1.0, "EX": 0.85}
    assert pricing.price_for(100, "EX", m) == 85.0
    assert pricing.price_for(100, "EX", m, "FR", 0.8) == 68.0
    assert pricing.price_for(100, "EX", m, "EN", 0.8) == 85.0
    assert pricing.price_for(None, "NM", m) is None
