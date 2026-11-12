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


def test_create_custom_card(client):
    body = {"set_id": "base1", "number": "103", "name_fr": "Carte Test", "rarity": "Rare Holo", "types": "Fire",
            "hp": "90", "price_nm_cad": 40, "quantity": 2, "condition": "EX"}  # fmt: skip
    r = client.post("/api/cards", json=body)
    assert r.status_code == 201
    c = r.json()
    assert (
        c["id"] == "base1-103" and c["is_custom"] == 1 and c["name_en"] == "Carte Test" and c["is_holo"] == 1
    )
    assert c["ref_source"] == "manual" and c["quantity"] == 2 and c["unit_value_cad"] == 34.0
    assert client.get("/api/cards", params={"set": "base1"}).json()["total"] == 103
    base = next(s for s in client.get("/api/sets").json() if s["id"] == "base1")
    assert base["cards_in_set"] == 103 and base["owned"] == 1
    assert client.get("/api/collection/summary").json()["total"]["value_cad"] == 68.0
    client.put("/api/settings/eur_cad", json={"value": 2.0})  # FX change must not wipe a manual price
    assert client.get("/api/cards/base1-103").json()["price_nm_cad"] == 40
    assert client.get("/api/health").json()["cards"] == 1790


def test_create_custom_card_defaults_and_validation(client):
    r = client.post("/api/cards", json={"set_id": "base2", "number": "65a", "name_fr": "Sans Prix"})
    assert r.status_code == 201
    c = r.json()
    assert (
        c["price_nm_cad"] is None
        and c["owned"] == 0
        and c["number_sort"] == 65
        and c["supertype"] == "Pokémon"
    )
    assert (
        client.post("/api/cards", json={"set_id": "base2", "number": "65A", "name_fr": "x"}).status_code
        == 409
    )
    assert (
        client.post("/api/cards", json={"set_id": "base1", "number": "4", "name_fr": "x"}).status_code == 409
    )
    assert (
        client.post("/api/cards", json={"set_id": "nope", "number": "1", "name_fr": "x"}).status_code == 404
    )
    assert (
        client.post("/api/cards", json={"set_id": "base1", "number": "1/2", "name_fr": "x"}).status_code
        == 422
    )
    assert (
        client.post("/api/cards", json={"set_id": "base1", "number": "200", "name_fr": ""}).status_code == 422
    )
    bad = {"set_id": "base1", "number": "201", "name_fr": "x", "quantity": 1, "condition": "ZZ"}
    assert client.post("/api/cards", json=bad).status_code == 422
    assert client.get("/api/cards/base1-201").status_code == 404  # nothing half-created


def test_edit_and_delete_custom_card(client):
    new = {"set_id": "base1", "number": "103", "name_fr": "Faute", "price_nm_cad": 10, "quantity": 1}
    cid = client.post("/api/cards", json=new).json()["id"]
    fix = {
        "number": "104",
        "name_fr": "Corrigé",
        "name_en": "Fixed",
        "rarity": "Rare",
        "hp": "70",
        "price_nm_cad": 12.5,
    }
    r = client.put(f"/api/cards/{cid}", json=fix)
    assert r.status_code == 200
    c = r.json()
    assert (c["id"], c["number"], c["number_sort"], c["name_fr"], c["name_en"]) == (
        cid,
        "104",
        104,
        "Corrigé",
        "Fixed",
    )
    assert c["price_nm_cad"] == 12.5 and c["quantity"] == 1  # the collection entry is untouched
    assert client.put(f"/api/cards/{cid}", json={**fix, "price_nm_cad": None}).json()["price_nm_cad"] is None
    assert client.put(f"/api/cards/{cid}", json={**fix, "number": "4"}).status_code == 409
    assert client.put(f"/api/cards/{cid}", json={**fix, "name_fr": ""}).status_code == 422
    assert client.put("/api/cards/base1-4", json=fix).status_code == 403  # reference data is read-only
    assert client.put("/api/cards/nope-1", json=fix).status_code == 404
    # the freed number can be reused even though the first card keeps its id
    again = client.post("/api/cards", json={"set_id": "base1", "number": "103", "name_fr": "Autre"}).json()
    assert again["id"] != cid and again["number"] == "103"

    assert client.delete("/api/cards/base1-4").status_code == 403
    assert client.delete("/api/cards/nope-1").status_code == 404
    lst = client.post("/api/listings", json={"card_id": cid, "asking_price_cad": 5}).json()
    sale = client.post(f"/api/listings/{lst['id']}/sell", json={}).json()["sale"]
    assert client.delete(f"/api/cards/{cid}").status_code == 409  # sales history protects the card
    client.delete(f"/api/sales/{sale['id']}")
    assert client.delete(f"/api/cards/{cid}").json() == {"deleted": cid}
    assert client.get(f"/api/cards/{cid}").status_code == 404
    assert client.get("/api/listings").json() == [] and client.get("/api/collection").json() == []
    assert client.get("/api/cards", params={"set": "base1"}).json()["total"] == 103


def test_custom_sets(client):
    r = client.post(
        "/api/sets", json={"name_fr": "Promos Divers", "release_date": "2001-05-01", "printed_total": 12}
    )
    assert r.status_code == 201
    s = r.json()
    assert s["is_custom"] == 1 and s["cards_in_set"] == 0 and s["owned"] == 0 and s["released_in_french"] == 1
    assert s["name_en"] == "Promos Divers" and s["release_date"] == "2001-05-01" and s["printed_total"] == 12
    sets = client.get("/api/sets").json()
    assert len(sets) == 19 and sets[-1]["id"] == s["id"] and sum(x["is_custom"] for x in sets) == 1
    assert client.post("/api/sets", json={"name_fr": "promos divers"}).status_code == 409
    assert client.post("/api/sets", json={"name_fr": "Jungle"}).status_code == 409
    assert client.post("/api/sets", json={"name_fr": ""}).status_code == 422

    card = client.post(
        "/api/cards", json={"set_id": s["id"], "number": "1", "name_fr": "Pikachu", "quantity": 1}
    ).json()
    assert card["set_fr"] == "Promos Divers" and card["printed_total"] == 12
    got = next(x for x in client.get("/api/sets").json() if x["id"] == s["id"])
    assert got["cards_in_set"] == 1 and got["owned"] == 1 and got["pct_complete"] == 100

    up = client.put(
        f"/api/sets/{s['id']}",
        json={"name_fr": "Promos FR", "name_en": "FR promos", "released_in_french": False},
    )
    assert (
        up.status_code == 200 and up.json()["name_fr"] == "Promos FR" and up.json()["released_in_french"] == 0
    )
    assert up.json()["release_date"] == "2001-05-01" and up.json()["printed_total"] is None
    assert client.put(f"/api/sets/{s['id']}", json={"name_fr": "Jungle"}).status_code == 409
    assert client.put("/api/sets/base1", json={"name_fr": "x"}).status_code == 403
    assert client.put("/api/sets/nope", json={"name_fr": "x"}).status_code == 404

    assert client.delete("/api/sets/base1").status_code == 403
    assert client.delete(f"/api/sets/{s['id']}").status_code == 409  # not empty
    client.delete(f"/api/cards/{card['id']}")
    assert client.delete(f"/api/sets/{s['id']}").json() == {"deleted": s["id"]}
    assert len(client.get("/api/sets").json()) == 18
