"""Look up what a card's matching listings actually sold for, and keep it up to date within a monthly budget."""
import asyncio
import logging
import time
from datetime import datetime, timedelta, UTC

from sqlmodel import Session, col, func, select

from ..config import get_settings
from ..db import engine
from ..models import Card, CardStatus, PriceSnapshot, SoldCall, SoldSale, utcnow
from .debuglog import log_event
from .ebay import EbayItem
from .grading import normalize_grade, parse_grade
from .parallels import parse_parallel
from .pricing import _JUNK, build_quotes
from .refresh import PAUSE_BETWEEN_CARDS
from .sold import SoldApiError, get_sold
from .trace import Trace, save_trace

log = logging.getLogger("cardvault.sold")

MIN_SAMPLE_TO_RECORD = 3  # an other-grade price needs at least this many sales to be recorded
AUTO_SHARE = 0.8  # share of the monthly budget automatic checks may use; the rest stays for manual ones
CHECK_EVERY_SECONDS = 3600


class SoldUnavailable(RuntimeError):
    """The feature is off, or this month's budget is spent."""


def month_start() -> datetime:
    n = utcnow()
    return datetime(n.year, n.month, 1, tzinfo=UTC)


def used_this_month(session: Session, kind: str | None = None) -> int:
    q = select(func.coalesce(func.sum(SoldCall.cost), 0)).where(SoldCall.at >= month_start())
    if kind:
        q = q.where(SoldCall.kind == kind)
    return int(session.exec(q).one())


def budget_left(session: Session, kind: str = "manual") -> int:
    limit = get_settings().sold_monthly_budget
    if kind == "auto":
        return int(limit * AUTO_SHARE) - used_this_month(session, "auto")
    return limit - used_this_month(session)


def sold_keyword(card: Card) -> str:
    """The words to search sold listings for: the card's search words plus its grade, if graded."""
    base = (card.search_query or card.name).strip()
    own = normalize_grade(card.grade)
    return base if own == "Raw" or own.lower() in base.lower() else f"{base} {own}"


async def refresh_sold(session: Session, card: Card, kind: str = "manual") -> dict:
    """One sold-prices search for this card. Stores the sales found and a sold-price snapshot per grade.

    Raises SoldUnavailable (off / budget spent) or SoldApiError (the provider said no).
    """
    provider = get_sold()
    if provider is None:
        raise SoldUnavailable("Sold prices aren't set up. Add a SoldComps key to turn them on.")
    if budget_left(session, kind) <= 0:
        raise SoldUnavailable(f"This month's sold-price budget is used up ({get_settings().sold_monthly_budget} lookups).")

    s = get_settings()
    own = normalize_grade(card.grade)
    keyword = sold_keyword(card)
    trace = Trace("sold", kind, keyword, [own], card.parallel)
    t0, err, items = time.monotonic(), None, []
    try:
        items = await provider.search(keyword)
    except Exception as e:
        err = repr(e)[:300]
        raise
    finally:
        ms = round((time.monotonic() - t0) * 1000)
        rec = {"api": "sold search", "purpose": "what matching cards sold for", "detail": keyword[:200], "ms": ms,
               "returned": len(items) if not err else None, "error": err}
        log_event(source="soldcomps", **rec)
        trace.calls.append(rec)
        session.add(SoldCall(card_id=card.id, kind=kind, cost=0 if err else 1, ok=not err, error=err))
        if err:
            trace.error = err
            save_trace(session, trace, card.id)  # commits the call record too

    cutoff = (utcnow() - timedelta(days=s.sold_window_days)).date()
    groups: dict[str, list[EbayItem]] = {}
    best_offers = 0
    ended: dict[str, object] = {}
    for it in items:
        if it.ended_at and it.ended_at < cutoff:
            continue
        if _JUNK.search(it.title):
            continue
        g = parse_grade(it.title) or "Raw"  # a title with no grader and grade is a raw card
        best_offers += it.best_offer
        ended[it.item_id] = it
        groups.setdefault(g, []).append(EbayItem(item_id=it.item_id, title=it.title, price=it.price, currency=it.currency,
                                                 url=it.url, image_url=it.image_url))

    # Keep every sale found, whatever its grade, so the sold table can show other grades too
    have = {r.item_id: r for r in session.exec(select(SoldSale).where(SoldSale.card_id == card.id)).all()}
    for g, lst in groups.items():
        for i in lst:
            src = ended[i.item_id]
            when = datetime(src.ended_at.year, src.ended_at.month, src.ended_at.day, tzinfo=UTC) if src.ended_at else None
            row = have.get(i.item_id) or SoldSale(card_id=card.id, item_id=i.item_id, grade=g, title=i.title, price=i.price)
            row.grade, row.title, row.price, row.ended_at, row.best_offer = g, i.title, i.price, when, src.best_offer
            row.url, row.image_url, row.fetched_at = i.url, i.image_url, utcnow()
            session.add(row)

    grades = [own] + [g for g, lst in groups.items() if g != own and len(lst) >= MIN_SAMPLE_TO_RECORD]
    quotes, _ = build_quotes(groups, grades, keyword, "ebay-sold", card.parallel, frozenset(), trace)
    saved = []
    for q in quotes:
        if q.median is not None and (q.grade == own or q.sample_size >= MIN_SAMPLE_TO_RECORD):
            session.add(PriceSnapshot(card_id=card.id, **q.model_dump()))
            saved.append(q)
    trace.mode = "ebay-sold"
    trace.summary = {"returned": len(items), "after_top_up": sum(len(v) for v in groups.values()), "mode": "sold",
                     "junk": 0, "unknown_grade": 0, "best_offer": best_offers, "window_days": s.sold_window_days,
                     "grades_found": {g: len(v) for g, v in groups.items()}}
    if best_offers:
        trace.note(f"{best_offers} of these were Best Offer sales. For those the price shown is the asking price, "
                   "so the real sale price was probably lower.")
    if not items:
        trace.note("The provider found no sold listings for these words.")
    session.commit()
    save_trace(session, trace, card.id)
    mine = next((q for q in quotes if q.grade == own), None)
    return {"found": len(items), "used": mine.sample_size if mine else 0, "grade": own,
            "median": mine.median if mine else None, "best_offers": best_offers,
            "budget_left": budget_left(session, "manual")}


def _due_cards(session: Session) -> list[int]:
    s = get_settings()
    cutoff = utcnow() - timedelta(days=s.sold_refresh_days)
    checked = dict(session.exec(select(SoldCall.card_id, func.max(SoldCall.at)).where(SoldCall.ok == True).group_by(SoldCall.card_id)).all())  # noqa: E712
    cards = session.exec(select(Card).where(col(Card.status) != CardStatus.sold,
                                            func.coalesce(Card.est_value, 0) >= s.sold_min_value)
                         .order_by(col(Card.est_value).desc().nulls_last())).all()  # most valuable first
    return [c.id for c in cards if checked.get(c.id) is None or checked[c.id] < cutoff]


async def sold_once(pause: float | None = None) -> tuple[int, int]:
    """Check every card that is due, most valuable first, until the automatic budget runs out."""
    pause = PAUSE_BETWEEN_CARDS if pause is None else pause
    done = failed = 0
    with Session(engine) as session:
        due = _due_cards(session)
    for card_id in due:
        with Session(engine) as session:
            if budget_left(session, "auto") <= 0:
                log.info("sold prices: the automatic budget for this month is used up")
                break
            card = session.get(Card, card_id)
            if card is None:
                continue
            try:
                await refresh_sold(session, card, "auto")
                done += 1
            except SoldUnavailable:
                break
            except Exception as e:
                failed += 1
                session.rollback()
                log.warning("sold lookup failed for card %s: %s", card_id, e)
                if isinstance(e, SoldApiError) and e.status in (401, 429):
                    break  # a bad key or a spent quota won't fix itself on the next card
        await asyncio.sleep(pause)
    return done, failed


async def sold_loop() -> None:
    while True:
        try:
            await sold_once()
        except Exception:
            log.exception("sold price round failed")
        await asyncio.sleep(CHECK_EVERY_SECONDS)
