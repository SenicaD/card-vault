"""One target price per card, how sure we are of it, and what to list it at.

The target is the value the app shows, totals and lists at: the value you set by hand, else what matching copies
sold for, else the asking estimate times the Settings page's asking_discount (asking prices run above what cards
sell for). It is recalculated whenever a card is saved, by a before_flush hook, so every path that changes a price
(a lookup, a sold-price check, re-value, an edit) keeps it current without calling anything.

Smoothing: when a daily or bulk eBay re-price (mark_fresh) moves the target by more than smoothing_band, the new
price waits as target_pending until the next lookup agrees with it (within the band), so one odd day of listings
doesn't swing the price. A changed basis (asking -> sold), your own value, or a recalculation you asked for
(re-value, an edit, a new setting) applies at once.

Confidence (0-100) starts at 100 and loses points for each reason the price may be wrong; the reasons are kept so
the card page and the review queue can say why. Cards under the Settings page's review_below go to the review queue.
"""
import math
from datetime import datetime, timezone

from sqlalchemy import event
from sqlmodel import Session

from ..models import Card, CardCategory, CardStatus
from .app_settings import load_app_settings
from .identity import surname_of

_FRESH: set[int] = set()  # cards whose newest price came from a fresh eBay lookup, so it is smoothed


def mark_fresh(card_id: int | None) -> None:
    """Smooth the next target change of this card: its price just came from a scheduled or bulk eBay lookup."""
    if card_id is not None:
        _FRESH.add(card_id)


def clear_fresh(card_id: int | None) -> None:
    """Forget mark_fresh once the lookup's save is done, in case nothing about the card changed and no flush used it."""
    _FRESH.discard(card_id)


def raw_target(card: Card, asking_discount: float = 1.0) -> tuple[float | None, str | None]:
    """The target before smoothing, and where it came from: "manual", "sold" or "asking"."""
    if card.manual_value is not None:
        return card.manual_value, "manual"
    if card.sold_value is not None:
        return card.sold_value, "sold"
    if card.est_value is not None:
        return round(card.est_value * asking_discount, 2), "asking"
    return None, None


def _snap(session: Session, card: Card):
    if card.id is None or session is None:
        return None
    from .tracker import own_snap  # tracker imports pricing; kept local so this module stays light to import
    with session.no_autoflush:
        return own_snap(session, card)


def confidence_of(card: Card, snap=None) -> tuple[int, list[str]]:
    """How sure the target is (0-100) and why points were taken off, worst first."""
    if card.manual_value is not None:
        return 100, []
    hits: list[tuple[int, str]] = []
    if card.est_value is None and card.sold_value is None:
        hits.append((60, "No price yet"))
    if card.id_confidence is not None and card.id_confidence < 0.3:
        hits.append((40, "The card was poorly identified"))
    elif card.id_confidence is not None and card.id_confidence < 0.6:
        hits.append((20, "The card's identification is uncertain"))
    if card.category == CardCategory.sports and not surname_of(card.name):
        hits.append((20, "No player name to match listings on"))
    if not card.parallel:
        hits.append((15, "No version picked, so every colour and print run is pooled"))
    if card.sold_value and card.est_value:
        ratio = max(card.sold_value, card.est_value) / max(0.01, min(card.sold_value, card.est_value))
        if ratio >= 2:
            hits.append((35 if ratio >= 4 else 25, f"Sold and asking prices disagree ({ratio:.1f}x)"))
    elif card.est_value is not None and card.sold_value is None:
        hits.append((10, "No sales on record, so this is an asking price"))
    if card.wide_spread:
        hits.append((25, "Asking prices spread too widely to price"))
    elif snap is not None and snap.spread is not None and snap.spread >= 1.0:
        hits.append((35 if snap.spread >= 2.0 else 20, f"Asking prices spread widely ({snap.spread:.0%} of the median)"))
    elif snap is not None and snap.spread is not None and snap.spread >= 0.6:
        hits.append((10, f"Asking prices spread somewhat ({snap.spread:.0%} of the median)"))
    if snap is not None and snap.sample_size is not None and snap.sample_size < 5 and card.sold_value is None:
        hits.append((15, f"Only {snap.sample_size} matching listing{'s' if snap.sample_size != 1 else ''}"))
    hits.sort(key=lambda h: -h[0])
    return max(0, 100 - sum(h[0] for h in hits)), [h[1] for h in hits]


def update_target(card: Card, session: Session, app_settings=None, fresh: bool = False) -> None:
    """Recalculate the card's target, pending price and confidence (see the module docstring)."""
    st = app_settings or load_app_settings(session)
    value, basis = raw_target(card, st.asking_discount)
    old, band, pending = card.target_value, st.smoothing_band, card.target_pending
    moved = old is not None and value is not None and abs(value - old) > band * old
    near_pending = pending is not None and value is not None and abs(value - pending) <= band * pending
    if value is None or old is None or basis != card.target_basis or not moved:
        card.target_value, card.target_pending = value, None
    elif fresh and near_pending:
        card.target_value, card.target_pending = value, None  # a second lookup agrees: take the new price
    elif fresh or near_pending:
        # a lookup's jump waits for the next lookup; any other save (the same lookup's re-value, a re-value with no
        # new listings) keeps it waiting rather than confirming it
        card.target_pending = value
    else:
        card.target_value, card.target_pending = value, None  # an edit or new setting moved it: apply at once
    card.target_basis = basis if card.target_value is not None else None
    card.confidence, card.confidence_reasons = confidence_of(card, _snap(session, card))
    if card.target_value != old:
        card.target_at = datetime.now(timezone.utc)


def list_price(target: float | None, markup: float) -> float | None:
    """What to list a card at: the target times the markup, ending in .99 (never below $0.99)."""
    if not target:
        return None
    return round(max(0.99, math.ceil(round(target * markup, 2)) - 0.01), 2)  # round first: 50 x 1.1 = 55.000000000000007


@event.listens_for(Session, "before_flush")
def _keep_targets_current(session: Session, _ctx, _instances) -> None:
    cards = [o for o in (*session.new, *session.dirty) if isinstance(o, Card) and o.status != CardStatus.sold]
    if not cards:
        return
    with session.no_autoflush:
        st = load_app_settings(session)
    for card in cards:
        fresh = card.id in _FRESH
        _FRESH.discard(card.id)
        update_target(card, session, st, fresh)


def backfill_targets() -> int:
    """At startup: give every card saved before targets existed its target and confidence."""
    from sqlmodel import col, select

    from ..db import engine
    with Session(engine) as session:
        cards = session.exec(select(Card).where(col(Card.confidence).is_(None))).all()
        for card in cards:
            session.add(card)
            update_target(card, session)
        session.commit()
        return len(cards)
