def _own(client, card_id="base1-4", quantity=3, condition="EX"):
    assert (
        client.put(
            f"/api/collection/{card_id}", json={"quantity": quantity, "condition": condition}
        ).status_code
        == 200
    )


def test_migration_is_idempotent_and_preserves_user_data(client):
    from app.db import connect, migrate

    _own(client)
    lst = client.post("/api/listings", json={"card_id": "base1-4"}).json()
    migrate()
    migrate()
    con = connect()
    assert con.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 1
    assert con.execute("SELECT quantity FROM collection WHERE card_id='base1-4'").fetchone()[0] == 3
    con.close()
    assert client.get(f"/api/listings/{lst['id']}").json()["name_fr"] == "Dracaufeu"


def test_listing_defaults_from_collection_and_pricing(client):
    _own(client)
    card = client.get("/api/cards/base1-4").json()
    r = client.post("/api/listings", json={"card_id": "base1-4", "quantity": 2, "platform": "Cardmarket"})
    assert r.status_code == 201
    lst = r.json()
    assert lst["condition"] == "EX" and lst["language"] == "FR" and lst["status"] == "active"
    assert lst["asking_price_cad"] == lst["suggested_price_cad"] == card["unit_value_cad"]
    assert [x["id"] for x in client.get("/api/listings", params={"status": "active"}).json()] == [lst["id"]]
    assert client.get("/api/listings", params={"status": "sold"}).json() == []


def test_listing_validation(client):
    assert client.post("/api/listings", json={"card_id": "nope-1"}).status_code == 404
    assert client.post("/api/listings", json={"card_id": "base1-4"}).status_code == 409  # not owned
    _own(client, quantity=2)
    assert client.post("/api/listings", json={"card_id": "base1-4", "condition": "ZZ"}).status_code == 422
    assert client.post("/api/listings", json={"card_id": "base1-4", "quantity": 0}).status_code == 422
    a = client.post("/api/listings", json={"card_id": "base1-4", "quantity": 2}).json()
    assert (
        client.post("/api/listings", json={"card_id": "base1-4"}).status_code == 409
    )  # all copies already listed
    assert client.get("/api/listings/999").status_code == 404
    assert client.patch(f"/api/listings/{a['id']}", json={"quantity": 3}).status_code == 409


def test_patch_and_cancel_listing(client):
    _own(client)
    lst = client.post("/api/listings", json={"card_id": "base1-4"}).json()
    p = client.patch(
        f"/api/listings/{lst['id']}", json={"asking_price_cad": 123.456, "platform": "eBay", "quantity": 2}
    )
    assert p.status_code == 200 and p.json()["asking_price_cad"] == 123.46 and p.json()["quantity"] == 2
    assert client.patch(f"/api/listings/{lst['id']}", json={"condition": "ZZ"}).status_code == 422
    assert client.delete(f"/api/listings/{lst['id']}").json()["status"] == "cancelled"
    assert client.post(f"/api/listings/{lst['id']}/sell", json={}).status_code == 409
    assert (
        client.get("/api/collection/summary").json()["total"]["cards"] == 3
    )  # cancelling never touches the collection
    assert client.patch(f"/api/listings/{lst['id']}", json={"status": "active"}).json()["status"] == "active"


def test_sell_listing_partial_then_full(client):
    _own(client)
    lst = client.post(
        "/api/listings",
        json={"card_id": "base1-4", "quantity": 3, "asking_price_cad": 100, "platform": "eBay"},
    ).json()
    r = client.post(
        f"/api/listings/{lst['id']}/sell",
        json={
            "quantity": 1,
            "sale_price_cad": 90,
            "fees_cad": 9.5,
            "shipping_cad": 5,
            "sold_at": "2026-03-14",
        },
    )
    assert r.status_code == 201
    sale, after = r.json()["sale"], r.json()["listing"]
    assert sale["net_cad"] == 75.5 and sale["platform"] == "eBay" and sale["sold_at"] == "2026-03-14"
    assert sale["ref_value_cad"] == lst["suggested_price_cad"]
    assert after["quantity"] == 2 and after["quantity_sold"] == 1 and after["status"] == "active"
    assert client.get("/api/cards/base1-4").json()["quantity"] == 2
    assert client.post(f"/api/listings/{lst['id']}/sell", json={"quantity": 5}).status_code == 409
    r = client.post(f"/api/listings/{lst['id']}/sell", json={}).json()  # rest, at asking price
    assert (
        r["sale"]["sale_price_cad"] == 200 and r["sale"]["quantity"] == 2 and r["listing"]["status"] == "sold"
    )
    card = client.get("/api/cards/base1-4").json()
    assert card["owned"] == 0 and card["quantity"] == 0
    assert client.delete(f"/api/listings/{lst['id']}").status_code == 409
    assert client.patch(f"/api/listings/{lst['id']}", json={"platform": "x"}).status_code == 409


def test_direct_sale_and_undo(client):
    _own(client, quantity=1, condition="LP")
    assert client.post("/api/sales", json={"card_id": "nope-1", "sale_price_cad": 1}).status_code == 404
    assert client.post("/api/sales", json={"card_id": "base1-4", "sale_price_cad": -1}).status_code == 422
    r = client.post(
        "/api/sales",
        json={"card_id": "base1-4", "sale_price_cad": 80, "platform": "In person", "buyer": "Luc"},
    )
    assert r.status_code == 201
    sale = r.json()
    assert sale["condition"] == "LP" and sale["name_fr"] == "Dracaufeu" and sale["net_cad"] == 80
    assert client.get("/api/cards/base1-4").json()["owned"] == 0
    assert [s["id"] for s in client.get("/api/sales").json()] == [sale["id"]]
    assert client.delete(f"/api/sales/{sale['id']}").json() == {"deleted": sale["id"]}
    card = client.get("/api/cards/base1-4").json()
    assert card["quantity"] == 1 and card["condition"] == "LP" and client.get("/api/sales").json() == []
    assert client.delete(f"/api/sales/{sale['id']}").status_code == 404


def test_undo_listing_sale_reopens_listing(client):
    _own(client, quantity=1)
    lst = client.post("/api/listings", json={"card_id": "base1-4", "asking_price_cad": 50}).json()
    sale = client.post(f"/api/listings/{lst['id']}/sell", json={}).json()["sale"]
    client.delete(f"/api/sales/{sale['id']}")
    back = client.get(f"/api/listings/{lst['id']}").json()
    assert back["status"] == "active" and back["quantity"] == 1 and back["quantity_sold"] == 0
    assert client.get("/api/cards/base1-4").json()["quantity"] == 1


def test_sales_report_and_csv(client):
    empty = client.get("/api/sales/report").json()
    assert empty["total"]["sales"] == 0 and empty["total"]["net_cad"] == 0 and empty["by_month"] == []
    _own(client, "base1-4", 2)
    _own(client, "base2-1", 1)
    client.post(
        "/api/sales",
        json={
            "card_id": "base1-4",
            "sale_price_cad": 100,
            "fees_cad": 10,
            "platform": "eBay",
            "sold_at": "2026-01-10",
        },
    )
    client.post(
        "/api/sales",
        json={
            "card_id": "base1-4",
            "sale_price_cad": 120,
            "shipping_cad": 4,
            "platform": "eBay",
            "sold_at": "2026-02-02",
        },
    )
    client.post("/api/sales", json={"card_id": "base2-1", "sale_price_cad": 30.5, "sold_at": "2026-02-20"})
    client.post("/api/listings", json={"card_id": "base2-1", "asking_price_cad": 5})  # nothing left to list
    rep = client.get("/api/sales/report").json()
    assert rep["total"] == {
        **rep["total"],
        "sales": 3,
        "cards": 3,
        "gross_cad": 250.5,
        "fees_cad": 10,
        "shipping_cad": 4,
        "net_cad": 236.5,
    }
    assert [(m["month"], m["sales"], m["gross_cad"]) for m in rep["by_month"]] == [
        ("2026-02", 2, 150.5),
        ("2026-01", 1, 100),
    ]
    assert [(p["platform"], p["net_cad"]) for p in rep["by_platform"]] == [("eBay", 206), ("—", 30.5)]
    assert rep["active_listings"]["listings"] == 0
    csv = client.get("/api/export/sales.csv")
    assert csv.status_code == 200 and "Dracaufeu" in csv.text and "2026-02-20" in csv.text
