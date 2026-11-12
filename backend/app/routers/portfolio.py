"""Portfolio dashboard: what the collection is worth, what it cost, and how the value has moved."""
from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, col, select

from ..db import get_session
from ..models import ASKING_SOURCES, Card, CardStatus, Location, PriceSnapshot, utcnow
from ..services.grading import normalize_grade

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

RANGE_DAYS = {"7d": 7, "1m": 30, "3m": 90, "1y": 365, "all": None}


def _card_history(session: Session, cards: list[Card]) -> dict[int, list[tuple[date, float]]]:
    """Per card: the asking median over time for the card's own grade and version, oldest first."""
    by_id = {c.id: c for c in cards}
    rows = session.exec(select(PriceSnapshot).where(
        col(PriceSnapshot.source).in_(ASKING_SOURCES), col(PriceSnapshot.median).is_not(None))
        .order_by(col(PriceSnapshot.created_at))).all()
    out: dict[int, list[tuple[date, float]]] = defaultdict(list)
    for s in rows:
        c = by_id.get(s.card_id)
        if c and s.grade == normalize_grade(c.grade) and s.parallel == c.parallel:
            out[c.id].append((s.created_at.date(), s.median))
    return out


def _value_at(points: list[tuple[date, float]], day: date) -> float | None:
    """Latest price on or before `day`; None if the card hadn't been priced yet."""
    val = None
    for d, v in points:
        if d > day:
            break
        val = v
    return val


@router.get("")
def portfolio(span: str = Query("1m", alias="range", pattern="^(7d|1m|3m|1y|all)$"), session: Session = Depends(get_session)):
    cards = session.exec(select(Card).where(col(Card.status) != CardStatus.sold)).all()
    hist = _card_history(session, cards)
    today = utcnow().date()

    value = sum((c.est_value or 0) * c.quantity for c in cards)
    costed = [c for c in cards if c.cost is not None]
    cost = sum(c.cost * c.quantity for c in costed)
    costed_value = sum((c.est_value or 0) * c.quantity for c in costed)

    locs = {l.id: l.name for l in session.exec(select(Location)).all()}
    by_loc: dict[str, dict] = defaultdict(lambda: {"cards": 0, "value": 0.0})
    by_cat: dict[str, dict] = defaultdict(lambda: {"cards": 0, "value": 0.0})
    for c in cards:
        for bucket, key in ((by_loc, locs.get(c.location_id, "Unassigned")), (by_cat, c.category.value)):
            bucket[key]["cards"] += c.quantity
            bucket[key]["value"] += (c.est_value or 0) * c.quantity

    # Value over time: each card counts from the day it was first priced, at its latest price on each day.
    first = min((pts[0][0] for pts in hist.values()), default=today)
    days = RANGE_DAYS[span]
    start = max(first, today - timedelta(days=days)) if days else first
    n = (today - start).days
    step = max(1, n // 120)  # at most ~120 points
    grid = [start + timedelta(days=i) for i in range(0, n + 1, step)]
    if grid[-1] != today:
        grid.append(today)
    totals = [0.0] * len(grid)
    for c in cards:
        pts, i, val = hist.get(c.id, []), 0, None
        for gi, d in enumerate(grid):  # one pass per card: advance through its prices as the days advance
            while i < len(pts) and pts[i][0] <= d:
                val, i = pts[i][1], i + 1
            if val is not None:
                totals[gi] += val * c.quantity
    series = [{"date": d.isoformat(), "value": round(t, 2)} for d, t in zip(grid, totals)]
    change = (series[-1]["value"] - series[0]["value"]) if len(series) > 1 else 0.0

    # Movers: compare today's price with the one a week ago (cards priced for the first time don't count).
    week_ago = today - timedelta(days=7)
    movers = []
    for c in cards:
        pts = hist.get(c.id, [])
        before, now = _value_at(pts, week_ago), _value_at(pts, today)
        if before and now is not None and before != now:
            movers.append({"id": c.id, "name": c.name, "grade": normalize_grade(c.grade), "before": before, "now": now,
                           "change": round((now - before) * c.quantity, 2), "pct": round((now - before) / before * 100, 1)})
    movers.sort(key=lambda m: m["change"])
    top = sorted(cards, key=lambda c: (c.est_value or 0) * c.quantity, reverse=True)[:8]

    with_sold = [c for c in cards if c.sold_value is not None]
    return {
        "totals": {"sold_value": round(sum(c.sold_value * c.quantity for c in with_sold), 2), "sold_cards": len(with_sold),
                   "asking_value_of_sold_cards": round(sum((c.est_value or 0) * c.quantity for c in with_sold), 2),
                   "cards": sum(c.quantity for c in cards), "distinct": len(cards), "value": round(value, 2),
                   "unpriced": sum(1 for c in cards if c.est_value is None),
                   "cost": round(cost, 2), "costed_cards": len(costed), "uncosted_cards": len(cards) - len(costed),
                   "gain": round(costed_value - cost, 2) if costed else None,
                   "gain_pct": round((costed_value - cost) / cost * 100, 1) if cost else None},
        "series": series, "change": round(change, 2), "range": span,
        "by_location": sorted(({"name": k, **v, "value": round(v["value"], 2)} for k, v in by_loc.items()), key=lambda r: -r["value"]),
        "by_category": sorted(({"name": k, **v, "value": round(v["value"], 2)} for k, v in by_cat.items()), key=lambda r: -r["value"]),
        "top_cards": [{"id": c.id, "name": c.name, "grade": normalize_grade(c.grade), "quantity": c.quantity,
                       "est_value": c.est_value, "image_url": f"/images/{c.image_path}" if c.image_path else None} for c in top],
        "gainers": [m for m in reversed(movers) if m["change"] > 0][:5],
        "losers": [m for m in movers if m["change"] < 0][:5],
    }
