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
from .pricing import _JUNK, build_quotes, summarize
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


def recompute_sold(session: Session, card: Card) -> None:
    """Set the card's sold-based value from the sales on record for its own grade and version.

    Same rules as the sold prices elsewhere: sales inside the window, lots left out, outliers trimmed.
    The value needs at least SOLD_MIN_SALES sales; below that only the count is kept.
    """
    s = get_settings()
    cutoff = utcnow() - timedelta(days=s.sold_window_days)
    own = normalize_grade(card.grade)
    rows = [r for r in session.exec(select(SoldSale).where(SoldSale.card_id == card.id, SoldSale.junk == False,  # noqa: E712
                                                           SoldSale.grade == own)).all()
            if r.ended_at and r.ended_at >= cutoff and (not card.parallel or parse_parallel(r.title) == card.parallel)]
    stats = summarize([EbayItem(item_id=r.item_id, title=r.title, price=r.price) for r in rows])
    card.sold_count = stats["sample_size"]
    card.sold_value = stats["median"] if stats["sample_size"] >= s.sold_min_sales else None
    card.sold_at = utcnow() if rows else None
    session.add(card)


def backfill_sold_values() -> int:
    """At startup: give every card that already has stored sales its sold-based value."""
    with Session(engine) as session:
        ids = session.exec(select(SoldSale.card_id).distinct()).all()
        for cid in ids:
            card = session.get(Card, cid)
            if card:
                recompute_sold(session, card)
        session.commit()
        return len(ids)


MAX_PAGES = 3  # most pages one check will fetch (each page is one lookup)


async def _fetch_pages(session: Session, card: Card, provider, keyword: str, kind: str, trace: Trace) -> list:
    """Fetch the newest sales, and keep going back a page at a time only while there may be sales we
    have never seen: that is, while the page shared nothing with what is already stored for this card.
    The first check of a card is a single page, because with nothing stored there is nothing to overlap."""
    known = set(session.exec(select(SoldSale.item_id).where(SoldSale.card_id == card.id)).all())
    items: list = []
    for page in range(1, MAX_PAGES + 1):
        if page > 1 and budget_left(session, kind) <= 0:
            trace.note("Stopped paging because this month's lookup budget ran out.")
            break
        t0, err, got = time.monotonic(), None, []
        try:
            got = await provider.search(keyword, page=page)
        except Exception as e:
            err = repr(e)[:300]
            raise
        finally:
            rec = {"api": "sold search", "purpose": f"what matching cards sold for (page {page})", "detail": keyword[:200],
                   "ms": round((time.monotonic() - t0) * 1000), "returned": len(got) if not err else None, "error": err}
            log_event(source="soldcomps", **rec)
            trace.calls.append(rec)
            session.add(SoldCall(card_id=card.id, kind=kind, cost=0 if err else 1, ok=not err, error=err))
            if err:
                trace.error = err
                save_trace(session, trace, card.id)  # commits the call record too
        items += got
        overlaps = any(i.item_id in known for i in got)
        if not (getattr(provider, "has_next", False) and known and not overlaps):
            break
        trace.note(f"Page {page} shared no sales with the ones already on record, so the next page was fetched too.")
    return items


async def refresh_sold(session: Session, card: Card, kind: str = "manual") -> dict:
    """Search for this card's sold listings and add every sale found to the card's history.

    Everything the provider returns is kept (sales older than the price window, lots and bundles too,
    flagged), so the record only grows and a sale never needs fetching twice. The prices use only the
    in-window, non-junk sales. Raises SoldUnavailable (off / budget spent) or SoldApiError.
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
    items = await _fetch_pages(session, card, provider, keyword, kind, trace)

    cutoff = (utcnow() - timedelta(days=s.sold_window_days)).date()
    have = {r.item_id: r for r in session.exec(select(SoldSale).where(SoldSale.card_id == card.id)).all()}
    groups: dict[str, list[EbayItem]] = {}
    best_offers = new_rows = 0
    for it in items:
        junk = bool(_JUNK.search(it.title))
        g = parse_grade(it.title) or "Raw"  # a title with no grader and grade is a raw card
        when = datetime(it.ended_at.year, it.ended_at.month, it.ended_at.day, tzinfo=UTC) if it.ended_at else None
        row = have.get(it.item_id)
        if row is None:
            row = SoldSale(card_id=card.id, item_id=it.item_id, grade=g, title=it.title, price=it.price)
            have[it.item_id] = row
            new_rows += 1
        row.grade, row.title, row.price, row.ended_at, row.best_offer, row.junk = g, it.title, it.price, when, it.best_offer, junk
        row.url, row.image_url, row.condition, row.shipping, row.fetched_at = it.url, it.image_url, it.condition, it.shipping, utcnow()
        session.add(row)
        if junk or (it.ended_at and it.ended_at < cutoff):
            continue  # on record, but not part of this price
        best_offers += it.best_offer
        groups.setdefault(g, []).append(EbayItem(item_id=it.item_id, title=it.title, price=it.price, currency=it.currency,
                                                 url=it.url, image_url=it.image_url))

    session.flush()  # so the sales just added are visible to the value below
    recompute_sold(session, card)
    grades = [own] + [g for g, lst in groups.items() if g != own and len(lst) >= MIN_SAMPLE_TO_RECORD]
    quotes, _ = build_quotes(groups, grades, keyword, "ebay-sold", card.parallel, frozenset(), trace)
    for q in quotes:
        if q.median is not None and (q.grade == own or q.sample_size >= MIN_SAMPLE_TO_RECORD):
            session.add(PriceSnapshot(card_id=card.id, **q.model_dump()))
    trace.mode = "ebay-sold"
    trace.summary = {"returned": len(items), "after_top_up": sum(len(v) for v in groups.values()), "mode": "sold",
                     "junk": sum(1 for r in have.values() if r.junk), "unknown_grade": 0, "best_offer": best_offers,
                     "window_days": s.sold_window_days, "new_on_record": new_rows,
                     "grades_found": {g: len(v) for g, v in groups.items()}}
    trace.note(f"{new_rows} of the {len(items)} sales returned were new to the record; {len(items) - new_rows} were already stored.")
    if best_offers:
        trace.note(f"{best_offers} of these were Best Offer sales. For those the price shown is the asking price, "
                   "so the real sale price was probably lower.")
    if not items:
        trace.note("The provider found no sold listings for these words.")
    session.commit()
    save_trace(session, trace, card.id)
    mine = next((q for q in quotes if q.grade == own), None)
    return {"found": len(items), "new": new_rows, "used": mine.sample_size if mine else 0, "grade": own, "sold_value": card.sold_value,
            "median": mine.median if mine else None, "best_offers": best_offers,
            "on_record": len(have), "budget_left": budget_left(session, "manual")}


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
