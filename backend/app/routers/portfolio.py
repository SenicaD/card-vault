"""Portfolio dashboard: what the collection is worth, what it cost, and how the value has moved.

A card's value is what it sold for (median of its recent sales) when it has enough sales, and its
asking-price estimate otherwise. The history uses the same rule for every date: the sales in the 90 days
up to that date, else the asking price at that time.
"""
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, col, select

from ..config import get_settings
from ..db import get_session
from ..models import ASKING_SOURCES, Card, CardStatus, Location, PriceSnapshot, SoldSale, utcnow
from ..services.ebay import EbayItem
from ..services.grading import normalize_grade
from ..services.parallels import parse_parallel
from ..services.pricing import summarize
from .common import card_value

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


def _sales_history(session: Session, cards: list[Card]) -> dict[int, tuple[list[date], list[float]]]:
    """Per card: its sales (own grade and version, lots left out) as parallel lists sorted by date."""
    by_id = {c.id: c for c in cards}
    rows = session.exec(select(SoldSale).where(SoldSale.junk == False, col(SoldSale.ended_at).is_not(None))  # noqa: E712
                        .order_by(col(SoldSale.ended_at))).all()
    tmp: dict[int, list[tuple[date, float]]] = defaultdict(list)
    for r in rows:
        c = by_id.get(r.card_id)
        if c and r.grade == normalize_grade(c.grade) and (not c.parallel or parse_parallel(r.title) == c.parallel):
            tmp[c.id].append((r.ended_at.date(), r.price))
    return {cid: ([d for d, _ in v], [p for _, p in v]) for cid, v in tmp.items()}


def _asking_at(points: list[tuple[date, float]], day: date) -> float | None:
    """Latest asking price on or before `day`; None if the card hadn't been priced yet."""
    val = None
    for d, v in points:
        if d > day:
            break
        val = v
    return val


def _sold_at(sales: tuple[list[date], list[float]] | None, day: date, window: int, minimum: int) -> float | None:
    """The sold value as it stood on `day`: median of the sales in the window before it, by pricing.summarize
    (the same rule as the card's sold value: outliers trimmed from 8 sales up), or None when too few remain."""
    if not sales:
        return None
    dates, prices = sales
    chunk = prices[bisect_right(dates, day - timedelta(days=window)):bisect_right(dates, day)]
    stats = summarize([EbayItem(item_id=str(i), title="", price=p) for i, p in enumerate(chunk)])
    return stats["median"] if stats["sample_size"] >= minimum else None


@router.get("")
def portfolio(span: str = Query("1m", alias="range", pattern="^(7d|1m|3m|1y|all)$"), session: Session = Depends(get_session)):
    cards = session.exec(select(Card).where(col(Card.status) != CardStatus.sold)).all()
    s = get_settings()
    hist = _card_history(session, cards)
    sales = _sales_history(session, cards)
    today = utcnow().date()
    val = {c.id: card_value(c) for c in cards}  # (value, basis) per card

    def worth(c: Card) -> float:
        return (val[c.id][0] or 0) * c.quantity

    value = sum(worth(c) for c in cards)
    costed = [c for c in cards if c.cost is not None]
    cost = sum(c.cost * c.quantity for c in costed)
    costed_value = sum(worth(c) for c in costed)

    locs = {l.id: l.name for l in session.exec(select(Location)).all()}
    by_loc: dict[str, dict] = defaultdict(lambda: {"cards": 0, "value": 0.0})
    by_cat: dict[str, dict] = defaultdict(lambda: {"cards": 0, "value": 0.0})
    for c in cards:
        for bucket, key in ((by_loc, locs.get(c.location_id, "Unassigned")), (by_cat, c.category.value)):
            bucket[key]["cards"] += c.quantity
            bucket[key]["value"] += worth(c)

    def value_at(c: Card, d: date) -> float | None:
        """The card's value on a past day: your own value if set, else sold if the sales then supported it, else the asking price then."""
        if c.manual_value is not None:
            return c.manual_value
        v = _sold_at(sales.get(c.id), d, s.sold_window_days, s.sold_min_sales)
        return v if v is not None else _asking_at(hist.get(c.id, []), d)

    # Value over time. A card counts from the first day it had a price of either kind.
    firsts = [pts[0][0] for pts in hist.values()] + [dd[0] for dd, _ in sales.values()]
    first = min(firsts, default=today)
    days = RANGE_DAYS[span]
    start = max(first, today - timedelta(days=days)) if days else first
    n = (today - start).days
    step = max(1, n // 60)  # at most ~60 points: each one is worked out per card
    grid = [start + timedelta(days=i) for i in range(0, n + 1, step)]
    if grid[-1] != today:
        grid.append(today)
    totals = [0.0] * len(grid)
    for c in cards:
        for gi, d in enumerate(grid):
            v = value_at(c, d)
            if v is not None:
                totals[gi] += v * c.quantity
    totals[-1] = value  # the chart ends on today's headline number
    series = [{"date": d.isoformat(), "value": round(t, 2)} for d, t in zip(grid, totals)]
    change = (series[-1]["value"] - series[0]["value"]) if len(series) > 1 else 0.0

    # Movers: today's value against a week ago (cards first priced since then don't count).
    week_ago = today - timedelta(days=7)
    movers = []
    for c in cards:
        before, now = value_at(c, week_ago), val[c.id][0]
        if before and now is not None and before != now:
            movers.append({"id": c.id, "name": c.name, "grade": normalize_grade(c.grade), "before": before, "now": now,
                           "change": round((now - before) * c.quantity, 2), "pct": round((now - before) / before * 100, 1)})
    movers.sort(key=lambda m: m["change"])
    top = sorted(cards, key=worth, reverse=True)[:8]
    with_sold = [c for c in cards if val[c.id][1] == "sold"]
    manual = [c for c in cards if val[c.id][1] == "manual"]

    return {
        "totals": {"cards": sum(c.quantity for c in cards), "distinct": len(cards), "value": round(value, 2),
                   "asking_value": round(sum((c.est_value or 0) * c.quantity for c in cards), 2),
                   "sold_value": round(sum(c.sold_value * c.quantity for c in with_sold), 2), "sold_cards": len(with_sold),
                   "asking_value_of_sold_cards": round(sum((c.est_value or 0) * c.quantity for c in with_sold), 2),
                   "manual_value": round(sum(c.manual_value * c.quantity for c in manual), 2), "manual_cards": len(manual),
                   "asking_cards": sum(1 for c in cards if val[c.id][1] == "asking"),
                   "unpriced": sum(1 for c in cards if val[c.id][0] is None),
                   "cost": round(cost, 2), "costed_cards": len(costed), "uncosted_cards": len(cards) - len(costed),
                   "gain": round(costed_value - cost, 2) if costed else None,
                   "gain_pct": round((costed_value - cost) / cost * 100, 1) if cost else None},
        "series": series, "change": round(change, 2), "range": span,
        "by_location": sorted(({"name": k, **v, "value": round(v["value"], 2)} for k, v in by_loc.items()), key=lambda r: -r["value"]),
        "by_category": sorted(({"name": k, **v, "value": round(v["value"], 2)} for k, v in by_cat.items()), key=lambda r: -r["value"]),
        "top_cards": [{"id": c.id, "name": c.name, "grade": normalize_grade(c.grade), "quantity": c.quantity,
                       "value": val[c.id][0], "basis": val[c.id][1],
                       "image_url": f"/images/{c.image_path}" if c.image_path else None} for c in top],
        "gainers": [m for m in reversed(movers) if m["change"] > 0][:5],
        "losers": [m for m in movers if m["change"] < 0][:5],
    }
