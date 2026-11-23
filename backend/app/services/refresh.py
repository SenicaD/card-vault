"""Daily re-pricing of cards still in the collection, so the asking-price trend fills in on its own.

Each card is re-priced at most once per `price_refresh_hours`, judged from its newest asking-price
snapshot, so restarting the container never re-prices everything. Failures are logged and skipped.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlmodel import Session, col, select

from ..config import get_settings
from ..db import engine
from ..models import ASKING_SOURCES, Card, JobRun, ListingSeen, CardStatus, PriceSnapshot, utcnow
from .grading import grade_sort_key, normalize_grade
from .identity import identity_of_card, who_of_card
from .app_settings import load_app_settings
from .pricing import apply_asking, asking_listings, card_image, quote_grades
from .trace import Trace, save_trace
from .tracker import track_listings

log = logging.getLogger("cardvault.refresh")

CHECK_EVERY_SECONDS = 3600  # how often to look for cards that are due
PAUSE_BETWEEN_CARDS = 1.0  # be polite to the eBay API
BULK_WORKERS = 4  # cards a hand-started bulk re-price works on at once
MAX_TRACKED_GRADES = 6
LOOKUP_ON_ADD = True  # look a new card up on eBay as soon as it's added (tests switch this off)


def _plan(session: Session, c: Card) -> tuple:
    """What to search for this card: (id, query, own grade, grades to price, photo, version, who, identity)."""
    rows = session.exec(select(PriceSnapshot.grade).where(
        PriceSnapshot.card_id == c.id, col(PriceSnapshot.source).in_(ASKING_SOURCES),
        col(PriceSnapshot.grade).is_not(None))).all()
    own = normalize_grade(c.grade)
    others = sorted(set(rows) - {own}, key=grade_sort_key)
    return (c.id, c.search_query or c.name, own, ([own] + others)[:MAX_TRACKED_GRADES], card_image(c), c.parallel, who_of_card(c),
            identity_of_card(c))


async def reprice_card(card_id: int, query: str, own: str, tracked: list[str], image, parallel, who=None, ident=None,
                       trigger: str = "daily") -> bool:
    """Price one card on eBay and record it. True if any grade got a price. Raises if the search fails."""
    with Session(engine) as session:
        skip = set(session.exec(select(ListingSeen.item_id).where(
            ListingSeen.card_id == card_id, ListingSeen.excluded == True)).all())  # noqa: E712
    trace = Trace("lookup", trigger, query, tracked, parallel)
    try:
        priced = await quote_grades(query, tracked, image, parallel=parallel, exclude=skip, trace=trace, who=who,
                                    ident=ident)
    except Exception as e:
        trace.error = repr(e)[:400]
        try:
            with Session(engine) as session:
                save_trace(session, trace, card_id)
        except Exception:
            log.exception("could not save the trace for card %s", card_id)
        raise
    quotes, counts, groups = priced.quotes, priced.counts, priced.groups
    own_empty = any(q.grade == own and q.median is None for q in quotes)  # the search ran but nothing matched
    quotes = [q for q in quotes if q.median is not None]  # don't record empty points
    ok = False
    with Session(engine) as session:
        card = session.get(Card, card_id)
        if card and card.status != CardStatus.sold:
            card.grade_counts, card.grade_counts_at = counts, utcnow()
            app_settings = load_app_settings(session)
            prices = [i.price for i in asking_listings(groups.get(own, []), parallel, skip, ident)]
            for q in quotes:
                session.add(PriceSnapshot(card_id=card_id, **q.model_dump()))
                if q.grade == own:  # the estimate follows only the card's own grade
                    apply_asking(card, q.median, q.spread, prices, app_settings)
                    card.priced_at = utcnow()
            if own_empty and not any(q.grade == own for q in quotes):
                card.est_value, card.wide_spread, card.spread_rule, card.priced_at = None, False, None, utcnow()  # no matching listings: don't keep an old, wrong number
            session.add(card)
            session.commit()
            ok = bool(quotes)
            try:
                save_trace(session, trace, card_id)
            except Exception:  # a debugging aid must never break pricing
                log.exception("could not save the trace for card %s", card_id)
                session.rollback()
            try:  # after the commit, so a failure here can never lose the prices
                await track_listings(session, card_id, groups, forget=priced.not_alike)
            except Exception:
                log.exception("listing tracking failed for card %s", card_id)
                session.rollback()
    return ok


async def refresh_due(max_age: timedelta | None = None, pause: float = PAUSE_BETWEEN_CARDS) -> tuple[int, int]:
    """Re-price every unsold card whose last graded asking snapshot is older than max_age.

    Each card is priced for its own grade plus every grade previously looked up for it. That is one
    eBay search per card however many grades it tracks. Returns (cards refreshed, cards failed).
    """
    if max_age is None:
        max_age = timedelta(hours=get_settings().price_refresh_hours)
    cutoff = utcnow() - max_age
    refreshed = failed = 0
    started = utcnow()
    errors: list[dict] = []

    with Session(engine) as session:
        cards = session.exec(select(Card).where(col(Card.status) != CardStatus.sold)).all()
        due: list[tuple] = []
        for c in cards:
            rows = session.exec(select(PriceSnapshot.created_at).where(
                PriceSnapshot.card_id == c.id, col(PriceSnapshot.source).in_(ASKING_SOURCES),
                col(PriceSnapshot.grade).is_not(None))).all()
            # A search that found nothing still counts as a check, or a card with no listings
            # for its grade would be searched again every hour.
            last = max(list(rows) + ([c.grade_counts_at] if c.grade_counts_at else []), default=None)
            if last is None or last < cutoff:
                due.append(_plan(session, c))

    for plan in due:
        try:
            if await reprice_card(*plan):
                refreshed += 1
        except Exception as e:  # keep going: one bad query shouldn't stop the round
            failed += 1
            errors.append({"card_id": plan[0], "query": plan[1], "error": repr(e)[:300]})
            log.warning("re-price failed for card %s (%r): %s", plan[0], plan[1], e)
        await asyncio.sleep(pause)

    if due:
        log.info("re-priced %d of %d due cards (%d failed)", refreshed, len(due), failed)
        record_run("daily", started, len(due), refreshed, failed, errors)
    return refreshed, failed


_add_lock: asyncio.Lock | None = None
_add_lock_loop: asyncio.AbstractEventLoop | None = None


def _lock_for_adds() -> asyncio.Lock:
    """One lock shared by every add lookup, made for the running loop (tests start a new loop per client)."""
    global _add_lock, _add_lock_loop
    loop = asyncio.get_running_loop()
    if _add_lock is None or _add_lock_loop is not loop:
        _add_lock, _add_lock_loop = asyncio.Lock(), loop
    return _add_lock


async def price_new_card(card_id: int) -> None:
    """Look a just-added card up on eBay, one card at a time with a pause. Failures are logged, never raised."""
    if not LOOKUP_ON_ADD:
        return
    async with _lock_for_adds():
        query = None
        try:
            with Session(engine) as session:
                card = session.get(Card, card_id)
                if not card or card.status == CardStatus.sold:  # deleted or sold since: nothing to look up
                    return
                plan = _plan(session, card)
            query = plan[1]
            await reprice_card(*plan, trigger="add")
        except Exception as e:  # runs after the response: a failure must never escape
            log.warning("lookup on add failed for card %s (%r): %s", card_id, query, e)
        await asyncio.sleep(PAUSE_BETWEEN_CARDS)


def record_run(kind: str, started, total: int, refreshed: int, failed: int, errors: list[dict]) -> None:
    """Keep a line for the Debug page's job log (the newest 100 runs)."""
    try:
        with Session(engine) as session:
            session.add(JobRun(kind=kind, started_at=started, finished_at=utcnow(), total=total, refreshed=refreshed,
                               failed=failed, errors=errors[:50]))
            session.commit()
            old = session.exec(select(JobRun).order_by(col(JobRun.id).desc()).offset(100)).all()
            for r in old:
                session.delete(r)
            session.commit()
    except Exception:
        log.exception("could not record the job run")


async def refresh_loop() -> None:
    while True:
        try:
            await refresh_due()
        except Exception:  # never let the background task die
            log.exception("price refresh round failed")
        await asyncio.sleep(CHECK_EVERY_SECONDS)


# ---------- Bulk re-price you start by hand, with progress and a before/after summary ----------

@dataclass
class RepriceJob:
    total: int
    state: str = "running"  # running | done
    done: int = 0
    failed: int = 0
    started_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None
    changes: list[dict] = field(default_factory=list)  # [{id, name, before, after}]
    errors: list[dict] = field(default_factory=list)

    def snapshot(self) -> dict:
        moved = [c for c in self.changes if c["before"] != c["after"]]
        # Biggest swings (in dollars, per card) first
        moved.sort(key=lambda c: abs((c["after"] or 0) - (c["before"] or 0)), reverse=True)
        return {"state": self.state, "total": self.total, "done": self.done, "failed": self.failed,
                "started_at": self.started_at, "finished_at": self.finished_at,
                "changed": len(moved), "unchanged": len(self.changes) - len(moved), "top_changes": moved[:10]}


_job: RepriceJob | None = None
_task: asyncio.Task | None = None  # keeps a reference so the task isn't garbage-collected mid-run


def current_job() -> RepriceJob | None:
    return _job


def start_job(ids: list[int] | None, pause: float | None = None) -> RepriceJob | None:
    """Begin re-pricing the given cards (None = every unsold card). None is returned if one is already running."""
    global _job, _task
    pause = PAUSE_BETWEEN_CARDS if pause is None else pause
    if _job and _job.state == "running":
        return None
    with Session(engine) as session:
        stmt = select(Card).where(col(Card.status) != CardStatus.sold)
        if ids is not None:
            stmt = stmt.where(col(Card.id).in_(ids))
        plans = [(_plan(session, c), c.name, c.est_value) for c in session.exec(stmt).all()]
    _job = RepriceJob(total=len(plans))
    if not plans:
        _job.state, _job.finished_at = "done", utcnow()
        return _job
    _task = asyncio.create_task(_run_job(_job, plans, pause))
    return _job


async def _run_job(job: RepriceJob, plans: list[tuple], pause: float) -> None:
    queue = list(reversed(plans))

    async def worker() -> None:
        while queue:
            plan, name, before = queue.pop()
            try:
                await reprice_card(*plan, trigger="bulk")
                with Session(engine) as session:
                    card = session.get(Card, plan[0])
                    job.changes.append({"id": plan[0], "name": name, "before": before,
                                        "after": card.est_value if card else before})
            except Exception as e:
                job.failed += 1
                job.errors.append({"card_id": plan[0], "query": plan[1], "name": name, "error": repr(e)[:300]})
                log.warning("bulk re-price failed for card %s (%r): %s", plan[0], plan[1], e)
            job.done += 1
            await asyncio.sleep(pause)

    try:
        await asyncio.gather(*(worker() for _ in range(BULK_WORKERS)))
    finally:
        job.state, job.finished_at = "done", utcnow()
        record_run("bulk", job.started_at, job.total, job.done - job.failed, job.failed, job.errors)
