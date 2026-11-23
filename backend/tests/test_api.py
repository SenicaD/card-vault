import io
import os
import tempfile

import pytest

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
    inner = get_ebay()._inner  # the client behind the call-logging wrapper
    real = inner.search_active
    monkeypatch.setattr(inner, "search_active", lambda *a, **k: calls.append(a) or real(*a, **k))

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


def _parse(titles):
    from app.services.ebay import EbayItem
    from app.services.identify.ebay_image import parse_matches

    return parse_matches([EbayItem(item_id=str(i), title=t, price=10) for i, t in enumerate(titles)])


def test_ebay_image_parser_reads_letter_codes_and_redemption_auto_flags():
    r = _parse(["2026 Bowman Chrome Jacob Misiorowski #CRA-JMI Refractor Auto RC Redemption",
                "2026 Bowman Chrome Jacob Misiorowski CRA-JMI Auto Redemption RC",
                "Jacob Misiorowski 2026 Bowman Chrome # CRA-JMI Refractor Auto Redemption",
                "2026 Bowman Chrome Jacob Misiorowski CRA-JMI Rookie Autograph Redemption",
                "2026 Bowman Chrome Refractor Jacob Misiorowski #CRA-JMI RC",
                "Jacob Misiorowski 2026 Bowman Chrome Refractor RC Brewers"])
    assert (r.card_number, r.year, r.set_name, r.is_auto, r.is_redemption) == ("CRA-JMI", "2026", "Bowman Chrome", True, True)
    assert "Misiorowski" in r.name and "CRA" not in r.name.upper() and "JMI" not in r.name.upper()
    empty = _parse([])
    assert (empty.is_auto, empty.is_redemption) == (False, False)


def test_ebay_image_parser_keeps_numbers_and_skips_uppercase_words():
    r = _parse(["2018 Panini Prizm Luka Doncic Rookie RC #280 Dallas Mavericks",
                "2018-19 Panini Prizm Luka Doncic RC #280 Mavericks Rookie",
                "Luka Doncic 2018 Prizm Base Rookie Card #280 INVEST",
                "2018 Prizm #280 Luka Doncic RC Rookie Mavs",
                "2018 Panini Prizm Luka Doncic #280 Rookie Card",
                "2018 Prizm Trae Young Rookie #78 Hawks",
                "Lot of 5 Luka Doncic cards"])
    assert (r.card_number, r.is_auto, r.is_redemption) == ("280", False, False)

    bad = {"NM-MT", "YU-GI", "GI-OH", "BLUE-EYES", "PRE-ORDER", "PSA-10", "ALL-STAR", "EX-MT", "ONE-OFF"}
    yugioh = _parse(["2002 YU-GI-OH BLUE-EYES WHITE DRAGON LOB-001 NM-MT"] * 3
                    + ["YU-GI-OH BLUE-EYES WHITE DRAGON LOB-001 1ST EDITION EX-MT", "NM-MT LOB-001 BLUE-EYES WHITE DRAGON"])
    prizm = _parse(["PRE-ORDER 2023 PRIZM WEMBANYAMA #136 PSA-10"] * 3 + ["2023 PRIZM WEMBANYAMA # 136 PSA-10 ALL-STAR"])
    none = _parse(["YU-GI-OH BLUE-EYES WHITE DRAGON NM-MT ALL-STAR"] * 3 + ["YU-GI-OH ONE-OFF BLUE-EYES WHITE DRAGON EX-MT"])
    assert (yugioh.card_number, prizm.card_number, none.card_number) == ("LOB-001", "136", None)
    assert "Blue-Eyes" in none.name
    for r in (yugioh, prizm, none):
        assert r.card_number not in bad and not r.is_auto and not r.is_redemption


def test_letter_code_pattern_shapes():
    from app.services.identify.ebay_image import _LETTER_CODE

    def code(t):
        m = _LETTER_CODE.search(t)
        return m.group(1) if m else None

    assert [code(t) for t in ("x #CRA-JMI y", "x # CRA-JMI", "CRA-JMI auto", "BCRA-AB")] == ["CRA-JMI"] * 3 + ["BCRA-AB"]
    for t in ("YU-GI-OH", "ONE-OF-ONE", "NM-MT", "EX-MT", "VG-EX", "GEM-MT", "ONE-OFF", "BLUE-EYES", "ALL-STAR",
              "PRE-ORDER", "Yu-Gi-Oh", "Pre-Order", "PSA-10", "cra-jmi"):
        assert code(t) is None, t


def test_identification_flags_default_false_and_accept_null():
    from app.services.identify.base import CardIdentification

    base = {"category": "sports", "name": "Jacob Misiorowski", "set_name": "Bowman Chrome", "year": "2026",
            "card_number": "CRA-JMI", "variant": None, "grade": "Raw", "condition": None, "confidence": 0.9,
            "search_query": "2026 Bowman Chrome Jacob Misiorowski CRA-JMI"}
    present = CardIdentification.model_validate({**base, "is_redemption": True, "is_auto": True})
    absent = CardIdentification.model_validate(base)
    null = CardIdentification.model_validate({**base, "is_redemption": None, "is_auto": None})
    assert (present.is_redemption, present.is_auto) == (True, True)
    assert (absent.is_redemption, absent.is_auto) == (False, False)
    assert (null.is_redemption, null.is_auto) == (False, False)
    assert present.card_number == "CRA-JMI"


def test_claude_prompt_asks_for_flags_and_letter_codes():
    from app.services.identify.claude import _PROMPT

    assert "is_redemption" in _PROMPT and "is_auto" in _PROMPT and "CRA-JMI" in _PROMPT


def test_mock_has_redemption_auto_sample():
    from app.services.identify.base import CardIdentification, build_search_query
    from app.services.identify.mock import _SAMPLES

    found = [s for s in _SAMPLES if s.get("card_number") == "CRA-JMI"]
    assert len(found) == 1
    s = found[0]
    assert (s["name"], s["set_name"], s["year"], s["category"]) == ("Jacob Misiorowski", "Bowman Chrome", "2026", "sports")
    assert (s["variant"], s["grade"], s["is_redemption"], s["is_auto"]) == ("Refractor, Auto, RC", "Raw", True, True)
    ident = CardIdentification(**s, search_query="")
    assert "#CRA-JMI" in build_search_query(ident)
    for other in _SAMPLES:
        if other is not s:
            i = CardIdentification(**other, search_query="")
            assert (i.is_redemption, i.is_auto) == (False, False)


def test_scan_identification_has_flags():
    with TestClient(app) as c:
        ident = c.post("/api/scan", files={"file": ("card.png", io.BytesIO(PNG), "image/png")}).json()["identification"]
        assert {"is_redemption", "is_auto"} <= set(ident)


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
                                          "grade": "PSA 9", "search_query": q, "id_confidence": 0.2}).json()
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
                                          "search_query": q, "id_confidence": 0.2}).json()
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


def _heic(w, h):
    import pillow_heif
    from PIL import Image
    pillow_heif.register_heif_opener()
    buf = io.BytesIO(); Image.new("RGB", (w, h), "blue").save(buf, "HEIF")
    return buf.getvalue()


def test_heic_upload_is_saved_as_jpeg_and_can_be_cropped():
    with TestClient(app) as c:
        r = c.post("/api/scan", files={"file": ("IMG_0001.HEIC", io.BytesIO(_heic(300, 400)), "application/octet-stream")})
        assert r.status_code == 200, r.text
        s = r.json()
        assert s["image_path"].endswith(".jpg")
        img = c.get(s["image_url"])
        assert img.status_code == 200 and img.content.startswith(b"\xff\xd8")
        crop = c.post("/api/scan/crop", json={"image_path": s["image_path"], "box": {"x": .1, "y": .1, "w": .5, "h": .5}})
        assert crop.status_code == 200, crop.text
        assert crop.json()["image_path"] != s["image_path"]


def test_heic_is_recognised_by_bytes_or_type():
    data = _heic(300, 400)
    with TestClient(app) as c:
        assert c.post("/api/scan", files={"file": ("blob", io.BytesIO(data), "application/octet-stream")}).status_code == 200
        assert c.post("/api/scan", files={"file": ("x.heif", io.BytesIO(data), "image/heif")}).status_code == 200


def test_heic_upload_is_shrunk_to_1600px():
    from PIL import Image
    from app.config import get_settings
    with TestClient(app) as c:
        s = c.post("/api/scan", files={"file": ("big.heic", io.BytesIO(_heic(2000, 1000)), "image/heic")}).json()
        assert Image.open(get_settings().image_dir / s["image_path"]).size == (1600, 800)


def test_unreadable_heic_and_other_types_are_rejected():
    from app.config import get_settings
    with TestClient(app) as c:
        get_settings().image_dir.mkdir(parents=True, exist_ok=True)
        before = set(get_settings().image_dir.iterdir())
        r = c.post("/api/scan", files={"file": ("bad.heic", io.BytesIO(b"not a heic photo"), "application/octet-stream")})
        assert r.status_code == 422 and "HEIC" in r.json()["detail"]
        assert set(get_settings().image_dir.iterdir()) == before
        r = c.post("/api/scan", files={"file": ("x.txt", io.BytesIO(b"hi"), "text/plain")})
        assert r.status_code == 415 and "HEIC" in r.json()["detail"] and "GIF" in r.json()["detail"]
        r = c.post("/api/scan", files={"file": ("x.jpg", io.BytesIO(b"\xff\xd8\xff\xe0 not heic"), "application/octet-stream")})
        assert r.status_code == 415


def test_heic_conversion_runs_off_the_event_loop(monkeypatch):
    import asyncio

    import app.routers.scan as scan

    real, seen = scan.heic_to_jpeg, []

    def wrapped(data):
        try:
            asyncio.get_running_loop()
            seen.append("on loop")
        except RuntimeError:
            seen.append("off loop")
        return real(data)

    monkeypatch.setattr(scan, "heic_to_jpeg", wrapped)
    with TestClient(app) as c:
        r = c.post("/api/scan", files={"file": ("IMG_0002.HEIC", io.BytesIO(_heic(300, 400)), "image/heic")})
        assert r.status_code == 200, r.text
        assert r.json()["image_path"].endswith(".jpg")
    assert seen == ["off loop"]


def _failing_identifier(monkeypatch, err):
    import app.routers.scan as scan

    class Failing:
        name = "failing"

        async def identify(self, data, content_type):
            raise err

    monkeypatch.setattr(scan, "get_identifier", lambda: Failing())


def _scan_png(c):
    return c.post("/api/scan", params={"price": "true"}, files={"file": ("card.png", io.BytesIO(PNG), "image/png")})


def test_identifier_rate_limit_is_a_429_with_retry_after(monkeypatch):
    import anthropic
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com")
    _failing_identifier(monkeypatch, anthropic.RateLimitError(
        "rate limited", response=httpx.Response(429, headers={"retry-after": "7"}, request=request), body=None))
    with TestClient(app) as c:
        r = _scan_png(c)
        assert r.status_code == 429, r.text
        assert r.headers["retry-after"] == "7"
        assert "rate limited" in r.json()["detail"]


def test_rate_limits_by_status_or_text_are_429_and_other_failures_502(monkeypatch):
    from app.services.ebay.live import EbayApiError

    with TestClient(app) as c:
        _failing_identifier(monkeypatch, EbayApiError("image search failed: 429 Too Many Requests", status=429))
        r = _scan_png(c)
        assert r.status_code == 429 and "retry-after" not in r.headers
        _failing_identifier(monkeypatch, RuntimeError("Too many requests, slow down"))
        assert _scan_png(c).status_code == 429
        _failing_identifier(monkeypatch, RuntimeError("boom"))
        r = _scan_png(c)
        assert r.status_code == 502 and r.json()["detail"].startswith("Identification failed")


def test_live_image_search_error_carries_status_and_retry_after(monkeypatch):
    import asyncio

    import httpx
    import pytest

    from app.config import get_settings
    from app.services.ebay import live
    from app.services.ebay.live import EbayApiError, LiveEbayClient

    class Fake:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **k):
            if url.endswith("/oauth2/token"):
                return httpx.Response(200, json={"access_token": "t", "expires_in": 7200})
            return httpx.Response(429, text="Too Many Requests", headers={"retry-after": "3"})

    monkeypatch.setattr(live.httpx, "AsyncClient", Fake)
    client = LiveEbayClient(get_settings().model_copy(
        update={"ebay_mode": "live", "ebay_client_id": "x", "ebay_client_secret": "y"}))
    with pytest.raises(EbayApiError) as err:
        asyncio.run(client.search_by_image(PNG))
    assert err.value.status == 429 and err.value.retry_after == "3"
    assert str(err.value).startswith("image search failed: 429")


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
        run = c.get("/api/debug/jobs").json()["runs"][0]  # the bulk run is on the job log
        assert run["kind"] == "bulk" and run["total"] == 2 and run["failed"] == 0 and run["finished_at"]
        assert c.get(f"/api/cards/{ids[0]}/debug/traces").json()[0]["trigger"] == "bulk"


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


def test_crop_straightens_a_tilted_card():
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (600, 600), "blue")
    quad = [(200, 80), (420, 140), (370, 470), (150, 410)]  # a tilted card, TL TR BR BL
    ImageDraw.Draw(img).polygon(quad, fill="red")
    buf = io.BytesIO(); img.save(buf, "PNG")
    with TestClient(app) as c:
        s = c.post("/api/scan", files={"file": ("tilt.png", io.BytesIO(buf.getvalue()), "image/png")}).json()
        corners = [[x / 600, y / 600] for x, y in quad]
        r = c.post("/api/scan/crop", json={"image_path": s["image_path"], "box": {"x": .2, "y": .1, "w": .5, "h": .7, "corners": corners}})
        assert r.status_code == 200, r.text
        out = Image.open(io.BytesIO(c.get(r.json()["image_url"]).content)).convert("RGB")
        w, h = out.size
        assert h > w  # card shape, not the tilted bounding box
        px = [out.getpixel((int(w * fx), int(h * fy))) for fx in (.1, .5, .9) for fy in (.1, .5, .9)]
        assert all(p[0] > 200 and p[2] < 60 for p in px)  # all red: no blue background left in the cut-out
        # bad shapes are refused
        assert c.post("/api/scan/crop", json={"image_path": s["image_path"], "box": {"x": 0, "y": 0, "w": 1, "h": 1, "corners": [[0, 0], [0, 0], [0, 0], [0, 0]]}}).status_code == 422
        assert c.post("/api/scan/crop", json={"image_path": s["image_path"], "box": {"x": 0, "y": 0, "w": 1, "h": 1, "corners": [[0, 0], [1, 0], [1, 1]]}}).status_code == 422


def test_duplicates_and_batch():
    with TestClient(app) as c:
        a = c.post("/api/cards", json={"name": "Dupe Guy", "category": "sports", "set_name": "Prizm", "card_number": "#12",
                                       "grade": "PSA 10", "batch": "Box 9 lot"}).json()
        hit = lambda **kw: [x["id"] for x in c.get("/api/cards/duplicates", params={"name": "dupe guy", **kw}).json()]  # noqa: E731
        assert hit(card_number="12", set_name="prizm", grade="PSA 10") == [a["id"]]
        assert hit() == []  # no grade means Raw: a different card from the PSA 10
        assert hit(grade="PSA 10", card_number="13") == []
        assert hit(grade="PSA 10", set_name="Select") == []
        assert hit(grade="PSA 10") == [a["id"]]  # unknown set and number don't rule it out
        c.patch(f"/api/cards/{a['id']}", json={"status": "sold"})
        assert hit(grade="PSA 10") == []  # sold cards aren't duplicates
        assert [x["id"] for x in c.get("/api/cards", params={"q": "Box 9"}).json()] == [a["id"]]


def test_sold_compare_asking_vs_gone():
    from datetime import timedelta

    from sqlmodel import Session

    from app.db import engine
    from app.models import ListingSeen, utcnow
    with TestClient(app) as c:
        card = c.post("/api/cards", json={"name": "Compare Me", "category": "sports", "grade": "PSA 10"}).json()
        now = utcnow()
        with Session(engine) as s:
            for i, (price, status) in enumerate([(100, "active"), (120, "active"), (80, "gone"), (90, "gone")]):
                s.add(ListingSeen(card_id=card["id"], item_id=f"i{i}", grade="PSA 10", title=f"Compare Me PSA 10 #{i}", price=price,
                                  first_seen=now - timedelta(days=10), last_seen=now, status=status,
                                  gone_at=now - timedelta(days=2) if status == "gone" else None))
            s.add(ListingSeen(card_id=card["id"], item_id="old", grade="PSA 10", title="Compare Me old", price=50,
                              first_seen=now - timedelta(days=400), last_seen=now - timedelta(days=300), status="gone",
                              gone_at=now - timedelta(days=300)))
            s.commit()
        r = c.get(f"/api/cards/{card['id']}/sold-compare", params={"days": 90}).json()
        g = r["grades"][0]
        assert g["grade"] == "PSA 10" and g["asking_median"] == 110 and g["asking_count"] == 2
        assert g["sold_median"] == 85 and g["sold_count"] == 2  # the 300-day-old one is outside the window
        assert g["gap"] == -25 and g["gap_pct"] == -22.7 and g["days_median"] == 8
        assert len(r["rows"]) == 2 and r["rows"][0]["days_listed"] == 8 and r["source"] == "estimated"
        assert len(c.get(f"/api/cards/{card['id']}/sold-compare", params={"days": 0}).json()["rows"]) == 3


def _priced_card(c, **kw):
    s = c.post("/api/scan", files={"file": ("card.png", io.BytesIO(PNG), "image/png")}).json()
    i = s["identification"]
    return c.post("/api/cards", json={"category": i["category"], "name": i["name"], "search_query": i["search_query"],
                                      "image_path": s["image_path"], **kw}).json(), s


def test_price_trace_records_every_step():
    with TestClient(app) as c:
        card, _ = _priced_card(c, grade="Raw")
        c.post(f"/api/cards/{card['id']}/price", json={})
        traces = c.get(f"/api/cards/{card['id']}/debug/traces").json()
        assert len(traces) == 1 and traces[0]["trigger"] == "manual" and traces[0]["has_raw"] and traces[0]["calls"] >= 1
        t = c.get(f"/api/cards/{card['id']}/debug/traces/{traces[0]['id']}").json()
        assert t["mode"] == "ebay-image" and t["calls"][0]["api"] == "photo search" and t["calls"][0]["returned"] > 0
        f = t["funnel"][0]
        assert f["grade"] == "Raw" and f["found"] >= f["used"] > 0
        assert f["found"] == f["excluded"] + f["other_identity"] + f["other_version"] + f["outliers"] + f["used"]  # every listing accounted for
        decisions = {r["d"] for r in t["listings"]}
        assert "used" in decisions and {"median", "low", "high"} <= {r["r"] for r in t["listings"] if r["r"]}
        # the snapshot price is exactly what the trace says it is
        assert c.get(f"/api/cards/{card['id']}").json()["est_value"] == f["median"]
        raw = c.get(f"/api/cards/{card['id']}/debug/traces/{traces[0]['id']}/raw")
        assert raw.status_code == 200 and raw.json()["listings"]


def test_trace_shows_exclusions_and_compares_lookups():
    with TestClient(app) as c:
        card, _ = _priced_card(c, grade="Raw")
        cid = card["id"]
        c.post(f"/api/cards/{cid}/price", json={})
        first = c.get(f"/api/cards/{cid}/debug/traces").json()[0]["id"]
        t1 = c.get(f"/api/cards/{cid}/debug/traces/{first}").json()
        victim = next(r for r in t1["listings"] if r["d"] == "used")
        c.post(f"/api/cards/{cid}/matches/exclude", json={"item_ids": [victim["id"]], "excluded": True})
        c.post(f"/api/cards/{cid}/price", json={})
        traces = c.get(f"/api/cards/{cid}/debug/traces").json()
        assert len(traces) == 2 and traces[0]["has_raw"] and not traces[1]["has_raw"]  # raw kept only on the newest
        t2 = c.get(f"/api/cards/{cid}/debug/traces/{traces[0]['id']}").json()
        assert next(r for r in t2["listings"] if r["id"] == victim["id"])["d"] == "excluded"
        assert t2["funnel"][0]["excluded"] == 1
        cmp = c.get(f"/api/cards/{cid}/debug/compare").json()
        assert [d["id"] for d in cmp["decision_changed"]] == [victim["id"]] and cmp["decision_changed"][0]["was_decision"] == "used"
        assert cmp["a"]["id"] == first and cmp["grades"][0]["grade"] == "Raw"
        assert c.get(f"/api/cards/{cid}/debug/compare", params={"a": first}).status_code in (200, 404, 422)


def test_trace_pruning_keeps_listings_for_newest_three():
    with TestClient(app) as c:
        card, _ = _priced_card(c, grade="Raw")
        for _ in range(5):
            c.post(f"/api/cards/{card['id']}/price", json={})
        rows = c.get(f"/api/cards/{card['id']}/debug/traces").json()
        assert [r["has_listings"] for r in rows] == [True, True, True, False, False]


def test_identification_trace_and_debug_pages():
    with TestClient(app) as c:
        card, s = _priced_card(c)
        chain = c.get(f"/api/cards/{card['id']}/debug/identification").json()
        assert len(chain) == 1 and chain[0]["identity"]["identifier"] and chain[0]["identity"]["result"]["name"]
        health = c.get("/api/debug/health").json()
        assert health["version"] and health["db"]["tables"]["card"] >= 1 and health["ebay"]["mode"] == "mock"
        assert c.post("/api/debug/health/ebay-test").json()["ok"] is True
        ev = c.get("/api/debug/events").json()
        assert ev["events"] and ev["last_hour"]["calls"] >= 1
        assert "runs" in c.get("/api/debug/jobs").json()


def test_soldcomps_response_parsing_and_errors(monkeypatch):
    import asyncio

    import httpx

    from app.services import sold
    from app.services.sold import SoldApiError, SoldCompsClient, parse_items

    body = {"items": [
        {"itemId": "1", "title": "Luka Doncic PSA 10", "soldPrice": "99.50", "soldCurrency": "USD", "endedAt": "2026-11-01",
         "bestOfferAccepted": True, "url": "https://ebay.com/itm/1"},
        {"itemId": "2", "title": "euro sale", "soldPrice": "50", "soldCurrency": "EUR", "endedAt": "2026-11-01"},
        {"itemId": "3", "title": "no price", "soldPrice": None, "soldCurrency": "USD"},
        {"itemId": "4", "title": "bad date", "soldPrice": "10", "soldCurrency": "USD", "endedAt": "soon"}]}
    items = parse_items(body)
    assert [i.item_id for i in items] == ["1", "4"] and items[0].best_offer and items[0].price == 99.5 and items[1].ended_at is None

    class Fake:
        status = 200
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, params=None, headers=None):
            Fake.seen = (url, params, headers)
            if Fake.status == 200:
                return httpx.Response(200, json=body, headers={"X-Usage-Remaining": "77"})
            return httpx.Response(Fake.status, json={"code": "quota_exceeded", "message": "monthly quota used"})

    monkeypatch.setattr(sold.httpx, "AsyncClient", Fake)
    from app.config import get_settings
    client = SoldCompsClient(get_settings().model_copy(update={"soldcomps_api_key": "sc_test"}))
    got = asyncio.run(client.search("luka doncic psa 10"))
    url, params, headers = Fake.seen
    assert url.endswith("/v1/scrape") and params["sold"] == "true" and headers["Authorization"] == "Bearer sc_test"
    assert len(got) == 2 and client.usage["x-usage-remaining"] == "77"
    Fake.status = 429
    try:
        asyncio.run(client.search("x"))
        raise AssertionError("expected an error")
    except SoldApiError as e:
        assert e.status == 429 and e.code == "quota_exceeded"


def test_sold_prices_flow_into_the_comparison_and_trend(monkeypatch):
    from app.config import get_settings
    import app.services.identify.mock as mock
    monkeypatch.setattr(get_settings(), "sold_monthly_budget", 2)
    # pinned card: the median comparison below assumes its sales have no outliers to trim
    monkeypatch.setattr(mock, "_SAMPLES", [x for x in mock._SAMPLES if x["name"] == "Tom Brady"])
    with TestClient(app) as c:
        card, _ = _priced_card(c, grade="Raw")
        cid = card["id"]
        c.post(f"/api/cards/{cid}/price", json={})
        r = c.post(f"/api/cards/{cid}/sold/refresh")
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["found"] > 0 and out["median"] > 0 and out["grade"] == "Raw"
        cmp = c.get(f"/api/cards/{cid}/sold-compare", params={"days": 90}).json()
        g = next(x for x in cmp["grades"] if x["grade"] == "Raw")
        assert cmp["source"] == "sold" and g["sold_source"] == "sold" and g["sold_median"] == out["median"] and g["gap"] is not None
        assert cmp["rows"] and cmp["rows"][0]["source"] == "sold" and cmp["provider"]["configured"] and cmp["provider"]["used"] == 1
        trend = c.get(f"/api/cards/{cid}/trend?range=1m&grades=Raw").json()
        assert trend["sold_source"] == "sold" and trend["sold"][0]["points"]
        # the sold check shows up in the card's debug trace list, with its searches
        traces = c.get(f"/api/cards/{cid}/debug/traces").json()
        t = next(x for x in traces if x["kind"] == "sold")
        full = c.get(f"/api/cards/{cid}/debug/traces/{t['id']}").json()
        assert full["mode"] == "ebay-sold" and full["calls"][0]["api"] == "sold search" and full["funnel"][0]["used"] == out["used"]
        # the monthly budget is enforced before any call is made
        assert c.post(f"/api/cards/{cid}/sold/refresh").status_code == 200
        blocked = c.post(f"/api/cards/{cid}/sold/refresh")
        assert blocked.status_code == 503 and "budget" in blocked.json()["detail"]
        health_sold = c.get("/api/debug/health").json()["sold"]
        assert health_sold["used_this_month"] == 2 and health_sold["auto_enabled"] is False  # automatic checks are opt-in


def test_automatic_sold_checks_respect_the_auto_budget(monkeypatch):
    import asyncio

    from app.config import get_settings
    from app.services import sold_refresh
    monkeypatch.setattr(get_settings(), "sold_monthly_budget", 100)
    with TestClient(app) as c:
        ids = [_priced_card(c, grade="Raw")[0]["id"] for _ in range(2)]
        for i in ids:
            c.post(f"/api/cards/{i}/price", json={})
        monkeypatch.setattr(get_settings(), "sold_monthly_budget", 5)  # auto share is 80%: 4 lookups minus what is spent
        done, failed = asyncio.run(sold_refresh.sold_once(pause=0))
        assert failed == 0 and done >= 2
        again, _ = asyncio.run(sold_refresh.sold_once(pause=0))
        assert again == 0  # every card was just checked, so none is due until the refresh interval passes


class _FakeSold:
    """A sold-prices provider that serves canned pages, to test what gets stored and when paging stops."""
    name = "fake"

    def __init__(self, pages):
        self.pages, self.has_next, self.calls = pages, False, 0

    async def search(self, keyword, count=240, page=1):
        self.calls += 1
        self.has_next = page < len(self.pages)
        return self.pages[page - 1]


def _sale(i, title, price, days_ago, **kw):
    from datetime import timedelta

    from app.models import utcnow
    from app.services.sold import SoldItem
    return SoldItem(item_id=f"f{i}", title=title, price=price, ended_at=(utcnow() - timedelta(days=days_ago)).date(), **kw)


def test_every_sale_is_kept_and_prices_use_only_the_window(monkeypatch):
    from sqlmodel import Session, select

    from app.config import get_settings
    from app.db import engine
    from app.models import SoldSale
    from app.services import sold_refresh
    monkeypatch.setattr(get_settings(), "sold_monthly_budget", 50)
    page = [_sale(1, "Hist Card Base", 10, 5), _sale(2, "Hist Card Base", 20, 40), _sale(3, "Hist Card Base", 30, 200),
            _sale(4, "Hist Card lot of 5", 99, 3), _sale(5, "Hist Card PSA 10", 80, 10, best_offer=True, condition="Graded", shipping=4.5)]
    monkeypatch.setattr(sold_refresh, "get_sold", lambda: _FakeSold([page]))
    with TestClient(app) as c:
        card = c.post("/api/cards", json={"name": "Hist Card", "category": "sports", "grade": "Raw"}).json()
        out = c.post(f"/api/cards/{card['id']}/sold/refresh").json()
        assert out["found"] == 5 and out["new"] == 5 and out["on_record"] == 5 and out["used"] == 2  # Raw: the $10 and $20 sales
        with Session(engine) as s:
            rows = {r.item_id: r for r in s.exec(select(SoldSale).where(SoldSale.card_id == card["id"])).all()}
        assert len(rows) == 5 and rows["f3"].price == 30 and rows["f4"].junk and not rows["f1"].junk  # old and junk sales are kept
        assert rows["f5"].grade == "PSA 10" and rows["f5"].best_offer and rows["f5"].shipping == 4.5 and rows["f5"].condition == "Graded"
        # a second check stores nothing twice, and reports that nothing was new
        again = c.post(f"/api/cards/{card['id']}/sold/refresh").json()
        assert again["new"] == 0 and again["on_record"] == 5
        # the trend is built from the stored sales, on the day each happened
        trend = c.get(f"/api/cards/{card['id']}/trend?range=all&grades=Raw").json()
        assert trend["sold_source"] == "sold" and sum(p["samples"] for p in trend["sold"][0]["points"]) == 3  # junk left out
        week = c.get(f"/api/cards/{card['id']}/trend?range=1m&grades=Raw").json()["sold"][0]["points"]
        assert [p["median"] for p in week] == [10]  # one day's sale inside the last month, plotted by day
        info = c.get(f"/api/cards/{card['id']}/sold-compare").json()["provider"]
        assert info["on_record"] == 4 and info["since"]  # the junk lot isn't counted


def test_sold_compare_median_trims_outliers_like_sold_refresh(monkeypatch):
    from statistics import median

    from app.config import get_settings
    from app.services import sold_refresh
    monkeypatch.setattr(get_settings(), "sold_monthly_budget", 50)
    prices = [10, 11, 12, 13, 14, 15, 16, 17, 500]  # the $500 sale is far outside the others
    page = [_sale(i, "Trim Card", p, i + 1) for i, p in enumerate(prices)]
    monkeypatch.setattr(sold_refresh, "get_sold", lambda: _FakeSold([page]))
    with TestClient(app) as c:
        card = c.post("/api/cards", json={"name": "Trim Card", "category": "sports", "grade": "Raw"}).json()
        out = c.post(f"/api/cards/{card['id']}/sold/refresh").json()
        g = next(x for x in c.get(f"/api/cards/{card['id']}/sold-compare?days=90").json()["grades"] if x["grade"] == "Raw")
        assert g["sold_source"] == "sold"
        assert g["sold_median"] == out["median"] == out["sold_value"] == c.get(f"/api/cards/{card['id']}").json()["sold_value"]
        assert g["sold_median"] == 13.5 and g["sold_median"] != median(prices) == 14  # the outlier is trimmed, as in the sold value
        assert g["sold_count"] == 9  # every real sale is counted, trimmed or not


def test_paging_back_only_when_there_may_be_a_gap(monkeypatch):
    from app.config import get_settings
    from app.services import sold_refresh
    monkeypatch.setattr(get_settings(), "sold_monthly_budget", 50)
    first = [_sale(i, "Page Card", 10 + i, i) for i in range(1, 4)]
    with TestClient(app) as c:
        card = c.post("/api/cards", json={"name": "Page Card", "category": "sports", "grade": "Raw"}).json()
        # first check of a card: one page only, because there is nothing stored to overlap with
        fake = _FakeSold([first, [_sale(10, "Page Card", 5, 60)]])
        monkeypatch.setattr(sold_refresh, "get_sold", lambda: fake)
        c.post(f"/api/cards/{card['id']}/sold/refresh")
        assert fake.calls == 1
        # a later check whose page shares a sale with the record: no gap, so one page again
        fake = _FakeSold([[first[0], _sale(11, "Page Card", 12, 0)], [_sale(12, "Page Card", 5, 90)]])
        monkeypatch.setattr(sold_refresh, "get_sold", lambda: fake)
        c.post(f"/api/cards/{card['id']}/sold/refresh")
        assert fake.calls == 1
        # a page sharing nothing with the record may have skipped sales, so the next page is fetched too
        fake = _FakeSold([[_sale(20, "Page Card", 14, 0)], [_sale(21, "Page Card", 15, 1), first[1]]])
        monkeypatch.setattr(sold_refresh, "get_sold", lambda: fake)
        out = c.post(f"/api/cards/{card['id']}/sold/refresh").json()
        assert fake.calls == 2 and out["found"] == 3


def test_sold_value_sits_beside_the_estimate_and_follows_grade_and_version(monkeypatch):
    from app.config import get_settings
    from app.services import sold_refresh
    monkeypatch.setattr(get_settings(), "sold_monthly_budget", 50)
    page = [_sale(i, "Both Card Green /99", 100 + i * 10, i * 5) for i in range(1, 5)] + \
           [_sale(10 + i, "Both Card PSA 10", 500 + i, i) for i in range(1, 4)] + [_sale(30, "Both Card Blue", 40, 2)]
    monkeypatch.setattr(sold_refresh, "get_sold", lambda: _FakeSold([page]))
    with TestClient(app) as c:
        card = c.post("/api/cards", json={"name": "Both Card", "category": "sports", "grade": "Raw", "est_value": None}).json()
        cid = card["id"]
        assert c.get(f"/api/cards/{cid}").json()["sold_value"] is None
        c.post(f"/api/cards/{cid}/sold/refresh")
        got = c.get(f"/api/cards/{cid}").json()
        assert got["sold_value"] == 120 and got["sold_count"] == 5 and got["est_value"] is None  # the estimate is untouched
        # the version and the grade each change which sales count, with no new lookup
        c.patch(f"/api/cards/{cid}", json={"parallel": "Green /99"})
        got = c.get(f"/api/cards/{cid}").json()
        assert got["sold_count"] == 4 and got["sold_value"] == 125
        c.patch(f"/api/cards/{cid}", json={"grade": "PSA 10", "parallel": None})
        got = c.get(f"/api/cards/{cid}").json()
        assert got["sold_count"] == 3 and got["sold_value"] == 502
        c.patch(f"/api/cards/{cid}", json={"grade": "PSA 9"})
        got = c.get(f"/api/cards/{cid}").json()
        assert got["sold_count"] == 0 and got["sold_value"] is None
        c.patch(f"/api/cards/{cid}", json={"grade": "PSA 10"})
        stats = c.get("/api/cards/stats").json()
        assert stats["sold_cards"] >= 1 and stats["sold_value"] >= 502
        assert c.get("/api/portfolio").json()["totals"]["sold_cards"] >= 1


def test_price_points_for_the_asking_vs_sold_chart(monkeypatch):
    from app.config import get_settings
    from app.services import sold_refresh
    monkeypatch.setattr(get_settings(), "sold_monthly_budget", 50)
    with TestClient(app) as c:
        card, _ = _priced_card(c, grade="Raw", id_confidence=0.9)  # sure of the name, so look-alikes are filtered
        cid = card["id"]
        page = [_sale(i, f"{card['name']} Raw", 50 + i, i * 3, best_offer=(i == 2)) for i in range(1, 7)]
        monkeypatch.setattr(sold_refresh, "get_sold", lambda: _FakeSold([page]))
        c.post(f"/api/cards/{cid}/price", json={})
        c.post(f"/api/cards/{cid}/sold/refresh")
        pts = c.get(f"/api/cards/{cid}/price-points", params={"grade": "Raw", "range": "3m"}).json()
        assert len(pts["sales"]) == 6 and pts["sold_count"] == 6 and pts["sold_median"] > 0
        assert any(s["best_offer"] for s in pts["sales"]) and pts["listings"] and pts["asking_line"] and pts["sold_line"]
        assert pts["asking_now"] > 0 and pts["asking_count"] > 0
        assert c.get(f"/api/cards/{cid}/price-points", params={"grade": "PSA 10"}).json()["sales"] == []


def test_sold_value_is_the_primary_value_with_asking_as_the_fallback(monkeypatch):
    from app.config import get_settings
    from app.services import sold_refresh
    monkeypatch.setattr(get_settings(), "sold_monthly_budget", 50)
    sales = [_sale(i, "Primary Card", 200 + i, i * 4) for i in range(1, 6)]  # median 203, five sales
    monkeypatch.setattr(sold_refresh, "get_sold", lambda: _FakeSold([sales]))
    with TestClient(app) as c:
        a = c.post("/api/cards", json={"name": "Primary Card", "category": "sports", "grade": "Raw", "quantity": 2}).json()
        b = c.post("/api/cards", json={"name": "Fallback Card", "category": "sports", "grade": "Raw"}).json()
        none = c.post("/api/cards", json={"name": "Unpriced Card", "category": "sports"}).json()
        for card in (a, b):
            c.patch(f"/api/cards/{card['id']}", json={"search_query": card["name"]})
        c.post(f"/api/cards/{a['id']}/price", json={})
        c.post(f"/api/cards/{b['id']}/price", json={})
        asking_a = c.get(f"/api/cards/{a['id']}").json()["est_value"]
        c.post(f"/api/cards/{a['id']}/sold/refresh")
        ga, gb, gn = (c.get(f"/api/cards/{x['id']}").json() for x in (a, b, none))
        assert ga["value"] == 203 and ga["value_basis"] == "sold" and ga["est_value"] == asking_a  # asking kept beside it
        assert ga["sold_low"] == 201 and ga["sold_high"] == 205
        assert gb["value"] == gb["est_value"] and gb["value_basis"] == "asking"  # too few sales: asking is the fallback
        assert gn["value"] is None and gn["value_basis"] is None
        # sorting, filtering and the headline all use the displayed value
        listed = [x["id"] for x in c.get("/api/cards", params={"q": "Card", "sort": "value"}).json()]
        assert listed.index(a["id"]) < listed.index(none["id"])
        assert a["id"] in [x["id"] for x in c.get("/api/cards", params={"q": "Primary", "min_value": 200, "max_value": 210}).json()]
        assert none["id"] in [x["id"] for x in c.get("/api/cards", params={"q": "Unpriced", "unpriced": True}).json()]
        st = c.get("/api/cards/stats").json()
        assert st["sold_cards"] >= 1 and st["asking_cards"] >= 1 and st["total_value"] >= 2 * 203 + gb["est_value"]
        folio = c.get("/api/portfolio", params={"range": "all"}).json()
        assert folio["totals"]["value"] == st["total_value"] and folio["series"][-1]["value"] == folio["totals"]["value"]
        top = next((t for t in folio["top_cards"] if t["id"] == a["id"]), None)  # only the 8 most valuable are listed
        assert top is None or (top["value"] == 203 and top["basis"] == "sold")
        assert all(t["basis"] in ("sold", "asking", None) for t in folio["top_cards"])


def test_portfolio_history_reaches_back_through_sales(monkeypatch):
    from app.config import get_settings
    from app.services import sold_refresh
    monkeypatch.setattr(get_settings(), "sold_monthly_budget", 50)
    sales = [_sale(i, "History Card", 300, 20 + i * 5) for i in range(5)]  # five sales, 20 to 40 days ago
    monkeypatch.setattr(sold_refresh, "get_sold", lambda: _FakeSold([sales]))
    with TestClient(app) as c:
        card = c.post("/api/cards", json={"name": "History Card", "category": "sports", "grade": "Raw"}).json()
        c.post(f"/api/cards/{card['id']}/sold/refresh")
        series = c.get("/api/portfolio", params={"range": "3m"}).json()["series"]
        assert len(series) > 2 and series[0]["date"] < series[-1]["date"]  # history starts at the oldest sale, not at today
        assert series[-1]["value"] >= 300


def test_identity_filter_drops_lookalikes():
    from app.services.identity import same_card, who_from
    who = who_from("Patrick Surtain II", "2024", "70")
    assert same_card("2024 Panini Mosaic Patrick Surtain II White #70 PSA 9", who)
    assert same_card("Patrick Surtain 2024 Mosaic White Sparkle PSA 9", who)
    assert not same_card("2024 Panini Mosaic Prizm Stained Glass Josh Jacobs PSA 9 #SG-5", who)
    assert not same_card("2021 Patrick Surtain II Mosaic Blue Reactive Rookie PSA 9", who)  # wrong year
    assert not same_card("2024 Mosaic Patrick Surtain II #276 PSA 9", who)  # wrong card number
    assert same_card("2024-25 Mosaic Patrick Surtain II PSA 9 #70", who_from("Patrick Surtain II", "2025", "70"))
    assert same_card("anything at all", None) and who_from("", None, None) is None


def test_a_check_that_matches_nothing_clears_the_old_estimate():
    with TestClient(app) as c:
        card = c.post("/api/cards", json={"category": "sports", "name": "Zzqx Nobodyson", "year": "2024", "card_number": "70",
                                          "grade": "PSA 9", "search_query": "grade probe 1", "id_confidence": 0.9, "est_value": 99}).json()
        cid = card["id"]
        from app.db import engine
        from app.models import Card
        from sqlmodel import Session
        with Session(engine) as s:
            k = s.get(Card, cid); k.est_value = 99.0; s.add(k); s.commit()
        r = c.post(f"/api/cards/{cid}/price").json()
        assert r["snapshot"]["median"] is None  # no listing names this player
        assert c.get(f"/api/cards/{cid}").json()["est_value"] is None


def test_renaming_a_card_rechecks_which_sales_are_look_alikes(monkeypatch):
    from app.config import get_settings
    from app.services import sold_refresh
    monkeypatch.setattr(get_settings(), "sold_monthly_budget", 50)
    mine = [_sale(i, "2024 Mosaic Renamed Player PSA 9", 50 + i, i) for i in range(1, 5)]
    other = [_sale(10 + i, "2024 Mosaic Someone Else PSA 9", 900, i) for i in range(1, 4)]
    monkeypatch.setattr(sold_refresh, "get_sold", lambda: _FakeSold([mine + other]))
    with TestClient(app) as c:
        card = c.post("/api/cards", json={"category": "sports", "name": "2024 Mosaic", "grade": "PSA 9",
                                          "search_query": "renamed player 2024 mosaic", "id_confidence": 0.9}).json()
        c.post(f"/api/cards/{card['id']}/sold/refresh")
        assert c.get(f"/api/cards/{card['id']}").json()["sold_count"] == 7  # "Mosaic" is in every title
        c.patch(f"/api/cards/{card['id']}", json={"name": "Renamed Player"})
        got = c.get(f"/api/cards/{card['id']}").json()
        assert got["sold_count"] == 4 and got["sold_value"] < 100


def test_manual_value_overrides_and_can_be_cleared():
    with TestClient(app) as c:
        card = c.post("/api/cards", json={"category": "sports", "name": "Manual Player", "grade": "PSA 9", "quantity": 2}).json()
        cid = card["id"]
        assert card["value"] is None
        before = c.get("/api/cards/stats").json()
        got = c.patch(f"/api/cards/{cid}", json={"manual_value": 120}).json()
        assert got["value"] == 120 and got["value_basis"] == "manual"
        s = c.get("/api/cards/stats").json()
        assert round(s["total_value"] - before["total_value"], 2) == 240 and s["manual_cards"] >= 1
        p = c.get("/api/portfolio").json()
        assert p["totals"]["manual_cards"] >= 1 and p["totals"]["manual_value"] >= 240
        assert any(t["id"] == cid and t["basis"] == "manual" for t in p["top_cards"]) or p["totals"]["value"] >= 240
        assert c.get("/api/cards", params={"min_value": 100}).json()
        c.post("/api/cards/bulk/update", json={"ids": [cid], "changes": {"manual_value": 150}})
        assert c.get(f"/api/cards/{cid}").json()["value"] == 150
        got = c.patch(f"/api/cards/{cid}", json={"manual_value": None}).json()
        assert got["value"] is None and got["value_basis"] is None


def _lookup_on_add(monkeypatch, search=None):
    """Turn the lookup on add back on, with no pause, counting (or replacing) eBay searches."""
    from app.services import pricing, refresh
    from app.services.ebay import get_ebay

    monkeypatch.setattr(refresh, "LOOKUP_ON_ADD", True)
    monkeypatch.setattr(refresh, "PAUSE_BETWEEN_CARDS", 0)
    calls = []
    real = get_ebay().search_active

    async def counting(query, limit=50):
        calls.append(query)
        if search:
            return await search(query, limit)
        return await real(query, limit)

    monkeypatch.setattr(get_ebay(), "search_active", counting)
    monkeypatch.setattr(pricing, "get_ebay", lambda: get_ebay())
    return calls


SCAN_QUOTE = {"query": "add lookup pikachu", "source": "ebay-active", "sample_size": 3,
              "low": 4.0, "median": 7.5, "mean": 7.0, "high": 12.0}


def test_adding_a_card_looks_it_up_on_ebay(monkeypatch):
    calls = _lookup_on_add(monkeypatch)
    with TestClient(app) as c:
        r = c.post("/api/cards", json={"category": "pokemon", "name": "Add Lookup Pikachu",
                                       "search_query": "add lookup pikachu", "price": SCAN_QUOTE})
        assert r.status_code == 201, r.text
        card = r.json()
        # the response is the card as saved, with the scan-time quote
        assert card["est_value"] == SCAN_QUOTE["median"] and card["grade_counts"] is None
        assert calls == ["add lookup pikachu"]

        assert c.get(f"/api/cards/{card['id']}/matches").json()  # the listings table fills on its own
        grades = [s["grade"] for s in c.get(f"/api/cards/{card['id']}/prices").json()]
        assert None in grades and "Raw" in grades  # scan snapshot kept, plus the card's own grade
        assert c.get(f"/api/cards/{card['id']}").json()["grade_counts"]
        assert c.get(f"/api/cards/{card['id']}/debug/traces").json()[0]["trigger"] == "add"


def test_lookup_on_add_failure_keeps_the_card(monkeypatch):
    async def down(query, limit=50):
        raise RuntimeError("eBay is down")

    _lookup_on_add(monkeypatch, search=down)
    with TestClient(app) as c:
        r = c.post("/api/cards", json={"category": "pokemon", "name": "Add Lookup Down",
                                       "search_query": "add lookup down", "price": SCAN_QUOTE})
        assert r.status_code == 201, r.text
        got = c.get(f"/api/cards/{r.json()['id']}")
        assert got.status_code == 200 and got.json()["name"] == "Add Lookup Down"
        assert got.json()["est_value"] == SCAN_QUOTE["median"]  # scan-time data stays
        trace = c.get(f"/api/cards/{r.json()['id']}/debug/traces").json()[0]
        assert trace["trigger"] == "add" and trace["error"]


def test_adding_several_cards_looks_each_up_once(monkeypatch):
    calls = _lookup_on_add(monkeypatch)
    with TestClient(app) as c:
        ids = [c.post("/api/cards", json={"category": "pokemon", "name": f"Add Batch {i}",
                                          "search_query": f"add batch {i}"}).json()["id"] for i in range(3)]
        assert sorted(calls) == ["add batch 0", "add batch 1", "add batch 2"]  # one search per card
        assert all(c.get(f"/api/cards/{i}/matches").json() for i in ids)


def test_lookup_on_add_runs_one_card_at_a_time(monkeypatch):
    import asyncio

    from app.services import refresh

    active, most = 0, 0

    async def slow(query, limit=50):
        nonlocal active, most
        active += 1
        most = max(most, active)
        await asyncio.sleep(0.01)
        active -= 1
        return []

    _lookup_on_add(monkeypatch, search=slow)
    with TestClient(app) as c:
        ids = [_mk(c, name=f"Add Serial {i}")["id"] for i in range(3)]
        c.delete(f"/api/cards/{ids[2]}")

    async def overlapping():
        await asyncio.gather(*(refresh.price_new_card(i) for i in ids))

    asyncio.run(overlapping())  # the deleted card is skipped without error
    assert most == 1


def _pics(*specs):
    """Raw listings with eBay pictures: (id, price) or (id, price, title)."""
    from app.services.ebay import EbayItem
    return [EbayItem(item_id=k, title=rest[0] if rest else f"Test card {k}", price=p, condition="Ungraded",
                     url=f"https://ebay.test/{k}", image_url=f"https://i.ebayimg.com/{k}.jpg") for k, p, *rest in specs]


def _alike_ebay(monkeypatch, found, photo_finds=True):
    from app.services import pricing

    class FakeEbay:
        async def search_by_image(self, image, limit=50):
            return list(found) if photo_finds else []

        async def search_active(self, query, limit=50):
            return list(found)
    monkeypatch.setattr(pricing, "get_ebay", lambda: FakeEbay())


def _alike_scores(monkeypatch, scores, calls=None, matches=None):
    """Fake CLIP confidences (and borderline feature matches) by listing id; ids missing from `scores` are
    pictures that didn't load."""
    from app.services import pricing
    from app.services.lookalike import Look

    async def fake(photo, urls):
        if calls is not None:
            calls.append(list(urls))
        ids = {u: u.rsplit("/", 1)[1].removesuffix(".jpg") for u in urls}
        return Look("clip", {u: scores[k] for u, k in ids.items() if k in scores},
                    {u: matches[k] for u, k in ids.items() if k in (matches or {})})
    monkeypatch.setattr(pricing, "compare_pictures", fake)


_ABCDE = (("A", 200), ("B", 210), ("C", 220), ("D", 1), ("E", 2))
_ALIKE = {"A": 90, "B": 85, "C": 80, "D": 30, "E": 35}


@pytest.mark.parametrize("photo_finds", [True, False])
def test_listings_that_dont_look_alike_are_left_out(monkeypatch, photo_finds):
    import asyncio

    from app.services import pricing
    from app.services.trace import Trace
    _alike_ebay(monkeypatch, _pics(*_ABCDE), photo_finds)
    _alike_scores(monkeypatch, _ALIKE)
    trace = Trace()
    p = asyncio.run(pricing.quote_grades("q", ["Raw"], image=b"photo", trace=trace))
    assert p.source == ("ebay-image" if photo_finds else "ebay-active")
    assert p.quotes[0].median == 210 and p.quotes[0].sample_size == 3
    for ids in ({i.item_id for i in p.groups["Raw"]}, {i.item_id for i in p.items}, {i.item_id for i in p.comps}):
        assert ids == {"A", "B", "C"}
    assert p.not_alike == {"D", "E"}
    assert {r["id"] for r in trace.listings if r["d"] == "not_alike"} == {"D", "E"}
    assert trace.summary["not_alike"] == 2 and trace.summary["returned"] == 5 and trace.summary["lookalike"] == "clip"
    assert sum("photo" in n for n in trace.notes if "left out" in n) == 1
    assert {r["id"]: r["s"] for r in trace.listings} == _ALIKE  # every listing shows its confidence
    assert trace.funnel[0]["found"] == 3 and trace.funnel[0]["not_alike"] == 2


def test_listings_without_a_scored_picture_are_kept_when_judging_look_alike(monkeypatch):
    import asyncio

    from app.services import pricing
    from app.services.ebay import EbayItem
    from app.services.trace import Trace
    found = _pics(("A", 200), ("B", 210), ("L", 2)) + [EbayItem(item_id="N", title="Test card N", price=1,
                                                                 condition="Ungraded", url="https://ebay.test/N")]
    _alike_ebay(monkeypatch, found)
    _alike_scores(monkeypatch, {"A": 95, "B": 90})  # L's picture didn't load, N has none
    trace = Trace()
    p = asyncio.run(pricing.quote_grades("q", ["Raw"], image=b"photo", trace=trace))
    assert {i.item_id for i in p.groups["Raw"]} == {"A", "B", "L", "N"} and not p.not_alike
    assert p.quotes[0].sample_size == 4 and p.quotes[0].median == 101
    assert {r["id"] for r in trace.listings if r["d"] == "used"} == {"A", "B", "L", "N"}
    assert trace.summary["not_alike"] == 0


def test_look_alike_fetches_at_most_forty_pictures(monkeypatch):
    import asyncio

    from app.services import pricing
    found = _pics(*[(f"x{n}", 100 + n) for n in range(60)])
    calls = []
    _alike_ebay(monkeypatch, found)
    _alike_scores(monkeypatch, {f"x{n}": 90 if n < 40 else 1 for n in range(60)}, calls)
    p = asyncio.run(pricing.quote_grades("q", ["Raw"], image=b"photo"))
    assert calls == [[i.image_url for i in found[:40]]]
    assert len(p.groups["Raw"]) == 60 and not p.not_alike  # 41-60 were never scored, so they stay


@pytest.mark.parametrize("exclude,parallel", [({"A"}, None), (set(), "Base")])
def test_look_alike_filter_is_skipped_when_it_would_leave_nothing_to_price(monkeypatch, exclude, parallel):
    import asyncio

    from app.services import pricing
    from app.services.trace import Trace
    # the only good look-alike is unticked, or another version than the card's
    _alike_ebay(monkeypatch, _pics(("A", 200, "Test card A Green /99"), ("B", 5), ("C", 6), ("D", 7)))
    _alike_scores(monkeypatch, {"A": 90, "B": 20, "C": 20, "D": 20})
    plain = asyncio.run(pricing.quote_grades("q", ["Raw"], parallel=parallel, exclude=exclude))
    trace = Trace()
    p = asyncio.run(pricing.quote_grades("q", ["Raw"], image=b"photo", parallel=parallel, exclude=exclude, trace=trace))
    assert p.quotes[0].median == plain.quotes[0].median == 6 and len(p.groups["Raw"]) == 4 and not p.not_alike
    assert not [r for r in trace.listings if r["d"] == "not_alike"] and trace.summary["not_alike"] == 0
    assert any("Raw" in n and "skipped" in n for n in trace.notes)


def test_no_photo_means_no_look_alike_scoring(monkeypatch):
    import asyncio

    from app.services import pricing
    calls = []
    _alike_ebay(monkeypatch, _pics(*_ABCDE))
    _alike_scores(monkeypatch, _ALIKE, calls)
    p = asyncio.run(pricing.quote_grades("q", ["Raw"], image=None))
    assert calls == [] and p.quotes[0].median == 200 and p.quotes[0].sample_size == 5 and not p.not_alike


def test_a_failed_look_alike_comparison_prices_as_before(monkeypatch):
    import asyncio

    from app.services import pricing
    from app.services.trace import Trace
    found = _pics(*_ABCDE)
    _alike_ebay(monkeypatch, found)

    async def broken(photo, urls):
        raise OSError("cannot identify image file")
    monkeypatch.setattr(pricing, "compare_pictures", broken)
    trace = Trace()
    p = asyncio.run(pricing.quote_grades("q", ["Raw"], image=b"photo", trace=trace))
    unfiltered, _ = pricing.build_quotes(pricing.group_by_grade(found), ["Raw"], "q", "ebay-image")
    assert [q.model_dump() for q in p.quotes] == [q.model_dump() for q in unfiltered] and not p.not_alike
    assert any("photo comparison failed" in n for n in trace.notes)


def test_listings_that_dont_look_alike_leave_the_matches_table(monkeypatch):
    _alike_ebay(monkeypatch, _pics(*_ABCDE))
    with TestClient(app) as c:
        card, _ = _priced_card(c, grade="Raw", id_confidence=0.4)  # no name filter, no re-identification
        cid = card["id"]
        _alike_scores(monkeypatch, {k: 90 for k in _ALIKE})  # an earlier lookup thought they all looked alike
        assert c.post(f"/api/cards/{cid}/price", json={}).json()["snapshot"]["median"] == 200
        assert {r["item_id"] for r in c.get(f"/api/cards/{cid}/matches").json()} == {"A", "B", "C", "D", "E"}
        _alike_scores(monkeypatch, _ALIKE)
        assert c.post(f"/api/cards/{cid}/price", json={}).json()["snapshot"]["median"] == 210
        assert {r["item_id"] for r in c.get(f"/api/cards/{cid}/matches").json()} == {"A", "B", "C"}
        out = c.post(f"/api/cards/{cid}/matches/exclude", json={"item_ids": ["A"]}).json()
        assert out["snapshot"]["median"] == 215 and out["snapshot"]["sample_size"] == 2

        fresh, _ = _priced_card(c, grade="Raw", id_confidence=0.4)  # never tracked them in the first place
        c.post(f"/api/cards/{fresh['id']}/price", json={})
        assert {r["item_id"] for r in c.get(f"/api/cards/{fresh['id']}/matches").json()} == {"A", "B", "C"}


def test_borderline_look_alikes_are_kept_only_when_the_feature_check_finds_the_card(monkeypatch):
    import asyncio

    from app.services import pricing
    from app.services.trace import Trace
    _alike_ebay(monkeypatch, _pics(("A", 200), ("B", 210), ("C", 220), ("D", 230), ("E", 2)))
    # B, C and D are borderline: B's design matched, C's didn't, D's picture couldn't be checked
    _alike_scores(monkeypatch, {"A": 90, "B": 65, "C": 65, "D": 65, "E": 30}, matches={"B": 120, "C": 10})
    trace = Trace()
    p = asyncio.run(pricing.quote_grades("q", ["Raw"], image=b"photo", trace=trace))
    assert p.not_alike == {"C", "E"} and {i.item_id for i in p.groups["Raw"]} == {"A", "B", "D"}
    assert {r["id"]: r["m"] for r in trace.listings} == {"A": None, "B": 120, "C": 10, "D": None, "E": None}
    assert any("below 55 without 100+ matching feature points, or below 80 with fewer than 30" in n for n in trace.notes)


def test_a_strong_feature_match_keeps_a_listing_clip_scores_low(monkeypatch):
    import asyncio

    from app.services import pricing
    _alike_ebay(monkeypatch, _pics(("A", 200), ("B", 210), ("C", 2), ("D", 3), ("E", 220), ("F", 4)))
    # all clearly low but A: B and E share the printed design strongly (E right at the threshold), C and F not enough,
    # and D's picture couldn't be checked
    _alike_scores(monkeypatch, {"A": 90, "B": 40, "C": 40, "D": 40, "E": 54, "F": 30},
                  matches={"B": 150, "C": 60, "E": 100, "F": 99})
    p = asyncio.run(pricing.quote_grades("q", ["Raw"], image=b"photo"))
    assert p.not_alike == {"C", "D", "F"} and {i.item_id for i in p.groups["Raw"]} == {"A", "B", "E"}
    assert p.quotes[0].median == 210


@pytest.mark.parametrize("problem,note", [("missing", "CLIP isn't installed"), ("download", "couldn't be loaded")])
def test_without_clip_the_look_alike_filter_is_skipped(monkeypatch, problem, note):
    import asyncio

    from app.services import lookalike, pricing
    from app.services.trace import Trace

    def no_model():
        if problem == "missing":
            raise lookalike._NotInstalled("No module named 'open_clip'")
        raise OSError("could not download the weights")

    async def no_fetch(urls):
        raise AssertionError("no picture should be fetched without CLIP")
    monkeypatch.setattr(lookalike, "_model", no_model)
    monkeypatch.setattr(lookalike, "_fetch", no_fetch)
    found = _pics(*_ABCDE)
    _alike_ebay(monkeypatch, found)
    trace = Trace()
    p = asyncio.run(pricing.quote_grades("q", ["Raw"], image=b"photo", trace=trace))
    unfiltered, _ = pricing.build_quotes(pricing.group_by_grade(found), ["Raw"], "q", "ebay-image")
    assert [q.model_dump() for q in p.quotes] == [q.model_dump() for q in unfiltered] and not p.not_alike  # median 200
    assert trace.summary["lookalike"] is None and trace.summary["not_alike"] == 0
    assert not [r for r in trace.listings if r["d"] == "not_alike" or r["s"] is not None]
    assert any("look-alike filter was skipped because" in n and note in n for n in trace.notes)


def test_without_clip_tracked_listings_are_not_forgotten(monkeypatch):
    from app.services import lookalike, pricing
    _alike_ebay(monkeypatch, _pics(*_ABCDE))
    with TestClient(app) as c:
        card, _ = _priced_card(c, grade="Raw", id_confidence=0.4)
        cid = card["id"]
        _alike_scores(monkeypatch, {k: 90 for k in _ALIKE})  # with CLIP, everything looked alike and was tracked
        c.post(f"/api/cards/{cid}/price", json={})
        monkeypatch.setattr(pricing, "compare_pictures", lookalike.compare)  # now the real comparison, but no CLIP
        monkeypatch.setattr(lookalike, "_model", lambda: (_ for _ in ()).throw(lookalike._NotInstalled("no torch")))
        assert c.post(f"/api/cards/{cid}/price", json={}).json()["snapshot"]["median"] == 200
        assert {r["item_id"] for r in c.get(f"/api/cards/{cid}/matches").json()} == {"A", "B", "C", "D", "E"}


def test_look_alike_embeddings_are_cached_by_picture_url(monkeypatch):
    import asyncio
    from collections import OrderedDict

    from app.services import lookalike

    class Vec(tuple):
        def __matmul__(self, other):
            return sum(a * b for a, b in zip(self, other))

    fetched, checked = [], []

    async def fetch(urls):
        fetched.extend(urls)
        return {u: u.rsplit("/", 1)[1].removesuffix(".jpg").encode() for u in urls}
    monkeypatch.setattr(lookalike, "_model", lambda: "clip")
    monkeypatch.setattr(lookalike, "_embeddings", OrderedDict())
    monkeypatch.setattr(lookalike, "_fetch", fetch)
    monkeypatch.setattr(lookalike, "card", lambda data: data)
    monkeypatch.setattr(lookalike, "_embed", lambda clip, img: Vec((1.0,)) if img == b"photo" else Vec((int(img) / 100,)))
    monkeypatch.setattr(lookalike, "feature_matches", lambda a, b: checked.append(b) or 50)
    urls = [f"https://i.ebayimg.com/{n}.jpg" for n in (90, 65, 20)] + ["https://example.com/90.jpg"]
    first = asyncio.run(lookalike.compare(b"photo", urls))
    assert first.method == "clip" and first.scores == {urls[0]: 90, urls[1]: 65, urls[2]: 20}  # only eBay pictures
    assert first.matches == {urls[1]: 50, urls[2]: 50} and checked == [b"65", b"20"]  # the ones below 80 get the check
    assert fetched == urls[:3]
    fetched.clear()
    again = asyncio.run(lookalike.compare(b"photo", urls))
    assert again.scores == first.scores and fetched == urls[1:3]  # embeddings were cached; the check needs the pictures


def test_card_crop_and_feature_check_on_synthetic_pictures():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from app.services import lookalike

    rng = np.random.default_rng(7)

    def design():
        img = cv2.resize(rng.integers(0, 255, (35, 25, 3), dtype=np.uint8), (500, 700), interpolation=cv2.INTER_NEAREST)
        for _ in range(40):
            cv2.circle(img, tuple(int(v) for v in rng.integers(0, 500, 2)), int(rng.integers(5, 40)),
                       tuple(int(v) for v in rng.integers(0, 255, 3)), -1)
        return img

    mine, other = design(), design()
    photo = np.full((1000, 900, 3), 40, np.uint8)  # the card held at an angle on a dark table
    corners = np.float32([[0, 0], [500, 0], [500, 700], [0, 700]])
    placed = np.float32([[220, 120], [690, 180], [640, 860], [150, 800]])
    cv2.warpPerspective(mine, cv2.getPerspectiveTransform(corners, placed), (900, 1000), photo, borderMode=cv2.BORDER_TRANSPARENT)
    cropped = lookalike.card(cv2.imencode(".png", photo)[1].tobytes())
    assert cropped.shape[:2] == (700, 500)
    assert lookalike.feature_matches(cropped, mine) >= lookalike.LOOKALIKE_MIN_MATCHES
    assert lookalike.feature_matches(cropped, other) < lookalike.LOOKALIKE_MIN_MATCHES
    assert lookalike.card(b"not a picture") is None


def test_classify_listing_reads_codes_redemption_and_auto():
    from app.services.parallels import classify_listing, parse_parallel
    c = classify_listing("CRA-JMI #BTP-2 BTP2 LOB-EN001 PA-JM I-JM CRA-JMI")
    assert c.codes == ("CRA-JMI", "BTP-2", "LOB-EN001", "PA-JM", "I-JM")  # in order, once each, hyphenated only
    assert classify_listing("2026 Topps #btp-2 Big Ticket").codes == ("BTP-2",)  # upper-cased
    for title in ("PSA10", "MT10", "MLB2026", "NM-MT", "GEM-MT", "ON-CARD", "ALL-STAR", "GAME-USED", "12/150", "/499",
                  "RC/50", "#10", "#23108", "2026", "On-Card", "Cra-Jmi"):
        assert classify_listing(f"2026 Topps Chrome Player {title} Rookie").codes == (), title
    r = classify_listing("Topps 2026 Bowman Chrome Jacob Misiorowski RC Auto Refractor Redemption #CRA-JMI")
    assert r.codes == ("CRA-JMI",) and r.redemption and r.auto
    assert classify_listing("Bowman Chrome Prospect Auto W/Redemption").redemption
    assert classify_listing("Topps Pristine Redemp Card").redemption
    assert not classify_listing("2026 Bowman Chrome Refractor RC").redemption
    for title in ("Red Orbital RPA /99", "Topps Chrome Autograph", "2 Autos Booklet", "on-card auto"):
        assert classify_listing(title).auto, title
    assert not classify_listing("2026 Topps Chrome Automatic Big Ticket").auto
    assert classify_listing("Red Orbital RPA /99")[3:] == ("Red", 99)
    assert parse_parallel("2026 Bowman Chrome #CRA-JMI Auto Refractor /499") == "/499"
    assert parse_parallel("2026 Topps Chrome Green #BTP-2 /99") == "Green /99"
    assert parse_parallel("2026 Topps #BTP-2 Jacob Misiorowski Big Ticket Players") == "Base"


def test_card_code_and_code_key():
    from app.services.parallels import card_code, code_key
    assert card_code("LOB-001") == card_code("LOB-EN001") == card_code("LOB-E001") == "lob1"
    assert card_code("CRA-JMI") == card_code("cra-jmi") == card_code(" #CRA-JMI") == "crajmi"
    assert card_code("BTP-02") == card_code("btp2") == "btp2"
    for number in ("136", "#23", None, "", "NM-MT", "PSA10", "MLB2026", "Rookie Card", "CRA-JMI 5"):
        assert card_code(number) is None, number
    assert code_key("BTP-2") == code_key("btp2") == code_key("BTP-02") == "btp2"
    assert code_key("LOB-EN001") == code_key("LOB-001") == "lob1" and code_key("CRA-JMI") == "crajmi"


# Card 46: a Jacob Misiorowski CRA-JMI auto redemption, whose search also finds $2-3 Big Ticket Players inserts
_CARD46_REDEMPTIONS = [
    ("Topps 2026 Bowman Chrome Jacob Misiorowski CRA-JMI Redemption RC Rookie Auto", 416.99),
    ("Topps 2026 Bowman Chrome Jacob Misiorowski CRA-JMI Redemption RC Rookie Auto", 435),
    ("Topps 2026 Bowman Chrome Jacob Misiorowski RC Auto Refractor Redemption #CRA-JMI", 864.95),
    ("Topps 2026 Bowman Chrome Jacob Misiorowski Brewers Auto redemption REDEMPTION", 450),
]
_CARD46_OTHERS = [
    ("2026 Bowman Chrome Jacob Misiorowski #CRA-JMI Rookie Auto Refractor /499 RC", 600),  # not a redemption
    ("Topps 2026 Topps Pristine Jacob Misiorowski Auto Redemption PA-JM Brewers", 549.99),  # another card's code
]
_CARD46_BTP = [(t, p) for t, p in zip(
    ["2026 Topps #BTP-2 Jacob Misiorowski Big Ticket Players",
     "Topps 2026 chrome Big Ticket Players Rookie Jacob Misiorowski Brewers BTP-2",
     "2026 Topps Chrome -#BTP-2 Jacob Misiorowski Admit One Refractor RC Brewers",
     "2026 Topps Chrome Big Ticket Players Jacob Misiorowski"] * 3,
    [2.45, 2.5, 2.55, 2.6, 2.65, 2.7, 2.75, 2.8, 2.85, 2.9, 2.95, 3.0])]


def _card46():
    from app.services.pricing import group_by_grade
    return group_by_grade(_items(*_CARD46_REDEMPTIONS, *_CARD46_OTHERS, *_CARD46_BTP))


def test_card46_redemption_is_priced_from_its_own_code_only():
    from app.services.identity import CardIdentity
    from app.services.pricing import build_quotes
    groups = _card46()
    plain, _ = build_quotes(groups, ["Raw"], "q", "ebay-active")
    assert plain[0].median < 5
    quotes, used = build_quotes(groups, ["Raw"], "q", "ebay-active", ident=CardIdentity("crajmi", True))
    assert not [i for i in used if "BTP" in i.title or "Big Ticket" in i.title]
    assert sorted(i.price for i in used) == [416.99, 435, 450, 864.95]
    assert 417 <= quotes[0].median <= 865 and quotes[0].sample_size == 4


def test_redemption_rule_works_both_ways():
    from app.services.identity import CardIdentity
    from app.services.parallels import classify_listing
    from app.services.pricing import build_quotes, group_by_grade
    groups = _card46()
    _, used = build_quotes(groups, ["Raw"], "q", "ebay-active", ident=CardIdentity("crajmi", True))
    assert used and all(classify_listing(i.title).redemption for i in used)
    for ident in (CardIdentity("crajmi", False), CardIdentity(None, False)):
        quotes, used = build_quotes(groups, ["Raw"], "q", "ebay-active", ident=ident)
        assert used and not any(classify_listing(i.title).redemption for i in used), ident
    assert {i.price for i in used} >= {600, 2.45}  # no code to check: only the redemptions are left out
    # every listing the wrong kind: no price, and nothing to fall back on
    only_plain = group_by_grade(_items(*_CARD46_OTHERS[:1], *_CARD46_BTP))
    quotes, used = build_quotes(only_plain, ["Raw"], "q", "ebay-active", ident=CardIdentity("crajmi", True))
    assert quotes[0].median is None and quotes[0].sample_size == 0 and not used
    only_redemptions = group_by_grade(_items(*_CARD46_REDEMPTIONS))
    quotes, _ = build_quotes(only_redemptions, ["Raw"], "q", "ebay-active", ident=CardIdentity(None, False))
    assert quotes[0].median is None and quotes[0].sample_size == 0


def test_cards_without_a_code_are_priced_as_before():
    from types import SimpleNamespace

    from app.services.identity import CardIdentity, identity_of_card
    from app.services.pricing import build_quotes, group_by_grade
    groups = group_by_grade(_items(*_CARD46_OTHERS[:1], *_CARD46_BTP))
    plain, plain_used = build_quotes(groups, ["Raw"], "q", "ebay-active")
    for number in (None, "136"):
        card = SimpleNamespace(name="Jacob Misiorowski", set_name="2026 Topps Chrome", variant=None, card_number=number,
                               search_query="2026 Topps Chrome Jacob Misiorowski", id_confidence=0.9)
        ident = identity_of_card(card)
        assert ident == CardIdentity(None, False)
        quotes, used = build_quotes(groups, ["Raw"], "q", "ebay-active", ident=ident)
        assert [q.model_dump() for q in quotes] == [q.model_dump() for q in plain] and used == plain_used
        assert any("BTP-2" in i.title for i in used)  # a code in the title is kept when the card has none


def test_listings_for_another_identity_are_traced(monkeypatch):
    import asyncio

    from app.services import pricing
    from app.services.identity import CardIdentity
    from app.services.trace import DECISIONS, Trace
    ident = CardIdentity("crajmi", True)
    trace = Trace()
    pricing.build_quotes(_card46(), ["Raw"], "q", "ebay-active", trace=trace, ident=ident)
    dropped = {r["t"] for r in trace.listings if r["d"] == "other_identity"}
    assert dropped == {t for t, _ in _CARD46_OTHERS + _CARD46_BTP}
    f = trace.funnel[0]
    assert f["other_identity"] == 14 and f["used"] == 4
    assert f["found"] == f["excluded"] + f["other_identity"] + f["other_version"] + f["outliers"] + f["used"]
    assert sum("CRAJMI" in n and "redemption" in n for n in trace.notes) == 1
    tags = {r["t"]: r["k"] for r in trace.listings}
    assert tags[_CARD46_REDEMPTIONS[2][0]] == "CRA-JMI · Auto · Redemption"
    assert tags[_CARD46_BTP[0][0]] == "BTP-2" and tags[_CARD46_BTP[3][0]] is None
    assert list(DECISIONS).index("other_identity") == list(DECISIONS).index("other_card") + 1
    # through the whole lookup, the summary counts them too
    _alike_ebay(monkeypatch, _pics(*[(f"x{n}", p, t) for n, (t, p) in
                                     enumerate(_CARD46_REDEMPTIONS + _CARD46_OTHERS + _CARD46_BTP)]))
    trace = Trace()
    p = asyncio.run(pricing.quote_grades("q", ["Raw"], image=None, trace=trace, ident=ident))
    assert trace.summary["other_identity"] == 14 == sum(r["d"] == "other_identity" for r in trace.listings)
    assert p.quotes[0].sample_size == 4
    trace = Trace()
    asyncio.run(pricing.quote_grades("q", ["Raw"], image=None, trace=trace))
    assert trace.summary["other_identity"] == 0 and trace.funnel[0]["other_identity"] == 0


def test_redemption_cards_skip_photo_look_alike_matching(monkeypatch):
    import asyncio

    from app.services import pricing
    from app.services.identity import CardIdentity
    from app.services.trace import Trace
    found = _pics(*[(f"x{n}", p, t) for n, (t, p) in enumerate(_CARD46_REDEMPTIONS + _CARD46_OTHERS + _CARD46_BTP)])
    redemptions = {f"x{n}" for n in range(len(_CARD46_REDEMPTIONS))}
    _alike_ebay(monkeypatch, found)
    calls = []
    _alike_scores(monkeypatch, {i.item_id: 10 if i.item_id in {"x0", "x1"} else 90 for i in found}, calls)
    trace = Trace()
    p = asyncio.run(pricing.quote_grades("q", ["Raw"], image=b"photo", trace=trace, ident=CardIdentity("crajmi", True)))
    assert calls == []  # the pictures were never compared
    assert p.source == "ebay-image" and not p.not_alike
    assert not [r for r in trace.listings if r["d"] == "not_alike"]
    assert trace.summary["not_alike"] == 0 and trace.summary["lookalike"] is None
    assert trace.funnel and all(f["not_alike"] == 0 for f in trace.funnel)
    assert sum("Photo matching was skipped because this is a redemption card" in n for n in trace.notes) == 1
    assert not [n for n in trace.notes if "look like the" in n or "photo comparison failed" in n]
    assert {r["id"] for r in trace.listings if r["d"] == "other_identity"} == {i.item_id for i in found} - redemptions
    assert p.quotes[0].sample_size == len(_CARD46_REDEMPTIONS)  # the low-scoring redemptions are priced too


def test_other_cards_keep_photo_look_alike_matching(monkeypatch):
    import asyncio

    from app.services import pricing
    from app.services.identity import CardIdentity
    from app.services.trace import Trace
    _alike_ebay(monkeypatch, _pics(*_ABCDE))
    _alike_scores(monkeypatch, _ALIKE)
    trace = Trace()
    p = asyncio.run(pricing.quote_grades("q", ["Raw"], image=b"photo", trace=trace, ident=CardIdentity(None, False)))
    assert p.not_alike == {"D", "E"} and trace.summary["lookalike"] == "clip"
    assert {r["id"] for r in trace.listings if r["d"] == "not_alike"} == {"D", "E"}
    assert not [n for n in trace.notes if "Photo matching was skipped" in n]


def test_identity_of_card_reads_redemption_from_the_card():
    from types import SimpleNamespace

    from app.services.identity import CardIdentity, identity_of_card

    def card(**kw):
        return SimpleNamespace(**{"name": "Jacob Misiorowski", "set_name": "2026 Bowman Chrome", "variant": None,
                                  "card_number": "CRA-JMI", "search_query": None, "id_confidence": None, **kw})
    assert identity_of_card(card()) == CardIdentity("crajmi", False)
    assert identity_of_card(card(variant="Auto Redemption")) == CardIdentity("crajmi", True)
    assert identity_of_card(card(search_query="2026 Bowman Chrome Misiorowski CRA-JMI REDEMPTION")).redemption
    assert identity_of_card(card(id_confidence=0.3, variant="Auto Redemption")) is None


# Ordinary title words that look like a no-hyphen code ("letters then digits")
_PLAIN_WORDS = ["SP2", "RC1", "Gen2", "PS5", "Top100", "US175"]
_PLAIN_SPELLINGS = [s for w in _PLAIN_WORDS for s in (w, w.lower(), f"#{w}")]


def test_no_hyphen_words_are_not_title_codes():
    from app.services.parallels import classify_listing
    for word in _PLAIN_SPELLINGS + ["BTP2", "Vol3", "Series2", "MVP1"]:
        assert classify_listing(f"2026 Topps Chrome Player {word} Rookie").codes == (), word


def test_no_hyphen_words_never_drop_a_listing_for_another_code():
    from app.services.identity import CardIdentity, fits_identity
    ident = CardIdentity("bcp50", False)
    for word in _PLAIN_SPELLINGS:
        assert fits_identity(f"2026 Bowman Chrome Prospects Player {word} Auto", ident), word
        assert not fits_identity(f"2026 Bowman Chrome Prospects Player {word} BTP-2 Auto", ident), word
        assert fits_identity(f"2026 Bowman Chrome Prospects Player BCP-50 {word} Auto", ident), word


def test_no_hyphen_word_still_confirms_the_cards_own_code():
    from app.services.identity import CardIdentity, fits_identity
    from app.services.parallels import card_code
    btp2 = CardIdentity(card_code("BTP-2"), False)
    assert fits_identity("2026 Topps Big Ticket BTP2 Player", btp2)
    assert fits_identity("2026 Topps CRA-JMI BTP2 Player", btp2)
    assert fits_identity("Player US175 BCP-50", CardIdentity(card_code("US175"), False))
    assert not fits_identity("2026 Topps CRA-JMI BTP3 Player", btp2)  # another no-hyphen word doesn't confirm it


def test_no_hyphen_words_leave_a_grade_priced():
    from app.services.identity import CardIdentity
    from app.services.pricing import build_quotes, group_by_grade
    from app.services.trace import Trace
    groups = group_by_grade(_items(*[(f"2026 Bowman Chrome Prospects Player {w} Auto", 20 + n)
                                     for n, w in enumerate(_PLAIN_WORDS)]))
    trace = Trace()
    quotes, used = build_quotes(groups, ["Raw"], "q", "ebay-active", ident=CardIdentity("bcp50", False), trace=trace)
    assert quotes[0].median is not None and quotes[0].sample_size > 0 and used
    assert not [r for r in trace.listings if r["d"] == "other_identity"]


def test_sold_value_of_a_redemption_uses_only_its_own_redemption_sales(monkeypatch):
    from sqlmodel import Session, select

    from app.config import get_settings
    from app.db import engine
    from app.models import SoldSale
    from app.services import sold_refresh
    monkeypatch.setattr(get_settings(), "sold_monthly_budget", 50)
    page = [_sale(1, "2026 Bowman Chrome Jacob Misiorowski CRA-JMI Redemption Auto", 400, 1),
            _sale(2, "2026 Bowman Chrome Jacob Misiorowski Auto Redemption #CRA-JMI", 450, 2),
            _sale(3, "2026 Bowman Chrome Jacob Misiorowski Brewers Auto REDEMPTION", 500, 3),
            _sale(4, "2026 Bowman Chrome Jacob Misiorowski #CRA-JMI Auto /499", 600, 4),
            _sale(5, "2026 Bowman Chrome Jacob Misiorowski #CRA-JMI Auto /499", 650, 5),
            _sale(6, "2026 Topps Jacob Misiorowski Redemption BTP-2", 5, 2)] + \
           [_sale(10 + n, "2026 Topps #BTP-2 Jacob Misiorowski Big Ticket Players", 2.5 + n / 10, n) for n in range(1, 5)]
    monkeypatch.setattr(sold_refresh, "get_sold", lambda: _FakeSold([page]))
    with TestClient(app) as c:
        card = c.post("/api/cards", json={"name": "Jacob Misiorowski", "category": "sports", "grade": "Raw",
                                          "card_number": "CRA-JMI", "variant": "Auto Redemption"}).json()
        out = c.post(f"/api/cards/{card['id']}/sold/refresh").json()
        assert out["used"] == 3 and out["median"] == 450
        got = c.get(f"/api/cards/{card['id']}").json()
        assert got["sold_value"] == 450 and got["sold_count"] == 3 and got["sold_low"] == 400 and got["sold_high"] == 500
        with Session(engine) as s:
            assert not any(r.junk for r in s.exec(select(SoldSale).where(SoldSale.card_id == card["id"])).all())
        trace = c.get(f"/api/cards/{card['id']}/debug/traces").json()[0]
        assert c.get(f"/api/cards/{card['id']}/debug/traces/{trace['id']}").json()["summary"]["other_identity"] == 7
        # no longer a redemption: the two plain CRA-JMI sales are all that count, too few for a value
        c.patch(f"/api/cards/{card['id']}", json={"variant": "Auto", "search_query": "Jacob Misiorowski CRA-JMI Auto"})
        got = c.get(f"/api/cards/{card['id']}").json()
        assert got["sold_value"] is None and got["sold_count"] == 2


def _quote(median=5.6, spread=1.96, **kw):
    return {"query": "x", "sample_size": 8, "low": 3, "median": median, "mean": 200, "high": 900, "spread": spread, **kw}


def test_wide_spread_quote_flags_the_card_and_withholds_the_asking_value():
    with TestClient(app) as c:
        card = _mk(c, name="Spread Wide", price=_quote(spread=1.96))
        assert card["wide_spread"] is True and card["est_value"] is None
        assert card["value"] is None and card["value_basis"] is None
        # the snapshot still keeps the median and spread
        snap = c.get(f"/api/cards/{card['id']}/prices").json()[0]
        assert snap["median"] == 5.6 and snap["spread"] == 1.96


def test_spread_at_or_below_the_threshold_or_unknown_is_not_flagged():
    with TestClient(app) as c:
        for spread in (1.0, 0.5, None):
            card = _mk(c, name="Spread Ok", price=_quote(spread=spread))
            assert card["wide_spread"] is False and card["est_value"] == 5.6
            assert card["value"] == 5.6 and card["value_basis"] == "asking"


def test_raising_max_price_spread_stops_the_flag(monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "max_price_spread", 2.0)
    with TestClient(app) as c:
        card = _mk(c, name="Spread Raised", price=_quote(spread=1.96))
        assert card["wide_spread"] is False and card["est_value"] == 5.6


def test_unticking_listings_that_narrow_the_spread_clears_the_flag():
    from sqlmodel import Session
    from app.db import engine
    from app.models import ListingSeen
    with TestClient(app) as c:
        card = _mk(c, name="Spread Untick", grade="Raw", price=_quote(spread=1.96, grade="Raw"))
        assert card["wide_spread"] is True
        prices = {"cheap0": 3, "cheap1": 3.2, "cheap2": 3.5, "cheap3": 3.8,
                  "dear0": 400, "dear1": 500, "dear2": 700, "dear3": 900}
        with Session(engine) as session:
            for item_id, p in prices.items():
                session.add(ListingSeen(card_id=card["id"], item_id=f"{card['id']}-{item_id}", grade="Raw",
                                        title=f"Spread Untick {item_id}", price=p))
            session.commit()
        cheap = [f"{card['id']}-cheap{n}" for n in range(4)]
        out = c.post(f"/api/cards/{card['id']}/matches/exclude", json={"item_ids": cheap}).json()
        assert out["snapshot"]["spread"] <= 1.0
        got = c.get(f"/api/cards/{card['id']}").json()
        assert got["wide_spread"] is False and got["est_value"] == 600 and got["value_basis"] == "asking"
        # ticking them back widens the spread again
        out = c.post(f"/api/cards/{card['id']}/matches/exclude", json={"item_ids": cheap, "excluded": False}).json()
        assert out["snapshot"]["spread"] > 1.0 and out["snapshot"]["median"] is not None
        got = c.get(f"/api/cards/{card['id']}").json()
        assert got["wide_spread"] is True and got["est_value"] is None


def test_choosing_a_version_applies_the_spread_flag():
    from sqlmodel import Session
    from app.db import engine
    from app.models import PriceSnapshot
    with TestClient(app) as c:
        card = _mk(c, name="Spread Version", grade="Raw", price=_quote(spread=1.96, grade="Raw"))
        assert card["wide_spread"] is True
        with Session(engine) as session:
            session.add(PriceSnapshot(card_id=card["id"], query="q", source="ebay-image", sample_size=6, low=80, median=100,
                                      mean=100, high=120, grade="Raw", parallel="Green /99", spread=0.3))
            session.add(PriceSnapshot(card_id=card["id"], query="q", source="ebay-image", sample_size=6, low=5, median=50,
                                      mean=200, high=900, grade="Raw", parallel="Red /49", spread=1.5))
            session.commit()
        got = c.patch(f"/api/cards/{card['id']}", json={"parallel": "Green /99"}).json()
        assert got["wide_spread"] is False and got["est_value"] == 100 and got["value"] == 100
        got = c.patch(f"/api/cards/{card['id']}", json={"parallel": "Red /49"}).json()
        assert got["wide_spread"] is True and got["est_value"] is None and got["value"] is None
        # a version with no price yet clears both the estimate and the flag
        got = c.patch(f"/api/cards/{card['id']}", json={"parallel": "Gold /10"}).json()
        assert got["wide_spread"] is False and got["est_value"] is None


def test_reprice_applies_the_spread_flag(monkeypatch):
    import asyncio

    from app.config import get_settings
    from app.models import PriceQuote
    from app.services import refresh
    from app.services.pricing import Priced

    quote = {"median": 50.0, "spread": 1.5}

    async def fake(query, grades, image=None, **kw):
        q = PriceQuote(query=query, grade="Raw", sample_size=8, low=5, median=quote["median"], mean=60, high=900,
                       spread=quote["spread"])
        return Priced([q], {"Raw": 8}, [], {}, [], "ebay-active")
    monkeypatch.setattr(refresh, "quote_grades", fake)
    with TestClient(app) as c:
        card = _mk(c, name="Spread Reprice", grade="Raw")
        reprice = lambda: asyncio.run(refresh.reprice_card(card["id"], "q", "Raw", ["Raw"], None, None))  # noqa: E731
        reprice()
        got = c.get(f"/api/cards/{card['id']}").json()
        assert got["wide_spread"] is True and got["est_value"] is None and got["priced_at"]
        monkeypatch.setattr(get_settings(), "max_price_spread", 2.0)
        reprice()
        got = c.get(f"/api/cards/{card['id']}").json()
        assert got["wide_spread"] is False and got["est_value"] == 50
        # flagged again, then a search that finds nothing clears the flag along with the estimate
        monkeypatch.setattr(get_settings(), "max_price_spread", 1.0)
        reprice()
        assert c.get(f"/api/cards/{card['id']}").json()["wide_spread"] is True
        quote.update(median=None, spread=None)
        reprice()
        got = c.get(f"/api/cards/{card['id']}").json()
        assert got["wide_spread"] is False and got["est_value"] is None


def test_stats_leave_flagged_cards_out_of_value_and_count_them():
    with TestClient(app) as c:
        before = c.get("/api/cards/stats").json()
        _mk(c, name="Spread Stats Flagged", price=_quote(spread=1.96))
        mid = c.get("/api/cards/stats").json()
        assert mid["flagged_cards"] == before["flagged_cards"] + 1
        assert mid["total_value"] == pytest.approx(before["total_value"])
        assert mid["asking_value"] == pytest.approx(before["asking_value"])
        _mk(c, name="Spread Stats Ok", price=_quote(spread=0.5))
        after = c.get("/api/cards/stats").json()
        assert after["flagged_cards"] == mid["flagged_cards"]
        assert after["asking_value"] == pytest.approx(mid["asking_value"] + 5.6)
        assert after["total_value"] == pytest.approx(mid["total_value"] + 5.6)


def test_flagged_card_keeps_its_manual_value():
    with TestClient(app) as c:
        card = _mk(c, name="Spread Manual", manual_value=42.0, price=_quote(spread=1.96))
        assert card["wide_spread"] is True and card["est_value"] is None
        assert card["value"] == 42.0 and card["value_basis"] == "manual"
