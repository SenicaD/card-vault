import uuid
from datetime import timedelta
from statistics import median as _median

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from ..config import get_settings
from ..db import get_session
from ..models import Card, CardCategory, CardStatus, Listing, ListingSeen, PriceSnapshot, Sale, utcnow
from ..services.ebay import EbayItem, ListingRequest, get_ebay
from ..services.grading import grade_sort_key, normalize_grade
from ..services.pricing import quote_grades
from ..services.tracker import track_listings
from .common import get_card_or_404

router = APIRouter(prefix="/api/cards/{card_id}", tags=["ebay"])

# eBay leaf categories for single cards (US marketplace)
_CATEGORY_IDS = {CardCategory.sports: "261328"}
_CCG_SINGLES = "183454"


class PriceResult(BaseModel):
    snapshot: PriceSnapshot  # the card's own grade if it was priced, otherwise the first grade asked for
    snapshots: list[PriceSnapshot]  # one per grade asked for
    comps: list[EbayItem]  # cheapest listings across the grades asked for
    grade_counts: dict[str, int]  # listings eBay showed per grade, including grades not asked for


class PriceRequest(BaseModel):
    query: str | None = None  # override the card's stored search query
    grades: list[str] | None = None  # grades to price; default is the card's own grade (Raw if none)


@router.post("/price", response_model=PriceResult)
async def price_card(card_id: int, body: PriceRequest | None = None,
                     session: Session = Depends(get_session)):
    card = get_card_or_404(session, card_id)
    query = (body and body.query) or card.search_query or card.name
    own = normalize_grade(card.grade)
    asked = body.grades if body and body.grades else [own]
    grades = list(dict.fromkeys(normalize_grade(g) for g in asked))[:8]
    try:
        quotes, counts, comps, groups = await quote_grades(query, grades)
    except Exception as e:
        raise HTTPException(502, f"eBay search failed: {e}") from e

    snaps = [PriceSnapshot(card_id=card.id, **q.model_dump()) for q in quotes]
    session.add_all(snaps)
    primary = next((s for s in snaps if s.grade == own), snaps[0])
    card.search_query = query
    card.grade_counts, card.grade_counts_at = counts, utcnow()
    # The estimate only ever follows the card's own grade, so comparing other grades can't change it.
    own_snap = next((s for s in snaps if s.grade == own), None)
    if own_snap and own_snap.median is not None:
        card.est_value = own_snap.median
        card.priced_at = utcnow()
    card.updated_at = utcnow()
    session.add(card)
    session.commit()
    try:  # after the commit, so a failure here can never lose the price lookup
        await track_listings(session, card.id, groups)
    except Exception:
        session.rollback()
    for s in snaps:  # tracking commits too, which expires these; reload them before they are serialised
        session.refresh(s)
    return PriceResult(snapshot=primary, snapshots=snaps, comps=comps, grade_counts=counts)


@router.get("/prices", response_model=list[PriceSnapshot])
def price_history(card_id: int, session: Session = Depends(get_session)):
    get_card_or_404(session, card_id)
    return session.exec(select(PriceSnapshot).where(PriceSnapshot.card_id == card_id)
                        .order_by(col(PriceSnapshot.created_at).desc())).all()


_RANGE_DAYS = {"7d": 7, "1m": 30, "3m": 90, "1y": 365, "all": None}


class TrendPoint(BaseModel):
    date: str  # YYYY-MM-DD (UTC)
    median: float
    low: float | None
    high: float | None
    samples: int


class SalePoint(BaseModel):
    id: int
    date: str
    price: float
    grade: str | None


class TrendSeries(BaseModel):
    grade: str
    points: list[TrendPoint]


class Trend(BaseModel):
    range: str
    grades: list[str]  # grades plotted, in order (at most MAX_TREND_GRADES)
    asking: list[TrendSeries]
    sold: list[TrendSeries]
    # Where `sold` comes from: "sold" = real sold prices (eBay Marketplace Insights, if we ever get it),
    # "estimated" = last asking prices of listings that disappeared, None = nothing yet.
    sold_source: str | None
    sales: list[SalePoint]  # sales you recorded yourself, for the plotted grades
    first_recorded: str | None  # date of the earliest asking snapshot, for "history starts on ..."
    grades_available: list[str]  # grades this card has recorded asking prices for


MAX_TREND_GRADES = 4


def _daily(snaps: list[PriceSnapshot]) -> list[TrendPoint]:
    """One point per UTC day: the median of that day's snapshot medians."""
    by_day: dict[str, list[PriceSnapshot]] = {}
    for s in snaps:
        if s.median is not None:
            by_day.setdefault(s.created_at.date().isoformat(), []).append(s)
    return [TrendPoint(date=d, median=round(_median(x.median for x in v), 2),
                       low=min((x.low for x in v if x.low is not None), default=None),
                       high=max((x.high for x in v if x.high is not None), default=None),
                       samples=sum(x.sample_size for x in v))
            for d, v in sorted(by_day.items())]


def _daily_gone(rows: list[ListingSeen]) -> list[TrendPoint]:
    """One point per UTC day: the median last-asking price of the listings that disappeared that day."""
    by_day: dict[str, list[ListingSeen]] = {}
    for r in rows:
        by_day.setdefault(r.gone_at.date().isoformat(), []).append(r)
    return [TrendPoint(date=d, median=round(_median(x.price for x in v), 2), low=min(x.price for x in v),
                       high=max(x.price for x in v), samples=len(v))
            for d, v in sorted(by_day.items())]


@router.get("/trend", response_model=Trend)
def price_trend(card_id: int, range: str = Query("1m", pattern="^(7d|1m|3m|1y|all)$"),
                grades: list[str] | None = Query(None), session: Session = Depends(get_session)):
    """Asking vs sold price over time for one or more grades, from the snapshots saved for this card."""
    card = get_card_or_404(session, card_id)
    wanted = list(dict.fromkeys(normalize_grade(g) for g in (grades or [card.grade])))[:MAX_TREND_GRADES]
    snaps = session.exec(select(PriceSnapshot).where(PriceSnapshot.card_id == card_id)
                         .order_by(col(PriceSnapshot.created_at))).all()
    asking_all = [s for s in snaps if s.source == "ebay-active" and s.grade]
    sold_all = [s for s in snaps if s.source == "ebay-sold" and s.grade]
    days = _RANGE_DAYS[range]
    cutoff = utcnow() - timedelta(days=days) if days else None
    in_range = lambda d: cutoff is None or d >= cutoff  # noqa: E731

    def series(rows: list[PriceSnapshot]) -> list[TrendSeries]:
        return [TrendSeries(grade=g, points=_daily([s for s in rows if s.grade == g and in_range(s.created_at)]))
                for g in wanted]

    # Sold line: real sold prices if we have any, otherwise the estimate from listings that disappeared.
    gone = session.exec(select(ListingSeen).where(ListingSeen.card_id == card_id, ListingSeen.status == "gone")).all()
    if sold_all:
        sold_source, sold = "sold", series(sold_all)
    elif gone:
        sold_source = "estimated"
        sold = [TrendSeries(grade=g, points=_daily_gone([r for r in gone if r.grade == g and r.gone_at and in_range(r.gone_at)]))
                for g in wanted]
    else:
        sold_source, sold = None, series([])

    sales = [SalePoint(id=x.id, date=x.sold_at.date().isoformat(), price=x.price, grade=x.grade)
             for x in session.exec(select(Sale).where(Sale.card_id == card_id).order_by(col(Sale.sold_at))).all()
             if in_range(x.sold_at) and (x.grade is None or x.grade in wanted)]

    return Trend(range=range, grades=wanted, asking=series(asking_all), sold=sold, sold_source=sold_source, sales=sales,
                 first_recorded=asking_all[0].created_at.date().isoformat() if asking_all else None,
                 grades_available=sorted({s.grade for s in asking_all}, key=grade_sort_key))


class ListingCreate(BaseModel):
    title: str | None = Field(default=None, max_length=80)
    price: float | None = Field(default=None, gt=0)
    description: str | None = None
    publish: bool = True


def default_title(card: Card) -> str:
    parts = [card.year, card.set_name, card.name,
             f"#{card.card_number.lstrip('#')}" if card.card_number else None, card.variant,
             card.grade if card.grade and card.grade.lower() != "raw" else None]
    return " ".join(p for p in parts if p)[:80]


@router.post("/listings", response_model=Listing, status_code=201)
async def create_listing(card_id: int, body: ListingCreate, session: Session = Depends(get_session)):
    card = get_card_or_404(session, card_id)
    if card.status == CardStatus.sold:
        raise HTTPException(409, "This card is marked sold")
    price = body.price or card.est_value
    if not price:
        raise HTTPException(422, "Set a price, or look up the card's value first")

    settings = get_settings()
    images = []
    if card.image_path and settings.public_base_url:
        images.append(f"{settings.public_base_url.rstrip('/')}/images/{card.image_path}")

    graded = bool(card.grade and card.grade.lower() != "raw")
    title = body.title or default_title(card)
    req = ListingRequest(
        sku=f"CV-{card.id}-{uuid.uuid4().hex[:6]}",
        title=title,
        description=body.description or f"{title}. {'Graded ' + card.grade if graded else 'Raw, condition ' + (card.condition or 'see photos')}.",
        price=round(price, 2),
        quantity=1,
        category_id=_CATEGORY_IDS.get(card.category, _CCG_SINGLES),
        graded=graded, grade=card.grade, condition=card.condition,
        image_urls=images,
    )
    try:
        result = await get_ebay().create_listing(req, publish=body.publish)
    except Exception as e:
        raise HTTPException(502, f"eBay listing failed: {e}") from e

    listing = Listing(card_id=card.id, sku=result.sku, ebay_offer_id=result.offer_id,
                      ebay_listing_id=result.listing_id, title=req.title, price=req.price,
                      status=result.status, url=result.url)
    session.add(listing)
    if result.status == "published":
        card.status = CardStatus.listed
        card.updated_at = utcnow()
        session.add(card)
    session.commit()
    session.refresh(listing)
    return listing


@router.get("/listings", response_model=list[Listing])
def list_listings(card_id: int, session: Session = Depends(get_session)):
    get_card_or_404(session, card_id)
    return session.exec(select(Listing).where(Listing.card_id == card_id)
                        .order_by(col(Listing.created_at).desc())).all()
