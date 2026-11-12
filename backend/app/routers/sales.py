from datetime import datetime, timedelta
from statistics import median

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from ..db import get_session
from ..models import CardStatus, ListingSeen, Sale, utcnow
from ..services.grading import grade_sort_key, normalize_grade
from ..services.parallels import parse_parallel
from .common import get_card_or_404

router = APIRouter(prefix="/api/cards/{card_id}", tags=["sales"])


class SaleCreate(BaseModel):
    price: float = Field(gt=0)
    sold_at: datetime | None = None  # defaults to now
    note: str | None = Field(default=None, max_length=500)


@router.post("/sales", response_model=Sale, status_code=201)
def record_sale(card_id: int, body: SaleCreate, session: Session = Depends(get_session)):
    """Record what you actually sold the card for, and mark it sold."""
    card = get_card_or_404(session, card_id)
    sale = Sale(card_id=card.id, price=round(body.price, 2), note=body.note,
                grade=normalize_grade(card.grade), sold_at=body.sold_at or utcnow())
    session.add(sale)
    card.status = CardStatus.sold
    card.updated_at = utcnow()
    session.add(card)
    session.commit()
    session.refresh(sale)
    return sale


@router.get("/sales", response_model=list[Sale])
def list_sales(card_id: int, session: Session = Depends(get_session)):
    get_card_or_404(session, card_id)
    return session.exec(select(Sale).where(Sale.card_id == card_id).order_by(col(Sale.sold_at).desc())).all()


@router.delete("/sales/{sale_id}", status_code=204)
def delete_sale(card_id: int, sale_id: int, session: Session = Depends(get_session)):
    sale = session.get(Sale, sale_id)
    if not sale or sale.card_id != card_id:
        raise HTTPException(404, "Sale not found")
    session.delete(sale)
    session.commit()


@router.get("/gone", response_model=list[ListingSeen])
def gone_listings(card_id: int, limit: int = 20, session: Session = Depends(get_session)):
    """Listings that disappeared from eBay, newest first. Their last asking price is our estimate of a sale."""
    get_card_or_404(session, card_id)
    return session.exec(select(ListingSeen).where(ListingSeen.card_id == card_id, ListingSeen.status == "gone")
                        .order_by(col(ListingSeen.gone_at).desc()).limit(min(max(limit, 1), 100))).all()


class GradeCompare(BaseModel):
    grade: str
    asking_median: float | None  # what matching listings on eBay are asking right now
    asking_count: int
    sold_median: float | None  # last asking price of listings that disappeared (likely sold)
    sold_count: int
    gap: float | None  # likely-sold minus asking, in dollars
    gap_pct: float | None
    days_median: float | None  # how long the disappeared listings were up before they went
    yours_median: float | None  # what you actually sold this card for (recorded by you)
    yours_count: int


class SoldRow(BaseModel):
    id: int
    item_id: str
    grade: str
    parallel: str
    title: str
    price: float
    currency: str
    url: str | None
    image_url: str | None
    first_seen: datetime
    gone_at: datetime | None
    days_listed: float | None
    verdict: str | None
    vs_asking_pct: float | None  # this listing's price against today's asking median for its grade


class SoldCompare(BaseModel):
    days: int
    source: str  # "estimated": eBay's public API doesn't return sold prices, so these are inferred
    grades: list[GradeCompare]
    rows: list[SoldRow]


def _med(values: list[float]) -> float | None:
    return round(median(values), 2) if values else None


@router.get("/sold-compare", response_model=SoldCompare)
def sold_compare(card_id: int, days: int = Query(90, ge=0, le=3650), grades: list[str] | None = Query(None),
                 all_versions: bool = False, session: Session = Depends(get_session)):
    """Asking prices now vs. what listings sold for (estimated from listings that disappeared), per grade.

    eBay's public API has no sold prices, so "sold" here is the last asking price of a listing that has
    vanished from eBay: usually a sale, but it can be a listing the seller ended or relisted. Your own
    recorded sales are shown next to it as the only confirmed figure. days=0 means all time.
    """
    card = get_card_or_404(session, card_id)
    cutoff = utcnow() - timedelta(days=days) if days else None
    rows = session.exec(select(ListingSeen).where(ListingSeen.card_id == card_id, ListingSeen.excluded == False)).all()  # noqa: E712
    if card.parallel and not all_versions:  # same rule the prices use: only this version of the card
        rows = [r for r in rows if parse_parallel(r.title) == card.parallel]
    active = [r for r in rows if r.status == "active"]
    gone = [r for r in rows if r.status == "gone" and r.gone_at and (cutoff is None or r.gone_at >= cutoff)]
    sales = [x for x in session.exec(select(Sale).where(Sale.card_id == card_id)).all()
             if cutoff is None or x.sold_at >= cutoff]

    present = {r.grade for r in active} | {r.grade for r in gone} | {x.grade for x in sales if x.grade}
    wanted = [normalize_grade(g) for g in grades] if grades else sorted(present | {normalize_grade(card.grade)}, key=grade_sort_key)
    wanted = list(dict.fromkeys(wanted))[:8]

    out, ask_by_grade = [], {}
    for g in wanted:
        ask = [r.price for r in active if r.grade == g]
        sold = [r for r in gone if r.grade == g]
        mine = [x.price for x in sales if x.grade == g]
        a, sm = _med(ask), _med([r.price for r in sold])
        ask_by_grade[g] = a
        spans = [(r.gone_at - r.first_seen).total_seconds() / 86400 for r in sold if r.gone_at and r.first_seen]
        dm = _med(spans)
        out.append(GradeCompare(
            grade=g, asking_median=a, asking_count=len(ask), sold_median=sm, sold_count=len(sold),
            gap=round(sm - a, 2) if a is not None and sm is not None else None,
            gap_pct=round((sm - a) / a * 100, 1) if a and sm is not None else None,
            days_median=round(dm, 1) if dm is not None else None,
            yours_median=_med(mine), yours_count=len(mine)))

    detail = [SoldRow(
        id=r.id, item_id=r.item_id, grade=r.grade, parallel=parse_parallel(r.title), title=r.title, price=r.price,
        currency=r.currency, url=r.url, image_url=r.image_url, first_seen=r.first_seen, gone_at=r.gone_at,
        days_listed=round(max(0.0, (r.gone_at - r.first_seen).total_seconds() / 86400), 1) if r.gone_at else None,
        verdict=r.verdict,
        vs_asking_pct=round((r.price - ask_by_grade[r.grade]) / ask_by_grade[r.grade] * 100, 1)
        if ask_by_grade.get(r.grade) else None) for r in gone if r.grade in wanted]
    detail.sort(key=lambda r: r.gone_at, reverse=True)
    return SoldCompare(days=days, source="estimated", grades=out, rows=detail[:200])
