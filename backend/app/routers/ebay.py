import uuid
from datetime import timedelta
from statistics import median as _median

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from ..config import get_settings
from ..db import get_session
from ..models import Card, CardCategory, CardStatus, Listing, PriceSnapshot, utcnow
from ..services.ebay import EbayItem, ListingRequest, get_ebay
from ..services.pricing import quote
from .common import get_card_or_404

router = APIRouter(prefix="/api/cards/{card_id}", tags=["ebay"])

# eBay leaf categories for single cards (US marketplace)
_CATEGORY_IDS = {CardCategory.sports: "261328"}
_CCG_SINGLES = "183454"


class PriceResult(BaseModel):
    snapshot: PriceSnapshot
    comps: list[EbayItem]


class PriceRequest(BaseModel):
    query: str | None = None  # override the card's stored search query


@router.post("/price", response_model=PriceResult)
async def price_card(card_id: int, body: PriceRequest | None = None,
                     session: Session = Depends(get_session)):
    card = get_card_or_404(session, card_id)
    query = (body and body.query) or card.search_query or card.name
    try:
        q, comps = await quote(query)
    except Exception as e:
        raise HTTPException(502, f"eBay search failed: {e}") from e

    snap = PriceSnapshot(card_id=card.id, **q.model_dump())
    session.add(snap)
    card.search_query = query
    card.est_value = snap.median
    card.priced_at = utcnow()
    card.updated_at = utcnow()
    session.add(card)
    session.commit()
    session.refresh(snap)
    return PriceResult(snapshot=snap, comps=comps)


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


class Trend(BaseModel):
    range: str
    asking: list[TrendPoint]
    sold: list[TrendPoint]
    # Sold prices need eBay's restricted Marketplace Insights API; false until a sold snapshot exists.
    sold_available: bool
    first_recorded: str | None  # date of the earliest asking snapshot, for "history starts on ..."


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


@router.get("/trend", response_model=Trend)
def price_trend(card_id: int, range: str = Query("1m", pattern="^(7d|1m|3m|1y|all)$"),
                session: Session = Depends(get_session)):
    """Asking vs sold price over time, built from the price snapshots saved for this card."""
    get_card_or_404(session, card_id)
    snaps = session.exec(select(PriceSnapshot).where(PriceSnapshot.card_id == card_id)
                         .order_by(col(PriceSnapshot.created_at))).all()
    asking_all = [s for s in snaps if s.source == "ebay-active"]
    sold_all = [s for s in snaps if s.source == "ebay-sold"]
    days = _RANGE_DAYS[range]
    cutoff = utcnow() - timedelta(days=days) if days else None

    def within(rows: list[PriceSnapshot]) -> list[PriceSnapshot]:
        return [s for s in rows if cutoff is None or s.created_at >= cutoff]

    return Trend(range=range, asking=_daily(within(asking_all)), sold=_daily(within(sold_all)),
                 sold_available=bool(sold_all),
                 first_recorded=asking_all[0].created_at.date().isoformat() if asking_all else None)


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
