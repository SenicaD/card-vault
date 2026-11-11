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
from .pricing import quote

log = logging.getLogger("cardvault.refresh")

CHECK_EVERY_SECONDS = 3600  # how often to look for cards that are due
PAUSE_BETWEEN_CARDS = 1.0  # be polite to the eBay API


async def refresh_due(max_age: timedelta | None = None, pause: float = PAUSE_BETWEEN_CARDS) -> tuple[int, int]:
    """Re-price every unsold card whose last asking-price snapshot is older than max_age.

    Returns (refreshed, failed).
    """
    if max_age is None:
        max_age = timedelta(hours=get_settings().price_refresh_hours)
    cutoff = utcnow() - max_age
    refreshed = failed = 0

    with Session(engine) as session:
        cards = session.exec(select(Card).where(col(Card.status) != CardStatus.sold)).all()
        due: list[tuple[int, str]] = []
        for c in cards:
            last = session.exec(
                select(PriceSnapshot.created_at)
                .where(PriceSnapshot.card_id == c.id, PriceSnapshot.source == "ebay-active")
                .order_by(col(PriceSnapshot.created_at).desc()).limit(1)
            ).first()
            if last is None or last < cutoff:
                due.append((c.id, c.search_query or c.name))

    for card_id, query in due:
        try:
            q, _ = await quote(query)
        except Exception as e:  # keep going: one bad query shouldn't stop the round
            failed += 1
            log.warning("re-price failed for card %s (%r): %s", card_id, query, e)
            await asyncio.sleep(pause)
            continue
        if q.median is None:  # no usable listings; don't record an empty point
            await asyncio.sleep(pause)
            continue
        with Session(engine) as session:
            card = session.get(Card, card_id)
            if not card or card.status == CardStatus.sold:
                continue
            session.add(PriceSnapshot(card_id=card_id, **q.model_dump()))
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
