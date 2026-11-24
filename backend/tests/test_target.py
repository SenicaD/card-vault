"""The target price: one value per card, smoothed against day-to-day swings, with a confidence score and reasons."""
from fastapi.testclient import TestClient

from app.main import app
from app.models import AppSettings, Card, CardCategory
from app.services.target import confidence_of, list_price, update_target


def _card(**kw) -> Card:
    return Card(**{"name": "Zed Tester", "category": CardCategory.sports, "parallel": "Base", **kw})


def test_asking_only_cards_are_valued_at_the_asking_discount():
    card = _card(est_value=100)
    update_target(card, None, AppSettings(asking_discount=0.8))
    assert (card.target_value, card.target_basis) == (80, "asking")
    card.sold_value = 70
    update_target(card, None, AppSettings(asking_discount=0.8))
    assert (card.target_value, card.target_basis) == (70, "sold")  # sales beat asking, undiscounted
    card.manual_value = 55
    update_target(card, None, AppSettings(asking_discount=0.8))
    assert (card.target_value, card.target_basis, card.confidence) == (55, "manual", 100)


def test_a_jump_from_one_lookup_waits_for_the_next_to_agree():
    st = AppSettings(asking_discount=1.0, smoothing_band=0.1)
    card = _card(est_value=100)
    update_target(card, None, st)
    assert card.target_value == 100 and card.target_pending is None

    card.est_value = 200
    update_target(card, None, st, fresh=True)  # a daily lookup doubles it
    assert (card.target_value, card.target_pending) == (100, 200)
    card.est_value = 205
    update_target(card, None, st)  # the same lookup's re-value doesn't count as a second opinion
    assert (card.target_value, card.target_pending) == (100, 205)
    card.est_value = 198
    update_target(card, None, st, fresh=True)  # the next lookup agrees
    assert (card.target_value, card.target_pending) == (198, None)

    card.est_value = 95
    update_target(card, None, st, fresh=True)
    card.est_value = 400
    update_target(card, None, st, fresh=True)  # it disagrees with the last jump: still waiting, on the newer price
    assert (card.target_value, card.target_pending) == (198, 400)

    card.est_value = 205
    update_target(card, None, st, fresh=True)  # back within the band of the target: nothing to wait for
    assert (card.target_value, card.target_pending) == (205, None)

    card.est_value = 50
    update_target(card, None, st)  # an edit or a new setting applies at once
    assert (card.target_value, card.target_pending) == (50, None)

    card.est_value = 500
    update_target(card, None, st, fresh=True)
    card.sold_value = 120
    update_target(card, None, st, fresh=True)  # a new basis (sales) applies at once
    assert (card.target_value, card.target_basis, card.target_pending) == (120, "sold", None)


def test_small_moves_apply_at_once():
    st = AppSettings(smoothing_band=0.1)
    card = _card(est_value=100)
    update_target(card, None, st)
    card.est_value = 108
    update_target(card, None, st, fresh=True)
    assert (card.target_value, card.target_pending) == (108, None)


def test_confidence_says_why_a_price_is_doubtful():
    good, reasons = confidence_of(_card(est_value=40, sold_value=38, id_confidence=0.9))
    assert good == 100 and reasons == []

    # Misiorowski: every version pooled, and sold vs asking far apart
    score, reasons = confidence_of(_card(name="Jacob Misiorowski", parallel=None, sold_value=400, est_value=70,
                                         id_confidence=0.92))
    assert score <= 50 and reasons[0].startswith("Sold and asking prices disagree (5.7x)")
    assert any("every colour and print run" in r for r in reasons)

    # an insert name with no player in it
    score, reasons = confidence_of(_card(name="Samurai Japan Generational Greats", est_value=99, id_confidence=0.9))
    assert "No player name to match listings on" in reasons and score < 100

    score, reasons = confidence_of(_card(id_confidence=0.2))
    assert score <= 40 and "No price yet" in reasons and "The card was poorly identified" in reasons


def test_list_price_ends_in_99():
    assert list_price(50, 1.1) == 54.99
    assert list_price(9.10, 1.1) == 10.99
    assert list_price(0.3, 1.1) == 0.99
    assert list_price(None, 1.1) is None and list_price(0, 1.1) is None


def _quote(median):
    return {"query": "q", "sample_size": 10, "low": median * 0.8, "median": median, "mean": median,
            "high": median * 1.2, "spread": 0.2}


def test_the_api_shows_the_target_list_price_and_confidence():
    with TestClient(app) as c:
        card = c.post("/api/cards", json={"name": "Zed Pricey Tester", "category": "sports", "price": _quote(50)}).json()
        assert card["value"] == 50 and card["value_basis"] == "asking"
        assert card["list_price"] == 54.99 and card["value_pending"] is None
        assert card["confidence"] == 90 and card["confidence_reasons"] == ["No sales on record, so this is an asking price"]


def test_review_queue_takes_doubtful_prices_riskiest_first():
    defaults = c_defaults = {"cluster_min_size": 2, "wide_spread_action": "flag", "price_window": 10,
                             "outlier_ratio": None, "asking_discount": 1.0, "smoothing_band": 0.1, "list_markup": 1.1,
                             "review_below": 40}
    with TestClient(app) as c:
        try:
            def unsure(name, median):  # identified with only 55% confidence
                return c.post("/api/cards", json={"name": name, "category": "sports", "id_confidence": 0.55,
                                                  "price": _quote(median)}).json()
            cheap, dear = unsure("Zed Review Cheap", 5), unsure("Zed Review Dear", 500)
            sure = c.post("/api/cards", json={"name": "Zed Review Sure", "category": "sports", "id_confidence": 0.9,
                                              "price": _quote(500)}).json()
            assert cheap["confidence"] == dear["confidence"] == 70 and sure["confidence"] == 90
            ids = lambda r: [x["id"] for x in r.json() if x["name"].startswith("Zed Review")]  # noqa: E731
            assert ids(c.get("/api/cards", params={"needs_review": True})) == []  # 70 and 90 are above 40

            r = c.put("/api/settings", json={**defaults, "review_below": 80})
            assert r.status_code == 200
            queue = ids(c.get("/api/cards", params={"needs_review": True, "sort": "risk"}))
            assert queue == [dear["id"], cheap["id"]]  # $500 at risk before $5; the sure card stays out

            c.patch(f"/api/cards/{dear['id']}", json={"reviewed": True})
            assert ids(c.get("/api/cards", params={"needs_review": True})) == [cheap["id"]]

            # a new asking discount re-values every card at once
            assert c.put("/api/settings", json={**defaults, "asking_discount": 0.8}).status_code == 200
            assert c.get(f"/api/cards/{sure['id']}").json()["value"] == 400
        finally:
            assert c.put("/api/settings", json=c_defaults).status_code == 200
