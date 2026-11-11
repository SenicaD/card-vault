import io
import os
import tempfile

tmp = tempfile.mkdtemp()
os.environ.update(DATABASE_URL=f"sqlite:///{tmp}/test.db", IMAGE_DIR=f"{tmp}/images",
                  IDENTIFIER="mock", EBAY_MODE="mock")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

# 1x1 PNG
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


def test_full_flow():
    with TestClient(app) as c:
        loc = c.post("/api/locations", json={"name": "Binder A", "kind": "binder"}).json()

        scan = c.post("/api/scan", files={"file": ("card.png", io.BytesIO(PNG), "image/png")})
        assert scan.status_code == 200, scan.text
        s = scan.json()
        assert c.get(s["image_url"]).status_code == 200
        ident = s["identification"]

        body = {k: ident[k] for k in ("category", "name", "set_name", "year", "card_number",
                                      "variant", "grade", "condition", "search_query")}
        card = c.post("/api/cards", json={**body, "image_path": s["image_path"],
                                          "id_confidence": ident["confidence"],
                                          "location_id": loc["id"], "slot": "p1-3"}).json()
        assert card["status"] == "in_stock"
        assert card["created_at"].endswith("Z")

        price = c.post(f"/api/cards/{card['id']}/price").json()
        assert price["snapshot"]["median"] > 0
        assert c.get(f"/api/cards/{card['id']}").json()["est_value"] == price["snapshot"]["median"]

        listing = c.post(f"/api/cards/{card['id']}/listings", json={"publish": True})
        assert listing.status_code == 201, listing.text
        assert listing.json()["status"] == "published"
        assert c.get(f"/api/cards/{card['id']}").json()["status"] == "listed"

        locs = c.get("/api/locations").json()
        assert locs[0]["card_count"] == 1
        stats = c.get("/api/cards/stats").json()
        assert stats["total_cards"] == 1 and stats["by_status"]["listed"] == 1
        assert len(c.get("/api/cards", params={"q": ident["name"][:4]}).json()) == 1
        assert len(c.get("/api/cards", params={"sort": "value"}).json()) == 1


def test_rejects_non_image():
    with TestClient(app) as c:
        r = c.post("/api/scan", files={"file": ("x.txt", io.BytesIO(b"hi"), "text/plain")})
        assert r.status_code == 415


def test_scan_with_price_and_save_quote():
    with TestClient(app) as c:
        r = c.post("/api/scan", params={"price": "true"},
                   files={"file": ("card.png", io.BytesIO(PNG), "image/png")})
        assert r.status_code == 200, r.text
        s = r.json()
        assert s["quote"]["median"] > 0 and s["quote"]["sample_size"] > 0
        assert s["comps"] and s["price_error"] is None

        # Without the flag, no pricing happens
        plain = c.post("/api/scan", files={"file": ("card.png", io.BytesIO(PNG), "image/png")}).json()
        assert plain["quote"] is None and plain["comps"] == []

        ident = s["identification"]
        card = c.post("/api/cards", json={"category": ident["category"], "name": ident["name"],
                                          "image_path": s["image_path"], "price": s["quote"]})
        assert card.status_code == 201, card.text
        card = card.json()
        assert card["est_value"] == s["quote"]["median"] and card["priced_at"]
        hist = c.get(f"/api/cards/{card['id']}/prices").json()
        assert len(hist) == 1 and hist[0]["query"] == s["quote"]["query"]


def test_price_query():
    with TestClient(app) as c:
        r = c.post("/api/price", json={"query": "1999 Base Set Charizard"})
        assert r.status_code == 200, r.text
        assert r.json()["quote"]["query"] == "1999 Base Set Charizard"
        assert c.post("/api/price", json={"query": ""}).status_code == 422


def test_ebay_image_identifier_prices_from_matches(monkeypatch):
    from app.services.ebay import get_ebay
    from app.services.identify.ebay_image import EbayImageIdentifier
    import app.routers.scan as scan

    monkeypatch.setattr(scan, "get_identifier", lambda: EbayImageIdentifier(get_ebay()))
    calls = []
    real = get_ebay().search_active
    monkeypatch.setattr(get_ebay(), "search_active", lambda *a, **k: calls.append(a) or real(*a, **k))

    with TestClient(app) as c:
        r = c.post("/api/scan", params={"price": "true"},
                   files={"file": ("card.png", io.BytesIO(PNG), "image/png")})
        assert r.status_code == 200, r.text
        s = r.json()
        assert s["identifier"] == "ebay-image"
        ident = s["identification"]
        assert ident["name"] and ident["search_query"] and 0 < ident["confidence"] <= 0.95
        assert s["quote"]["source"] == "ebay-image" and s["quote"]["median"] > 0
        assert s["quote"]["query"] == ident["search_query"]
        assert len(calls) == 1  # only the mock's own image search; no separate text search


def test_ebay_image_parser_reads_titles():
    from app.services.ebay import EbayItem
    from app.services.identify.ebay_image import parse_matches

    titles = ["2018 Panini Prizm Luka Doncic Rookie RC #280 Dallas Mavericks",
              "2018-19 Panini Prizm Luka Doncic RC #280 Mavericks Rookie",
              "Luka Doncic 2018 Prizm Base Rookie Card #280 INVEST",
              "2018 Prizm #280 Luka Doncic RC Rookie Mavs",
              "2018 Panini Prizm Luka Doncic #280 Rookie Card",
              "2018 Prizm Trae Young Rookie #78 Hawks",
              "Lot of 5 Luka Doncic cards"]
    r = parse_matches([EbayItem(item_id=str(i), title=t, price=10) for i, t in enumerate(titles)])
    assert (r.name, r.year, r.card_number, r.set_name, r.category) == ("Luka Doncic", "2018", "280", "Panini Prizm", "sports")
    assert r.grade == "Raw" and "Rookie" in r.variant
    assert parse_matches([]).confidence == 0


def _card(c, name="Charizard Base Set"):
    return c.post("/api/cards", json={"category": "pokemon", "name": name, "search_query": name}).json()


def test_price_returns_top_four_listings_and_keeps_them():
    with TestClient(app) as c:
        card = _card(c)
        snap = c.post(f"/api/cards/{card['id']}/price").json()["snapshot"]
        assert len(snap["top_listings"]) == 4
        assert {"title", "price", "url"} <= set(snap["top_listings"][0])
        # persisted: visible again from the history endpoint
        hist = c.get(f"/api/cards/{card['id']}/prices").json()
        assert hist[0]["top_listings"] == snap["top_listings"]


def test_trend_ranges_and_sold_flag():
    from datetime import timedelta

    from sqlmodel import Session

    from app.db import engine
    from app.models import PriceSnapshot, utcnow

    with TestClient(app) as c:
        card = _card(c, "Trend Card")
        now = utcnow()
        with Session(engine) as s:
            for days_ago, med in ((400, 50.0), (60, 30.0), (3, 20.0), (3, 24.0)):
                s.add(PriceSnapshot(card_id=card["id"], query="q", source="ebay-active", sample_size=5,
                                    low=med - 5, median=med, mean=med, high=med + 5,
                                    created_at=now - timedelta(days=days_ago)))
            s.add(PriceSnapshot(card_id=card["id"], query="q", source="ebay-image", sample_size=5,
                                low=1, median=1, mean=1, high=1, created_at=now))
            s.commit()

        def trend(r):
            return c.get(f"/api/cards/{card['id']}/trend", params={"range": r}).json()

        assert [p["median"] for p in trend("7d")["asking"]] == [22.0]  # two same-day snapshots -> median of medians
        assert len(trend("3m")["asking"]) == 2
        assert len(trend("1y")["asking"]) == 2  # the 400-day-old point is outside a year
        assert len(trend("all")["asking"]) == 3  # the ebay-image snapshot is not an asking price
        t = trend("all")
        assert t["sold"] == [] and t["sold_available"] is False
        assert t["first_recorded"] == (now - timedelta(days=400)).date().isoformat()
        assert c.get(f"/api/cards/{card['id']}/trend", params={"range": "5y"}).status_code == 422


def test_refresh_job_reprices_only_due_unsold_cards():
    import asyncio
    from datetime import timedelta

    from app.services.refresh import refresh_due

    with TestClient(app) as c:
        fresh, stale, sold = _card(c, "Fresh One"), _card(c, "Stale One"), _card(c, "Sold One")
        c.post(f"/api/cards/{fresh['id']}/price")  # priced just now -> not due
        c.patch(f"/api/cards/{sold['id']}", json={"status": "sold"})

        refreshed, failed = asyncio.run(refresh_due(pause=0))
        assert refreshed >= 1 and failed == 0  # (other tests' cards share this database)
        assert len(c.get(f"/api/cards/{stale['id']}/prices").json()) == 1
        assert len(c.get(f"/api/cards/{fresh['id']}/prices").json()) == 1
        assert c.get(f"/api/cards/{sold['id']}/prices").json() == []
        assert c.get(f"/api/cards/{stale['id']}").json()["est_value"] is not None

        # nothing is due again right away
        assert asyncio.run(refresh_due(pause=0)) == (0, 0)
        # ...but with a zero max age everything unsold is due
        assert asyncio.run(refresh_due(max_age=timedelta(0), pause=0))[0] >= 2
        assert len(c.get(f"/api/cards/{fresh['id']}/prices").json()) == 2
        assert c.get(f"/api/cards/{sold['id']}/prices").json() == []  # sold cards are never re-priced


def test_old_database_gets_top_listings_column(tmp_path):
    import sqlite3

    from sqlalchemy import create_engine, text

    import app.db as db

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE pricesnapshot (id INTEGER PRIMARY KEY, card_id INT, query TEXT, source TEXT, "
                "sample_size INT, low REAL, median REAL, mean REAL, high REAL, currency TEXT, created_at DATETIME)")
    con.commit(); con.close()

    real = db.engine
    db.engine = create_engine(f"sqlite:///{path}")
    try:
        db._add_missing_columns()
        db._add_missing_columns()  # idempotent
        with db.engine.connect() as conn:
            assert "top_listings" in [r[1] for r in conn.execute(text("PRAGMA table_info(pricesnapshot)"))]
    finally:
        db.engine = real
