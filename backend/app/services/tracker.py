"""Watch the listings that turn up in a card's searches, and notice the ones that disappear.

eBay's only sold-price feed is closed to us, so this is a proxy: a fixed-price listing that vanishes
was probably bought, and its last asking price is our best guess at the sale price. It can also mean the
seller ended it or relisted it under a new id, so the result is shown as an estimate, never as a sale.

A listing that merely drops out of the top search results is NOT counted. Anything missing is checked
with a single-item lookup first, and only counted when eBay says it is gone.
"""
import logging

from sqlmodel import Session, col, select

from ..models import ASKING_SOURCES, Card, ListingSeen, PriceSnapshot, utcnow
from .app_settings import load_app_settings
from .ebay import EbayItem, get_ebay
from .grading import normalize_grade
from .identity import identity_of_card
from .pricing import apply_asking, asking_listings

log = logging.getLogger("cardvault.tracker")

MAX_CHECKS_PER_CARD = 20  # single-item lookups per search; the rest are checked next time


async def track_listings(session: Session, card_id: int, groups: dict[str, list[EbayItem]],
                        forget: set[str] = frozenset()) -> dict[str, int]:
    """Record this search's listings and check the ones that were up before but aren't now.

    forget holds item ids this search ruled out (their picture doesn't look like the card's photo): their active
    rows are deleted, unless you unticked them, so they leave the matches table and can't come back into a re-price.
    Returns counts: seen, new, gone (newly gone), checked, unknown (couldn't be checked).
    Never raises on an eBay error: tracking must not break a price lookup.
    """
    now = utcnow()
    rows = {r.item_id: r for r in session.exec(select(ListingSeen).where(ListingSeen.card_id == card_id)).all()}
    for item_id in forget:
        row = rows.get(item_id)
        if row is not None and row.status == "active" and not row.excluded:
            session.delete(row)
            del rows[item_id]
    stats = dict(seen=0, new=0, gone=0, checked=0, unknown=0)
    in_results: set[str] = set()

    for grade, items in groups.items():
        if grade == "Unknown":  # no stated grade, so we can't say which line it belongs to
            continue
        for it in items:
            if it.price <= 0 or it.item_id in in_results:
                continue
            in_results.add(it.item_id)
            stats["seen"] += 1
            row = rows.get(it.item_id)
            if row is None:
                row = ListingSeen(card_id=card_id, item_id=it.item_id, grade=grade, title=it.title, price=it.price,
                                  currency=it.currency, condition=it.condition, url=it.url, image_url=it.image_url,
                                  first_seen=now, last_seen=now)
                rows[it.item_id] = row
                stats["new"] += 1
            else:
                row.grade, row.title, row.price, row.last_seen = grade, it.title, it.price, now
                row.url, row.image_url, row.condition = it.url or row.url, it.image_url or row.image_url, it.condition
                if row.status == "gone":  # it came back (relisted under the same id)
                    row.status, row.gone_at, row.verdict = "active", None, None
            session.add(row)

    missing = [r for r in rows.values() if r.status == "active" and r.item_id not in in_results]
    ebay = get_ebay()
    for row in missing[:MAX_CHECKS_PER_CARD]:
        try:
            verdict = await ebay.get_item_status(row.item_id)
        except Exception as e:  # can't tell: leave it active and look again next time
            stats["unknown"] += 1
            log.warning("could not check listing %s: %r", row.item_id, e)
            continue
        stats["checked"] += 1
        if verdict == "active":  # still up, it just ranked below the results we fetched
            row.last_seen = now
        else:
            row.status, row.gone_at, row.verdict = "gone", now, verdict
            stats["gone"] += 1
        session.add(row)
    session.commit()
    return stats


def own_snap(session: Session, card: Card) -> PriceSnapshot | None:
    """The newest asking price for the card's own grade and version."""
    return session.exec(select(PriceSnapshot).where(
        PriceSnapshot.card_id == card.id, col(PriceSnapshot.source).in_(ASKING_SOURCES), PriceSnapshot.grade == normalize_grade(card.grade), PriceSnapshot.parallel == card.parallel,
        col(PriceSnapshot.median).is_not(None)).order_by(col(PriceSnapshot.created_at).desc()).limit(1)).first()


def revalue(session: Session, card: Card, app_settings=None) -> bool:
    """Re-value a card from its newest quote and the listings already saved, with no eBay call. False if it has no quote."""
    snap = own_snap(session, card)
    if snap is None:
        return False
    own = normalize_grade(card.grade)
    rows = session.exec(select(ListingSeen).where(ListingSeen.card_id == card.id, ListingSeen.status == "active",
                                                  ListingSeen.grade == own)).all()
    listings = asking_listings(rows, card.parallel, {r.item_id for r in rows if r.excluded}, identity_of_card(card))
    apply_asking(card, snap.median, snap.spread, [r.price for r in listings], app_settings or load_app_settings(session))
    return True
