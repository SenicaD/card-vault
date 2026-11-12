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
        assert price["snapshot"]["source"] == "ebay-image"  # the card photo drives the search
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
        assert 1 <= len(snap["top_listings"]) <= 4  # (the mock may have fewer than 4 raw copies)
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
                s.add(PriceSnapshot(card_id=card["id"], query="q", source="ebay-active", grade="Raw", sample_size=5,
                                    low=med - 5, median=med, mean=med, high=med + 5,
                                    created_at=now - timedelta(days=days_ago)))
            s.add(PriceSnapshot(card_id=card["id"], query="q", source="ebay-image", grade=None, sample_size=5,
                                low=1, median=1, mean=1, high=1, created_at=now))
            s.commit()

        def trend(r):
            return c.get(f"/api/cards/{card['id']}/trend", params={"range": r}).json()

        assert [p["median"] for p in trend("7d")["asking"][0]["points"]] == [22.0]  # two same-day snapshots -> median of medians
        assert len(trend("3m")["asking"][0]["points"]) == 2
        assert len(trend("1y")["asking"][0]["points"]) == 2  # the 400-day-old point is outside a year
        assert len(trend("all")["asking"][0]["points"]) == 3  # the ebay-image snapshot is not an asking price
        t = trend("all")
        assert t["sold"][0]["points"] == [] and t["sold_source"] is None and t["sales"] == []
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
            cols = [r[1] for r in conn.execute(text("PRAGMA table_info(pricesnapshot)"))]
            assert {"top_listings", "grade", "price_refs"} <= set(cols)
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE card (id INTEGER PRIMARY KEY, name TEXT)"); con.commit(); con.close()
        db._add_missing_columns()
        with db.engine.connect() as conn:
            assert {"grade_counts", "grade_counts_at"} <= {r[1] for r in conn.execute(text("PRAGMA table_info(card)"))}
    finally:
        db.engine = real


# ---------- grades ----------

def test_parse_grade_from_titles():
    from app.services.grading import normalize_grade, parse_grade

    for title, cond, want in [
        ("2000 Playoff Contenders Tom Brady Ticket AUTO BGS 9 MINT", None, "BGS 9"),
        ("PSA10 Charizard", None, "PSA 10"), ("Beckett 9.5 Gem Mint Jordan", None, "BGS 9.5"),
        ("CGC Pristine 10 card", None, "CGC 10"), ("Charizard raw NM", None, "Raw"),
        ("Charizard NM", "Ungraded", "Raw"), ("Charizard PSA 10 candidate!", None, "Raw"),
        ("Charizard ready for PSA 10", None, "Raw"), ("Charizard Base Set", None, None),
        ("Card #144 PSA 100", None, None), ("PSAbulk 10 cards", None, None),
    ]:
        assert parse_grade(title, cond) == want, (title, parse_grade(title, cond))
    assert [normalize_grade(g) for g in (None, "", "raw", "psa 10", "Beckett 9", "CGC10", "mint")] == \
        ["Raw", "Raw", "Raw", "PSA 10", "BGS 9", "CGC 10", "Raw"]


def _query_with(*grades):
    """A mock-eBay query whose results include every given grade (the mock is random per query)."""
    import asyncio

    from app.services.ebay import get_ebay
    from app.services.grading import parse_grade

    async def has(q):
        found = {parse_grade(i.title, i.condition) for i in await get_ebay().search_active(q, limit=200)}
        return all(g in found for g in grades)

    for n in range(500):
        q = f"grade probe {n}"
        if asyncio.run(has(q)):
            return q
    raise AssertionError(f"no mock query had {grades}")


def test_price_defaults_to_cards_own_grade_and_compares_others():
    q = _query_with("Raw", "PSA 10", "PSA 9")
    with TestClient(app) as c:
        card = c.post("/api/cards", json={"category": "pokemon", "name": "Grade Test",
                                          "grade": "PSA 9", "search_query": q}).json()
        cid = card["id"]
        # no grades asked -> the card's own grade only
        r = c.post(f"/api/cards/{cid}/price").json()
        assert [s["grade"] for s in r["snapshots"]] == ["PSA 9"]
        assert r["snapshot"]["grade"] == "PSA 9"
        est = c.get(f"/api/cards/{cid}").json()["est_value"]
        assert est == r["snapshot"]["median"] and est is not None
        assert r["grade_counts"]  # eBay showed listings for some grades

        # comparing other grades must not move the estimate, and every grade gets its own snapshot
        r2 = c.post(f"/api/cards/{cid}/price", json={"grades": ["Raw", "PSA 10"]}).json()
        assert [s["grade"] for s in r2["snapshots"]] == ["Raw", "PSA 10"]
        assert r2["snapshot"]["grade"] == "Raw"  # own grade (PSA 9) wasn't asked for -> first asked
        assert c.get(f"/api/cards/{cid}").json()["est_value"] == est
        for s in r2["snapshots"]:  # each grade is priced only from listings of that grade
            assert all(t for t in (s["top_listings"] or []))
            assert len(s["top_listings"] or []) <= 4

        # a card with no grade is priced as Raw
        raw = c.post("/api/cards", json={"category": "pokemon", "name": "Ungraded Card"}).json()
        assert c.post(f"/api/cards/{raw['id']}/price").json()["snapshot"]["grade"] == "Raw"


def test_grade_groups_never_mix_listings():
    import asyncio

    from app.services.ebay import get_ebay
    from app.services.pricing import quote_grades

    mix_q = _query_with("Raw", "PSA 10")

    async def run():
        items = await get_ebay().search_active(mix_q, limit=200)
        quotes, counts, comps, groups, *_ = await quote_grades(mix_q, ["Raw", "PSA 10", "PSA 9"])
        return items, quotes, counts, comps

    items, quotes, counts, comps = asyncio.run(run())
    by_grade = {q.grade: q for q in quotes}
    assert set(by_grade) == {"Raw", "PSA 10", "PSA 9"}
    assert sum(counts.values()) <= len(items)
    for q in quotes:
        for t in q.top_listings or []:
            from app.services.grading import parse_grade
            assert parse_grade(t["title"], t["condition"]) == q.grade


def test_trend_per_grade_and_legacy_snapshots_ignored():
    from datetime import timedelta

    from sqlmodel import Session

    from app.db import engine
    from app.models import PriceSnapshot, utcnow

    with TestClient(app) as c:
        card = c.post("/api/cards", json={"category": "pokemon", "name": "Multi Grade", "grade": "PSA 10"}).json()
        now = utcnow()
        with Session(engine) as s:
            for grade, med in (("PSA 10", 100.0), ("Raw", 10.0), ("BGS 9.5", 80.0), ("PSA 9", 40.0), ("CGC 9", 30.0), (None, 999.0)):
                s.add(PriceSnapshot(card_id=card["id"], query="q", source="ebay-active", grade=grade, sample_size=5,
                                    low=med, median=med, mean=med, high=med, created_at=now - timedelta(days=1)))
            s.commit()

        t = c.get(f"/api/cards/{card['id']}/trend", params={"range": "1m"}).json()
        assert t["grades"] == ["PSA 10"]  # default: the card's own grade
        assert [p["median"] for p in t["asking"][0]["points"]] == [100.0]  # the legacy NULL-grade 999 is ignored

        t = c.get(f"/api/cards/{card['id']}/trend",
                  params=[("range", "1m"), ("grades", "Raw"), ("grades", "PSA 10"), ("grades", "BGS 9.5"),
                          ("grades", "PSA 9"), ("grades", "CGC 9")]).json()
        assert t["grades"] == ["Raw", "PSA 10", "BGS 9.5", "PSA 9"]  # capped at four lines
        assert [s["points"][0]["median"] for s in t["asking"]] == [10.0, 100.0, 80.0, 40.0]
        assert t["grades_available"] == ["Raw", "BGS 9.5", "CGC 9", "PSA 10", "PSA 9"]


def test_refresh_tracks_own_and_looked_up_grades_with_one_search(monkeypatch):
    import asyncio

    from app.services import pricing, refresh
    from app.services.ebay import get_ebay

    calls = []
    real = get_ebay().search_active

    async def counting(query, limit=50):
        calls.append(query)
        return await real(query, limit)

    monkeypatch.setattr(get_ebay(), "search_active", counting)
    monkeypatch.setattr(pricing, "get_ebay", lambda: get_ebay())

    with TestClient(app) as c:
        card = c.post("/api/cards", json={"category": "pokemon", "name": "Refresh Grades", "grade": "PSA 10",
                                          "search_query": "refresh grades query"}).json()
        c.post(f"/api/cards/{card['id']}/price", json={"grades": ["PSA 10", "Raw", "PSA 9"]})
        calls.clear()
        refreshed, _ = asyncio.run(refresh.refresh_due(max_age=__import__("datetime").timedelta(0), pause=0))
        assert refreshed >= 1
        assert calls.count("refresh grades query") == 1  # three grades, one eBay search
        grades = {s["grade"] for s in c.get(f"/api/cards/{card['id']}/prices").json()}
        assert grades >= {"PSA 10", "Raw", "PSA 9"}


def test_prices_link_to_the_listing_behind_them():
    from app.services.ebay import EbayItem
    from app.services.pricing import summarize

    items = [EbayItem(item_id=str(n), title=f"Card {n}", price=p, url=f"https://ebay.test/{n}")
             for n, p in enumerate([5.0, 9.0, 12.0, 20.0, 31.0])]
    s = summarize(items)
    refs = s["price_refs"]
    assert (s["low"], s["median"], s["high"]) == (5.0, 12.0, 31.0)
    assert refs["low"]["url"] == "https://ebay.test/0" and refs["low"]["price"] == 5.0
    assert refs["high"]["url"] == "https://ebay.test/4" and refs["high"]["price"] == 31.0
    assert refs["median"]["url"] == "https://ebay.test/2"
    # an even count: the median (10.5) sits between two listings, so it points at the closest one
    even = summarize(items[:4])
    assert even["median"] == 10.5 and even["price_refs"]["median"]["price"] in (9.0, 12.0)
    assert summarize([])["price_refs"] is None
    # junk titles never become a reference
    junk = summarize([EbayItem(item_id="j", title="Huge lot of cards", price=1.0, url="u")] + items)
    assert junk["price_refs"]["low"]["item_id"] == "0"


def test_snapshots_keep_listing_links():
    with TestClient(app) as c:
        card = _card(c, "Link Card")
        snap = c.post(f"/api/cards/{card['id']}/price").json()["snapshot"]
        refs = snap["price_refs"]
        assert snap["sample_size"] > 0 and set(refs) == {"low", "median", "high"}
        assert refs["low"]["price"] == snap["low"] and refs["high"]["price"] == snap["high"]
        assert refs["low"]["url"] and refs["high"]["url"]
        assert c.get(f"/api/cards/{card['id']}/prices").json()[0]["price_refs"] == refs


def test_card_remembers_which_grades_have_listings():
    q = _query_with("Raw", "PSA 10")
    with TestClient(app) as c:
        card = c.post("/api/cards", json={"category": "pokemon", "name": "Counts Card", "grade": "PSA 10",
                                          "search_query": q}).json()
        assert card["grade_counts"] is None
        c.post(f"/api/cards/{card['id']}/price")
        got = c.get(f"/api/cards/{card['id']}").json()
        assert got["grade_counts"]["Raw"] > 0 and got["grade_counts"]["PSA 10"] > 0
        assert got["grade_counts_at"].endswith("Z")


def test_refresh_saves_counts_and_does_not_research_empty_cards_every_round(monkeypatch):
    import asyncio
    from datetime import timedelta

    from app.services import pricing, refresh
    from app.services.ebay import get_ebay

    calls = []
    real = get_ebay().search_active

    async def counting(query, limit=50):
        calls.append(query)
        return await real(query, limit)

    monkeypatch.setattr(get_ebay(), "search_active", counting)
    monkeypatch.setattr(pricing, "get_ebay", lambda: get_ebay())

    with TestClient(app) as c:
        # Raw has listings for this query, PSA 10 never does for a query that only has raw copies
        q = next(f"only raw {n}" for n in range(500) if "PSA 10" not in {
            __import__("app.services.grading", fromlist=["x"]).parse_grade(i.title, i.condition)
            for i in asyncio.run(real(f"only raw {n}", 200))})
        card = c.post("/api/cards", json={"category": "pokemon", "name": "Empty Grade", "grade": "PSA 10",
                                          "search_query": q}).json()
        calls.clear()
        asyncio.run(refresh.refresh_due(pause=0))
        assert calls.count(q) == 1
        got = c.get(f"/api/cards/{card['id']}").json()
        assert got["grade_counts"] and "PSA 10" not in got["grade_counts"]  # nothing to price for its own grade
        assert c.get(f"/api/cards/{card['id']}/prices").json() == []  # ...and no empty snapshot was written
        calls.clear()
        asyncio.run(refresh.refresh_due(pause=0))
        assert q not in calls  # the check counts, so it isn't searched again straight away
        asyncio.run(refresh.refresh_due(max_age=timedelta(0), pause=0))
        assert q in calls


# ---------- sales you made, and listings that disappear ----------

def test_recording_a_sale_marks_sold_and_appears_on_the_trend():
    from datetime import timedelta

    from app.models import utcnow

    with TestClient(app) as c:
        card = c.post("/api/cards", json={"category": "sports", "name": "Sale Card", "grade": "PSA 9"}).json()
        cid = card["id"]
        assert c.post(f"/api/cards/{cid}/sales", json={"price": 0}).status_code == 422
        when = (utcnow() - timedelta(days=3)).isoformat()
        r = c.post(f"/api/cards/{cid}/sales", json={"price": 123.456, "sold_at": when, "note": "eBay auction"})
        assert r.status_code == 201, r.text
        sale = r.json()
        assert sale["price"] == 123.46 and sale["grade"] == "PSA 9"
        assert c.get(f"/api/cards/{cid}").json()["status"] == "sold"
        assert [x["id"] for x in c.get(f"/api/cards/{cid}/sales").json()] == [sale["id"]]

        t = c.get(f"/api/cards/{cid}/trend", params={"range": "1m"}).json()
        assert [(p["price"], p["grade"]) for p in t["sales"]] == [(123.46, "PSA 9")]
        # a different grade's chart doesn't show it, and neither does a range that ends before the sale
        assert c.get(f"/api/cards/{cid}/trend", params=[("range", "1m"), ("grades", "Raw")]).json()["sales"] == []
        assert c.get(f"/api/cards/{cid}/trend", params={"range": "7d"}).json()["sales"][0]["price"] == 123.46

        assert c.delete(f"/api/cards/{cid}/sales/{sale['id']}").status_code == 204
        assert c.get(f"/api/cards/{cid}/sales").json() == []
        assert c.delete(f"/api/cards/{cid}/sales/{sale['id']}").status_code == 404


def test_listings_that_disappear_become_an_estimated_sold_line():
    import asyncio

    from sqlmodel import Session, select

    from app.db import engine
    from app.models import ListingSeen
    from app.services import pricing
    from app.services.ebay import EbayItem, get_ebay
    from app.services.tracker import track_listings

    ebay = get_ebay()

    def item(n, price, title="Tracked Card"):
        return EbayItem(item_id=f"v1|track{n}|0", title=title, price=price, condition="Ungraded", url=f"https://ebay.test/{n}")

    with TestClient(app) as c:
        card = c.post("/api/cards", json={"category": "sports", "name": "Tracked", "search_query": "tracked card"}).json()
        cid = card["id"]
        with Session(engine) as s:
            first = asyncio.run(track_listings(s, cid, {"Raw": [item(1, 10.0), item(2, 20.0), item(3, 30.0), item(4, 40.0)]}))
            assert first == dict(seen=4, new=4, gone=0, checked=0, unknown=0)

            # next day: 2 and 3 have left the results. eBay says 2 is gone, 3 is still up (it just ranked lower)
            ebay.gone.add("v1|track2|0")
            ebay.gone.add("v1|track4|0")
            second = asyncio.run(track_listings(s, cid, {"Raw": [item(1, 12.0)]}))
            assert second["gone"] == 2 and second["checked"] == 3 and second["new"] == 0
            rows = {r.item_id: r for r in s.exec(select(ListingSeen).where(ListingSeen.card_id == cid)).all()}
            assert rows["v1|track2|0"].status == "gone" and rows["v1|track2|0"].price == 20.0  # last asking price
            assert rows["v1|track3|0"].status == "active"  # ranked out, not sold: never counted
            assert rows["v1|track1|0"].price == 12.0  # latest asking price while it is up

        gone = c.get(f"/api/cards/{cid}/gone").json()
        assert sorted(g["price"] for g in gone) == [20.0, 40.0]
        assert all(g["verdict"] == "not_found" and g["url"] for g in gone)

        t = c.get(f"/api/cards/{cid}/trend", params={"range": "7d", "grades": "Raw"}).json()
        assert t["sold_source"] == "estimated"
        point = t["sold"][0]["points"][0]
        assert (point["median"], point["low"], point["high"], point["samples"]) == (30.0, 20.0, 40.0, 2)

        # it comes back (relisted under the same id): no longer counted as gone
        ebay.gone.discard("v1|track2|0")
        with Session(engine) as s:
            asyncio.run(track_listings(s, cid, {"Raw": [item(1, 12.0), item(2, 19.0)]}))
        assert [g["price"] for g in c.get(f"/api/cards/{cid}/gone").json()] == [40.0]
    ebay.gone.clear()


def test_tracking_never_guesses_from_an_ebay_error_and_unknown_grades_are_skipped(monkeypatch):
    import asyncio

    from sqlmodel import Session, select

    from app.db import engine
    from app.models import ListingSeen
    from app.services.ebay import EbayItem, get_ebay
    from app.services.tracker import track_listings

    async def boom(item_id):
        raise RuntimeError("rate limited")

    with TestClient(app) as c:
        cid = c.post("/api/cards", json={"category": "sports", "name": "Errors"}).json()["id"]
        mk = lambda n: EbayItem(item_id=f"v1|err{n}|0", title="Card", price=5.0, url="u")  # noqa: E731
        with Session(engine) as s:
            asyncio.run(track_listings(s, cid, {"Raw": [mk(1), mk(2)], "Unknown": [mk(9)]}))
            assert {r.item_id for r in s.exec(select(ListingSeen).where(ListingSeen.card_id == cid)).all()} == {"v1|err1|0", "v1|err2|0"}
            monkeypatch.setattr(get_ebay(), "get_item_status", boom)
            stats = asyncio.run(track_listings(s, cid, {"Raw": []}))
            assert stats["unknown"] == 2 and stats["gone"] == 0  # an error is not a sale
        assert c.get(f"/api/cards/{cid}/gone").json() == []


def test_lookup_and_daily_job_feed_the_tracker():
    import asyncio

    from sqlmodel import Session, select

    from app.db import engine
    from app.models import ListingSeen

    with TestClient(app) as c:
        cid = c.post("/api/cards", json={"category": "pokemon", "name": "Feeds", "search_query": "feeds tracker query"}).json()["id"]
        assert c.post(f"/api/cards/{cid}/price").status_code == 200
        with Session(engine) as s:
            n = len(s.exec(select(ListingSeen).where(ListingSeen.card_id == cid)).all())
        assert n > 0


def test_live_item_status_reading():
    import pytest

    from app.services.ebay.live import EbayApiError, item_status

    assert item_status(404, {}) == "not_found"
    assert item_status(200, {"estimatedAvailabilities": [{"estimatedAvailabilityStatus": "OUT_OF_STOCK"}]}) == "out_of_stock"
    assert item_status(200, {"estimatedAvailabilities": [{"estimatedAvailabilityStatus": "IN_STOCK"}]}) == "active"
    assert item_status(200, {}) == "active"
    for code in (400, 429, 500, 503):
        with pytest.raises(EbayApiError):
            item_status(code, {})


def test_changing_the_grade_moves_the_estimate_to_the_new_grade():
    q = _query_with("PSA 10", "PSA 9")
    with TestClient(app) as c:
        card = c.post("/api/cards", json={"category": "pokemon", "name": "Regrade", "grade": "PSA 10", "search_query": q}).json()
        cid = card["id"]
        r = c.post(f"/api/cards/{cid}/price", json={"grades": ["PSA 10", "PSA 9"]}).json()
        by = {s["grade"]: s["median"] for s in r["snapshots"]}
        assert c.get(f"/api/cards/{cid}").json()["est_value"] == by["PSA 10"]

        # PSA 9 was already priced: the estimate switches to its latest price straight away
        got = c.patch(f"/api/cards/{cid}", json={"grade": "PSA 9"}).json()
        assert got["est_value"] == by["PSA 9"]

        # a grade that was never priced: the old grade's value must not linger
        got = c.patch(f"/api/cards/{cid}", json={"grade": "BGS 7"}).json()
        assert got["est_value"] is None and got["priced_at"] is None

        # editing something else leaves the estimate alone
        c.patch(f"/api/cards/{cid}", json={"grade": "PSA 9"})
        keep = c.get(f"/api/cards/{cid}").json()["est_value"]
        assert c.patch(f"/api/cards/{cid}", json={"notes": "hello"}).json()["est_value"] == keep
        # "PSA 9" and "psa 9" are the same grade, so retyping it doesn't reset anything
        assert c.patch(f"/api/cards/{cid}", json={"grade": "psa 9"}).json()["est_value"] == keep


def test_price_uses_photo_even_when_the_query_is_sent_and_text_only_opts_out():
    with TestClient(app) as c:
        s = c.post("/api/scan", files={"file": ("card.png", io.BytesIO(PNG), "image/png")}).json()
        ident = s["identification"]
        card = c.post("/api/cards", json={"category": ident["category"], "name": ident["name"], "grade": None,
                                          "search_query": ident["search_query"], "image_path": s["image_path"]}).json()
        grades = ["Raw", "PSA 9"]
        photo = c.post(f"/api/cards/{card['id']}/price", json={"query": ident["search_query"], "grades": grades}).json()
        assert {x["source"] for x in photo["snapshots"]} == {"ebay-image"}
        words = c.post(f"/api/cards/{card['id']}/price", json={"text_only": True, "grades": grades}).json()
        assert {x["source"] for x in words["snapshots"]} == {"ebay-active"}


def test_crop_a_scan_and_a_saved_card():
    with TestClient(app) as c:
        s = c.post("/api/scan", files={"file": ("card.png", io.BytesIO(PNG), "image/png")}).json()
        # PNG fixture is tiny: too-small crops are refused rather than producing a useless image
        bad = c.post("/api/scan/crop", json={"image_path": s["image_path"], "box": {"x": 0, "y": 0, "w": 1, "h": 1}})
        assert bad.status_code == 422
        assert c.post("/api/scan/crop", json={"image_path": "../etc/passwd", "box": {"x": 0, "y": 0, "w": 1, "h": 1}}).status_code == 422
        assert c.post("/api/scan/crop", json={"image_path": s["image_path"], "box": {"x": 0, "y": 0, "w": 2, "h": 1}}).status_code == 422
        card = c.post("/api/cards", json={"category": "sports", "name": "X", "image_path": s["image_path"]}).json()
        assert c.post(f"/api/cards/{card['id']}/crop", json={"x": 0, "y": 0, "w": 1, "h": 1}).status_code == 422


def test_crop_changes_the_photo(tmp_path):
    from PIL import Image
    buf = io.BytesIO(); Image.new("RGB", (400, 600), "red").save(buf, "PNG")
    with TestClient(app) as c:
        s = c.post("/api/scan", files={"file": ("big.png", io.BytesIO(buf.getvalue()), "image/png")}).json()
        r = c.post("/api/scan/crop", json={"image_path": s["image_path"], "box": {"x": .1, "y": .1, "w": .5, "h": .5}})
        assert r.status_code == 200 and r.json()["image_path"] != s["image_path"]
        assert c.get(r.json()["image_url"]).status_code == 200
        card = c.post("/api/cards", json={"category": "sports", "name": "X", "image_path": s["image_path"]}).json()
        out = c.post(f"/api/cards/{card['id']}/crop", json={"x": 0, "y": 0, "w": .5, "h": .5})
        assert out.status_code == 200 and out.json()["image_url"] != card["image_url"]


def test_image_priced_snapshots_count_as_asking_prices_in_the_trend():
    with TestClient(app) as c:
        s = c.post("/api/scan", files={"file": ("card.png", io.BytesIO(PNG), "image/png")}).json()
        i = s["identification"]
        card = c.post("/api/cards", json={"category": i["category"], "name": i["name"], "search_query": i["search_query"],
                                          "image_path": s["image_path"], "grade": "Raw"}).json()
        assert c.post(f"/api/cards/{card['id']}/price", json={}).status_code == 200
        t = c.get(f"/api/cards/{card['id']}/trend?range=1m&grades=Raw").json()
        assert t["asking"] and t["asking"][0]["points"]


def _items(*specs):
    from app.services.ebay import EbayItem
    return [EbayItem(item_id=f"i{n}", title=t, price=p, condition="Ungraded") for n, (t, p) in enumerate(specs)]


def test_parallel_is_read_from_titles():
    from app.services.parallels import parse_parallel
    assert parse_parallel("2026 Obsidian King Miller Red Orbital RPA /99") == "Red /99"
    assert parse_parallel("Obsidian Orbital Lava Flow auto /7") == "Lava Flow /7"
    assert parse_parallel("Obsidian Orbital Auto Patch /49") == "/49"
    assert parse_parallel("2022-23 Topps Chrome 12/150 Rookie") == "Base"  # a set number is not a print run


def test_summary_drops_outliers_and_reports_spread():
    from app.services.pricing import summarize
    items = _items(*[("Card A", p) for p in (20, 21, 22, 23, 24, 25, 26, 27, 2, 900)])
    s = summarize(items)
    assert s["trimmed"] == 2 and s["low"] == 20 and s["high"] == 27
    assert 0 < s["spread"] < 0.5
    assert summarize(_items(("Card A", 5)))["trimmed"] == 0  # too few listings to call anything an outlier


def test_pricing_can_be_limited_to_one_parallel_and_skip_excluded_listings():
    from app.services.pricing import build_quotes, group_by_grade
    items = _items(("King Miller Green /99 auto", 100), ("King Miller Green /99 auto", 110), ("King Miller Green /99 auto", 120),
                   ("King Miller Red /49 auto", 500), ("King Miller Red /49 auto", 520))
    groups = group_by_grade(items)
    allq, _ = build_quotes(groups, ["Raw"], "q", "ebay-image")
    green, used = build_quotes(groups, ["Raw"], "q", "ebay-image", parallel="Green /99")
    assert green[0].median == 110 and green[0].parallel == "Green /99" and len(used) == 3
    assert allq[0].median > green[0].median
    no_one, _ = build_quotes(groups, ["Raw"], "q", "ebay-image", parallel="Green /99", exclude={"i1"})
    assert no_one[0].median == 110 and no_one[0].sample_size == 2


def test_matches_are_kept_and_excluding_one_reprices_without_a_new_search():
    with TestClient(app) as c:
        s = c.post("/api/scan", files={"file": ("card.png", io.BytesIO(PNG), "image/png")}).json()
        i = s["identification"]
        card = c.post("/api/cards", json={"category": i["category"], "name": i["name"], "search_query": i["search_query"],
                                          "image_path": s["image_path"], "grade": "Raw"}).json()
        c.post(f"/api/cards/{card['id']}/price", json={})
        rows = c.get(f"/api/cards/{card['id']}/matches", params={"grades": ["Raw"]}).json()
        assert rows and {"parallel", "excluded", "grade"} <= set(rows[0])
        top = max(rows, key=lambda r: r["price"])
        before = c.get(f"/api/cards/{card['id']}").json()["est_value"]
        out = c.post(f"/api/cards/{card['id']}/matches/exclude", json={"item_ids": [top["item_id"]]}).json()
        assert out["snapshot"]["sample_size"] <= len(rows) - 1
        again = c.get(f"/api/cards/{card['id']}/matches", params={"grades": ["Raw"]}).json()
        assert [r["excluded"] for r in again if r["item_id"] == top["item_id"]] == [True]
        back = c.post(f"/api/cards/{card['id']}/matches/exclude", json={"item_ids": [top["item_id"]], "excluded": False}).json()
        assert back["snapshot"]["median"] == before


def test_card_parallel_limits_prices_and_trend(monkeypatch):
    with TestClient(app) as c:
        s = c.post("/api/scan", files={"file": ("card.png", io.BytesIO(PNG), "image/png")}).json()
        i = s["identification"]
        card = c.post("/api/cards", json={"category": i["category"], "name": i["name"], "search_query": i["search_query"],
                                          "image_path": s["image_path"], "grade": "Raw"}).json()
        c.post(f"/api/cards/{card['id']}/price", json={})
        r = c.patch(f"/api/cards/{card['id']}", json={"parallel": "Green /99"})
        assert r.json()["parallel"] == "Green /99" and r.json()["est_value"] is None  # no price for that version yet
        out = c.post(f"/api/cards/{card['id']}/price", json={}).json()
        assert out["snapshot"]["parallel"] == "Green /99"
        t = c.get(f"/api/cards/{card['id']}/trend", params={"range": "1m", "grades": ["Raw"]}).json()
        assert len(t["asking"][0]["points"]) <= 1  # only this version's snapshots are on the line


def test_rank_by_look_suggests_the_version_most_lookalikes_share(monkeypatch):
    from app.routers import ebay as router
    with TestClient(app) as c:
        s = c.post("/api/scan", files={"file": ("card.png", io.BytesIO(PNG), "image/png")}).json()
        i = s["identification"]
        card = c.post("/api/cards", json={"category": i["category"], "name": i["name"], "search_query": i["search_query"],
                                          "image_path": s["image_path"], "grade": "Raw"}).json()
        c.post(f"/api/cards/{card['id']}/price", json={})
        rows = c.get(f"/api/cards/{card['id']}/matches").json()
        # no network: pretend the first listings look just like the photo and carry a "Green /99" title
        from sqlmodel import Session
        from app.db import engine
        from app.models import ListingSeen
        with Session(engine) as session:
            for r in session.exec(__import__("sqlmodel").select(ListingSeen).where(ListingSeen.card_id == card["id"])).all()[:5]:
                r.title, r.image_url = f"King Miller Green /99 {r.item_id}", f"https://i.ebayimg.com/{r.item_id}.jpg"
                session.add(r)
            session.commit()

        async def fake(photo, urls):
            return {u: 95 for u in urls[:5]} | {u: 10 for u in urls[5:]}
        monkeypatch.setattr(router, "score_urls", fake)
        out = c.post(f"/api/cards/{card['id']}/matches/similarity", json={}).json()
        assert out["suggested_parallel"] == "Green /99" and out["votes"] >= 3
        assert max(out["scores"].values()) == 95


def test_a_poorly_identified_card_takes_its_name_from_what_the_photo_matched(monkeypatch):
    with TestClient(app) as c:
        s = c.post("/api/scan", files={"file": ("card.png", io.BytesIO(PNG), "image/png")}).json()
        card = c.post("/api/cards", json={"category": "sports", "name": "Wild", "search_query": "wild auto", "id_confidence": 0.08,
                                          "image_path": s["image_path"], "grade": "Raw"}).json()
        out = c.post(f"/api/cards/{card['id']}/price", json={"query": "wild auto"}).json()
        after = c.get(f"/api/cards/{card['id']}").json()
        assert after["search_query"] != "wild auto" and after["name"] != "Wild"
        assert out["snapshot"]["query"] == after["search_query"]


def test_new_columns_are_added_to_an_existing_database():
    from sqlalchemy import inspect
    from app.db import engine
    with engine.connect() as conn:
        assert "parallel" in {c["name"] for c in inspect(conn).get_columns("card")}
        assert {"excluded"} <= {c["name"] for c in inspect(conn).get_columns("listingseen")}


def _mk(c, **kw):
    return c.post("/api/cards", json={"name": "Bulk Test", "category": "sports", **kw}).json()


def test_filters_stack():
    with TestClient(app) as c:
        a = _mk(c, name="Zed Alpha", cost=5.0)
        b = _mk(c, name="Zed Beta", grade="PSA 10", id_confidence=0.1)
        assert {x["id"] for x in c.get("/api/cards", params={"q": "Zed", "unpriced": True}).json()} >= {a["id"], b["id"]}
        low = c.get("/api/cards", params={"q": "Zed", "low_confidence": True}).json()
        assert [x["id"] for x in low] == [b["id"]]
        assert [x["id"] for x in c.get("/api/cards", params={"q": "Zed", "grade": "PSA 10"}).json()] == [b["id"]]
        assert [x["id"] for x in c.get("/api/cards", params={"q": "Zed", "no_cost": True}).json()] == [b["id"]]
        assert [x["id"] for x in c.get("/api/cards", params={"q": "Zed", "no_photo": True, "grade": "Raw"}).json()] == [a["id"]]
        assert c.get("/api/cards", params={"q": "Zed", "stale_days": 7}).json()  # never priced counts as stale
        # review queue: poorly identified or unpriced, until you mark it reviewed
        queue = {x["id"] for x in c.get("/api/cards", params={"needs_review": True}).json()}
        assert b["id"] in queue
        c.patch(f"/api/cards/{b['id']}", json={"reviewed": True})
        assert b["id"] not in {x["id"] for x in c.get("/api/cards", params={"needs_review": True}).json()}


def test_bulk_update_delete_and_cost():
    with TestClient(app) as c:
        loc = c.post("/api/locations", json={"name": "Bulk Box"}).json()
        ids = [_mk(c, name=f"Bulk {i}")["id"] for i in range(3)]
        r = c.post("/api/cards/bulk/update", json={"ids": ids + [999999], "changes": {"location_id": loc["id"], "cost": 2.5}})
        assert r.json() == {"updated": 3, "missing": 1}
        for i in ids:
            got = c.get(f"/api/cards/{i}").json()
            assert got["location_id"] == loc["id"] and got["cost"] == 2.5
        # identity fields are not bulk-editable
        assert c.post("/api/cards/bulk/update", json={"ids": ids, "changes": {"name": "x"}}).status_code == 422
        assert c.post("/api/cards/bulk/update", json={"ids": ids, "changes": {}}).status_code == 422
        # a grade change in bulk clears the old grade's estimate, same as a single edit
        c.post(f"/api/cards/{ids[0]}/price")
        assert c.get(f"/api/cards/{ids[0]}").json()["est_value"]
        c.post("/api/cards/bulk/update", json={"ids": [ids[0]], "changes": {"grade": "PSA 9"}})
        assert c.get(f"/api/cards/{ids[0]}").json()["est_value"] is None
        assert c.post("/api/cards/bulk/delete", json={"ids": ids}).json() == {"deleted": 3}
        assert c.get(f"/api/cards/{ids[0]}").status_code == 404


def test_bulk_reprice_job(monkeypatch):
    import time

    from app.services import refresh
    monkeypatch.setattr(refresh, "PAUSE_BETWEEN_CARDS", 0)
    with TestClient(app) as c:
        ids = [_mk(c, name=f"Reprice {i}")["id"] for i in range(2)]
        r = c.post("/api/cards/bulk/reprice", json={"ids": ids})
        assert r.status_code == 202 and r.json()["total"] == 2
        for _ in range(100):
            st = c.get("/api/cards/bulk/reprice").json()
            if st["state"] == "done":
                break
            time.sleep(0.05)
        assert st["state"] == "done" and st["done"] == 2 and st["failed"] == 0
        assert st["changed"] == 2  # unpriced -> priced
        assert all(c.get(f"/api/cards/{i}").json()["est_value"] for i in ids)


def test_portfolio_totals_cost_and_series():
    with TestClient(app) as c:
        a = _mk(c, name="Folio A", quantity=2, cost=10.0)
        b = _mk(c, name="Folio B")
        c.post(f"/api/cards/{a['id']}/price")
        c.post(f"/api/cards/{b['id']}/price")
        p = c.get("/api/portfolio", params={"range": "all"}).json()
        t = p["totals"]
        assert t["value"] > 0 and t["costed_cards"] >= 1 and t["cost"] >= 20.0
        assert t["gain"] is not None
        assert p["series"] and p["series"][-1]["value"] == t["value"]
        assert p["by_location"] and p["by_category"] and p["top_cards"]
        sold = c.post("/api/cards", json={"name": "Folio Sold", "category": "sports"}).json()
        c.patch(f"/api/cards/{sold['id']}", json={"status": "sold"})
        assert c.get("/api/portfolio").json()["totals"]["distinct"] == t["distinct"]  # sold cards leave the portfolio
