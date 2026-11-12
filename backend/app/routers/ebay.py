import uuid
from collections import Counter
from datetime import timedelta
from statistics import median as _median

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from ..config import get_settings
from ..db import get_session
from ..models import ASKING_SOURCES, Card, CardCategory, CardStatus, Listing, ListingSeen, PriceSnapshot, Sale, SoldSale, utcnow
from ..services.ebay import EbayItem, ListingRequest, get_ebay
from ..services.grading import grade_sort_key, normalize_grade
from ..services.parallels import BASE, parse_parallel
from ..services.pricing import build_quotes, card_image, quote_grades
from ..services.similarity import score_urls
from ..services.trace import Trace, save_trace
from ..services.tracker import track_listings
from .common import get_card_or_404

router = APIRouter(prefix="/api/cards/{card_id}", tags=["ebay"])

# eBay leaf categories for single cards (US marketplace)
_CATEGORY_IDS = {CardCategory.sports: "261328"}
_CCG_SINGLES = "183454"


class PriceResult(BaseModel):
    snapshot: PriceSnapshot  # the card's own grade if it was priced, otherwise the first grade asked for
    snapshots: list[PriceSnapshot]  # one per grade asked for
    comps: list[EbayItem]  # listings of the grades asked for, best match first
    grade_counts: dict[str, int]  # listings eBay showed per grade, including grades not asked for


class PriceRequest(BaseModel):
    query: str | None = None  # override the card's stored search query
    text_only: bool = False  # search by words instead of the card's photo
    grades: list[str] | None = None  # grades to price; default is the card's own grade (Raw if none)


def excluded_ids(session: Session, card_id: int) -> set[str]:
    return set(session.exec(select(ListingSeen.item_id).where(ListingSeen.card_id == card_id, ListingSeen.excluded == True)).all())  # noqa: E712


@router.post("/price", response_model=PriceResult)
async def price_card(card_id: int, body: PriceRequest | None = None,
                     session: Session = Depends(get_session)):
    card = get_card_or_404(session, card_id)
    query = (body and body.query) or card.search_query or card.name
    own = normalize_grade(card.grade)
    asked = body.grades if body and body.grades else [own]
    grades = list(dict.fromkeys(normalize_grade(g) for g in asked))[:8]
    trace = Trace("lookup", "manual", query, grades, card.parallel)
    try:
        priced = await quote_grades(query, grades, None if (body and body.text_only) else card_image(card),
                                    parallel=card.parallel, exclude=excluded_ids(session, card.id), trace=trace)
    except Exception as e:
        trace.error = repr(e)[:400]
        save_trace(session, trace, card.id, keep_raw=True)  # a failed lookup is the one you most want to see
        raise HTTPException(502, f"eBay search failed: {e}") from e
    quotes, counts, comps, groups = priced.quotes, priced.counts, priced.comps, priced.groups

    # A card whose first identification was poor takes its name and search words from what the photo matched.
    if priced.source == "ebay-image" and (card.id_confidence or 0.0) < 0.3:
        from ..services.identify.ebay_image import parse_matches
        ident = parse_matches(priced.items)
        if ident.name and ident.confidence >= 0.3 and ident.confidence > (card.id_confidence or 0.0):
            trace.note(f"The card was re-identified ({round((card.id_confidence or 0) * 100)}% before, {round(ident.confidence * 100)}% now): "
                       f"was \"{card.name}\", now \"{ident.name}\"; search words are now \"{ident.search_query}\".")
            card.name, card.set_name, card.year = ident.name, ident.set_name, ident.year
            card.card_number, card.variant, card.category = ident.card_number, ident.variant, ident.category
            card.id_confidence, query = ident.confidence, ident.search_query
            for q in quotes:
                q.query = query

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
    trace.query = query
    try:
        save_trace(session, trace, card.id, keep_raw=True)
    except Exception:  # a debugging aid must never break a price lookup
        session.rollback()
    try:  # after the commit, so a failure here can never lose the price lookup
        await track_listings(session, card.id, groups)
    except Exception:
        session.rollback()
    for s in snaps:  # tracking commits too, which expires these; reload them before they are serialised
        session.refresh(s)
    return PriceResult(snapshot=primary, snapshots=snaps, comps=comps, grade_counts=counts)


class MatchRow(BaseModel):
    item_id: str
    title: str
    price: float
    currency: str
    grade: str
    parallel: str
    condition: str | None
    url: str | None
    image_url: str | None
    excluded: bool


def _active_rows(session: Session, card_id: int, grades: list[str] | None = None) -> list[ListingSeen]:
    rows = session.exec(select(ListingSeen).where(ListingSeen.card_id == card_id, ListingSeen.status == "active")
                        .order_by(col(ListingSeen.id))).all()
    wanted = {normalize_grade(g) for g in grades} if grades else None
    return [r for r in rows if wanted is None or r.grade in wanted]


@router.get("/matches", response_model=list[MatchRow])
def matches(card_id: int, grades: list[str] | None = Query(None), session: Session = Depends(get_session)):
    """Every listing currently matched to this card (kept between visits), for the listings table."""
    get_card_or_404(session, card_id)
    return [MatchRow(item_id=r.item_id, title=r.title, price=r.price, currency=r.currency, grade=r.grade,
                     parallel=parse_parallel(r.title), condition=r.condition, url=r.url, image_url=r.image_url,
                     excluded=r.excluded) for r in _active_rows(session, card_id, grades)]


class ExcludeRequest(BaseModel):
    item_ids: list[str] = Field(min_length=1, max_length=200)
    excluded: bool = True  # False puts them back
    grades: list[str] | None = None  # grades to re-price; default is the card's own


@router.post("/matches/exclude", response_model=PriceResult)
def exclude_matches(card_id: int, body: ExcludeRequest, session: Session = Depends(get_session)):
    """Mark listings as "not my card" (or put them back) and re-price from the listings already saved.

    No new eBay search: the numbers are rebuilt from the matched listings, so this is instant and the
    choice also applies to later refreshes.
    """
    card = get_card_or_404(session, card_id)
    ids = set(body.item_ids)
    for r in session.exec(select(ListingSeen).where(ListingSeen.card_id == card_id)).all():
        if r.item_id in ids:
            r.excluded = body.excluded
            session.add(r)
    own = normalize_grade(card.grade)
    grades = list(dict.fromkeys(normalize_grade(g) for g in (body.grades or [own])))[:8]
    rows = _active_rows(session, card_id)
    groups: dict[str, list[EbayItem]] = {}
    for r in rows:
        groups.setdefault(r.grade, []).append(EbayItem(item_id=r.item_id, title=r.title, price=r.price, currency=r.currency,
                                                       condition=r.condition, url=r.url, image_url=r.image_url))
    quotes, _ = build_quotes(groups, grades, card.search_query or card.name, "ebay-image", card.parallel,
                             {r.item_id for r in rows if r.excluded})
    snaps = [PriceSnapshot(card_id=card.id, **q.model_dump()) for q in quotes]
    session.add_all(snaps)
    own_snap = next((s for s in snaps if s.grade == own), None)
    if own_snap and own_snap.median is not None:
        card.est_value, card.priced_at = own_snap.median, utcnow()
    card.updated_at = utcnow()
    session.add(card)
    session.commit()
    for s in snaps:
        session.refresh(s)
    return PriceResult(snapshot=next((s for s in snaps if s.grade == own), snaps[0]), snapshots=snaps, comps=[],
                       grade_counts=card.grade_counts or {})


class SimilarityRequest(BaseModel):
    grades: list[str] | None = None


class SimilarityResult(BaseModel):
    scores: dict[str, int]  # item id -> 0-100 colour similarity to your photo
    suggested_parallel: str | None  # the version the most look-alike listings are
    votes: int = 0  # how many of the closest listings are that version
    of: int = 0


@router.post("/matches/similarity", response_model=SimilarityResult)
async def rank_by_look(card_id: int, body: SimilarityRequest | None = None, session: Session = Depends(get_session)):
    """Compare each matched listing's picture with your photo by colour, and suggest which version you have."""
    card = get_card_or_404(session, card_id)
    photo = card_image(card)
    if not photo:
        raise HTTPException(422, "This card has no photo to compare")
    rows = [r for r in _active_rows(session, card_id, body.grades if body else None) if r.image_url][:40]
    scores_by_url = await score_urls(photo, [r.image_url for r in rows])
    scores = {r.item_id: scores_by_url[r.image_url] for r in rows if r.image_url in scores_by_url}
    top = sorted((r for r in rows if r.item_id in scores), key=lambda r: -scores[r.item_id])[:8]
    votes = Counter(parse_parallel(r.title) for r in top)
    votes.pop(BASE, None)
    suggested, n = votes.most_common(1)[0] if votes else (None, 0)
    if n < 3:
        suggested, n = None, 0
    return SimilarityResult(scores=scores, suggested_parallel=suggested, votes=n, of=len(top))


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


def _sales_points(rows: list[SoldSale], weekly: bool) -> list[TrendPoint]:
    """Sold prices by the day each sale happened (or by week, for long ranges): the median of that period."""
    buckets: dict[str, list[SoldSale]] = {}
    for r in rows:
        d = r.ended_at.date()
        key = (d - timedelta(days=d.weekday())).isoformat() if weekly else d.isoformat()
        buckets.setdefault(key, []).append(r)
    return [TrendPoint(date=d, median=round(_median(x.price for x in v), 2), low=min(x.price for x in v),
                       high=max(x.price for x in v), samples=len(v)) for d, v in sorted(buckets.items())]


@router.get("/trend", response_model=Trend)
def price_trend(card_id: int, range: str = Query("1m", pattern="^(7d|1m|3m|1y|all)$"),
                grades: list[str] | None = Query(None), session: Session = Depends(get_session)):
    """Asking vs sold price over time for one or more grades, from the snapshots saved for this card."""
    card = get_card_or_404(session, card_id)
    wanted = list(dict.fromkeys(normalize_grade(g) for g in (grades or [card.grade])))[:MAX_TREND_GRADES]
    snaps = session.exec(select(PriceSnapshot).where(PriceSnapshot.card_id == card_id)
                         .order_by(col(PriceSnapshot.created_at))).all()
    mine = lambda s: (s.parallel or None) == (card.parallel or None)  # noqa: E731  only this version's prices
    asking_all = [s for s in snaps if s.source in ASKING_SOURCES and s.grade and mine(s)]
    sold_all = [s for s in snaps if s.source == "ebay-sold" and s.grade and mine(s)]
    days = _RANGE_DAYS[range]
    cutoff = utcnow() - timedelta(days=days) if days else None
    in_range = lambda d: cutoff is None or d >= cutoff  # noqa: E731

    def series(rows: list[PriceSnapshot]) -> list[TrendSeries]:
        return [TrendSeries(grade=g, points=_daily([s for s in rows if s.grade == g and in_range(s.created_at)]))
                for g in wanted]

    # Sold line: real sold prices if we have any, otherwise the estimate from listings that disappeared.
    gone = session.exec(select(ListingSeen).where(ListingSeen.card_id == card_id, ListingSeen.status == "gone")).all()
    stored = [r for r in session.exec(select(SoldSale).where(SoldSale.card_id == card_id, SoldSale.junk == False)).all()  # noqa: E712
              if r.ended_at and in_range(r.ended_at) and (not card.parallel or parse_parallel(r.title) == card.parallel)]
    if stored:  # real sales, plotted on the day they happened: every check adds to this record
        sold_source = "sold"
        weekly = days is None or days > 31
        sold = [TrendSeries(grade=g, points=_sales_points([r for r in stored if r.grade == g], weekly)) for g in wanted]
    elif sold_all:
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



class PricePoints(BaseModel):
    grade: str
    range: str
    sales: list[dict]  # every sold sale on record: {date, price, best_offer, title, url}
    listings: list[dict]  # asking listings seen: {first, last, price, status, title, url}
    asking_line: list[TrendPoint]  # median asking price per day, with the low-high spread
    sold_line: list[TrendPoint]  # median sold price per day (week, for long ranges)
    asking_now: float | None
    sold_median: float | None
    sold_count: int
    asking_count: int


@router.get("/price-points", response_model=PricePoints)
def price_points(card_id: int, grade: str | None = None, range: str = Query("3m", pattern="^(7d|1m|3m|1y|all)$"),
                 all_versions: bool = False, session: Session = Depends(get_session)):
    """Every price on record for one grade, for the asking-vs-sold chart: each sold sale, each asking listing
    seen, and the median line through each. Limited to the card's version unless all_versions is set."""
    card = get_card_or_404(session, card_id)
    g = normalize_grade(grade or card.grade)
    days = _RANGE_DAYS[range]
    cutoff = utcnow() - timedelta(days=days) if days else None
    in_range = lambda d: cutoff is None or d >= cutoff  # noqa: E731
    version = lambda title: all_versions or not card.parallel or parse_parallel(title) == card.parallel  # noqa: E731

    sales = [r for r in session.exec(select(SoldSale).where(SoldSale.card_id == card_id, SoldSale.junk == False,  # noqa: E712
                                                            SoldSale.grade == g).order_by(col(SoldSale.ended_at))).all()
             if r.ended_at and in_range(r.ended_at) and version(r.title)]
    seen = [r for r in session.exec(select(ListingSeen).where(ListingSeen.card_id == card_id, ListingSeen.excluded == False,  # noqa: E712
                                                              ListingSeen.grade == g)).all()
            if (r.status == "active" or in_range(r.last_seen)) and version(r.title)]
    seen.sort(key=lambda r: r.last_seen, reverse=True)
    snaps = [x for x in session.exec(select(PriceSnapshot).where(PriceSnapshot.card_id == card_id, PriceSnapshot.grade == g)
                                     .order_by(col(PriceSnapshot.created_at))).all()
             if x.source in ASKING_SOURCES and in_range(x.created_at) and (all_versions or (x.parallel or None) == (card.parallel or None))]
    weekly = days is None or days > 31
    active = [r.price for r in seen if r.status == "active"]
    return PricePoints(
        grade=g, range=range,
        sales=[{"date": r.ended_at.date().isoformat(), "price": r.price, "best_offer": r.best_offer, "title": r.title, "url": r.url} for r in sales],
        listings=[{"first": r.first_seen.date().isoformat(), "last": (r.gone_at or r.last_seen).date().isoformat(), "price": r.price,
                   "status": r.status, "title": r.title, "url": r.url} for r in seen[:300]],
        asking_line=_daily(snaps), sold_line=_sales_points(sales, weekly),
        asking_now=round(_median(active), 2) if active else None,
        sold_median=round(_median(r.price for r in sales), 2) if sales else None,
        sold_count=len(sales), asking_count=len(active))
