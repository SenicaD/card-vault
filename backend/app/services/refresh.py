"""Daily re-pricing of cards still in the collection, so the asking-price trend fills in on its own.

Each card is re-priced at most once per `price_refresh_hours`, judged from its newest asking-price
snapshot, so restarting the container never re-prices everything. Failures are logged and skipped.
"""
import asyncio
import logging
from datetime import timedelta

from sqlmodel import Session, col, select

from ..config import get_settings
from ..db import engine
from ..models import Card, CardStatus, PriceSnapshot, utcnow
from .grading import grade_sort_key, normalize_grade
from .pricing import quote_grades

log = logging.getLogger("cardvault.refresh")

CHECK_EVERY_SECONDS = 3600  # how often to look for cards that are due
PAUSE_BETWEEN_CARDS = 1.0  # be polite to the eBay API
MAX_TRACKED_GRADES = 6


async def refresh_due(max_age: timedelta | None = None, pause: float = PAUSE_BETWEEN_CARDS) -> tuple[int, int]:
    """Re-price every unsold card whose last graded asking snapshot is older than max_age.

    Each card is priced for its own grade plus every grade previously looked up for it. That is one
    eBay search per card however many grades it tracks. Returns (cards refreshed, cards failed).
    """
    if max_age is None:
        max_age = timedelta(hours=get_settings().price_refresh_hours)
    cutoff = utcnow() - max_age
    refreshed = failed = 0

    with Session(engine) as session:
        cards = session.exec(select(Card).where(col(Card.status) != CardStatus.sold)).all()
        due: list[tuple[int, str, str, list[str]]] = []
        for c in cards:
            rows = session.exec(select(PriceSnapshot.grade, PriceSnapshot.created_at).where(
                PriceSnapshot.card_id == c.id, PriceSnapshot.source == "ebay-active",
                col(PriceSnapshot.grade).is_not(None))).all()
            last = max((created for _, created in rows), default=None)
            if last is None or last < cutoff:
                own = normalize_grade(c.grade)
                others = sorted({g for g, _ in rows} - {own}, key=grade_sort_key)
                due.append((c.id, c.search_query or c.name, own, ([own] + others)[:MAX_TRACKED_GRADES]))

    for card_id, query, own, tracked in due:
        try:
            quotes, _, _ = await quote_grades(query, tracked)
        except Exception as e:  # keep going: one bad query shouldn't stop the round
            failed += 1
            log.warning("re-price failed for card %s (%r): %s", card_id, query, e)
            await asyncio.sleep(pause)
            continue
        quotes = [q for q in quotes if q.median is not None]  # don't record empty points
        if quotes:
            with Session(engine) as session:
                card = session.get(Card, card_id)
                if card and card.status != CardStatus.sold:
                    for q in quotes:
                        session.add(PriceSnapshot(card_id=card_id, **q.model_dump()))
                        if q.grade == own:  # the estimate follows only the card's own grade
                            card.est_value = q.median
                            card.priced_at = utcnow()
                    session.add(card)
                    session.commit()
                    refreshed += 1
        await asyncio.sleep(pause)

    if due:
        log.info("re-priced %d of %d due cards (%d failed)", refreshed, len(due), failed)
    return refreshed, failed


async def refresh_loop() -> None:
    while True:
        try:
            await refresh_due()
        except Exception:  # never let the background task die
            log.exception("price refresh round failed")
        await asyncio.sleep(CHECK_EVERY_SECONDS)
