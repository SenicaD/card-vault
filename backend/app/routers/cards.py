from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, col, func, or_, select

from ..config import get_settings
from ..db import engine, get_session
from ..models import ASKING_SOURCES, Card, CardCategory, CardCreate, CardRead, CardStatus, CardUpdate, PriceSnapshot, utcnow
from ..services.imagecrop import CropBox, CropError, crop_saved_image
from ..services.grading import normalize_grade
from ..services.identify import CardIdentification, build_search_query, get_identifier
from ..services import refresh as refresh_service
from ..services.sold_refresh import recompute_sold
from ..services.trace import identification_trace, save_trace
from .common import get_card_or_404, to_read

router = APIRouter(prefix="/api/cards", tags=["cards"])
VALUE = func.coalesce(Card.manual_value, Card.sold_value, Card.est_value)  # the value shown: yours, else sold, else asking


def filtered(stmt, q=None, category=None, status=None, location_id=None, grade=None, min_value=None,
             max_value=None, unpriced=False, stale_days=None, low_confidence=False, no_photo=False,
             no_cost=False, needs_review=False):
    """Narrow a card query. Every filter given must hold (they stack)."""
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(col(Card.name).ilike(like), col(Card.set_name).ilike(like),
                              col(Card.card_number).ilike(like), col(Card.variant).ilike(like), col(Card.batch).ilike(like)))
    if category:
        stmt = stmt.where(Card.category == category)
    if status:
        stmt = stmt.where(Card.status == status)
    if location_id is not None:
        stmt = stmt.where(Card.location_id == location_id)
    if grade:
        stmt = stmt.where(Card.grade == grade) if grade != "Raw" else stmt.where(or_(Card.grade == "Raw", col(Card.grade).is_(None)))
    if min_value is not None:
        stmt = stmt.where(VALUE >= min_value)
    if max_value is not None:
        stmt = stmt.where(VALUE <= max_value)
    if unpriced:
        stmt = stmt.where(VALUE.is_(None))
    if stale_days:
        stmt = stmt.where(or_(col(Card.priced_at).is_(None), col(Card.priced_at) < utcnow() - timedelta(days=stale_days)))
    if low_confidence:
        stmt = stmt.where(col(Card.id_confidence) < LOW_CONFIDENCE)
    if no_photo:
        stmt = stmt.where(col(Card.image_path).is_(None))
    if no_cost:
        stmt = stmt.where(col(Card.cost).is_(None))
    if needs_review:  # not yet looked over, and either poorly identified or without a price
        stmt = stmt.where(col(Card.reviewed) == False, or_(col(Card.id_confidence) < LOW_CONFIDENCE, VALUE.is_(None)))  # noqa: E712
    return stmt


LOW_CONFIDENCE = 0.3


@router.get("", response_model=list[CardRead])
def list_cards(
    q: str | None = None,
    category: CardCategory | None = None,
    status: CardStatus | None = None,
    location_id: int | None = None,
    grade: str | None = None,
    min_value: float | None = None,
    max_value: float | None = None,
    unpriced: bool = False,
    stale_days: int | None = Query(None, ge=1),
    low_confidence: bool = False,
    no_photo: bool = False,
    no_cost: bool = False,
    needs_review: bool = False,
    sort: str = Query("recent", pattern="^(recent|value|name|oldest_price)$"),
    session: Session = Depends(get_session),
):
    stmt = filtered(select(Card), q, category, status, location_id, normalize_grade(grade) if grade else None,
                    min_value, max_value, unpriced, stale_days, low_confidence, no_photo, no_cost, needs_review)
    order = {"recent": col(Card.created_at).desc(),
             "value": VALUE.desc().nulls_last(),
             "name": col(Card.name).asc(),
             "oldest_price": col(Card.priced_at).asc().nulls_first()}[sort]
    return [to_read(c) for c in session.exec(stmt.order_by(order)).all()]


@router.get("/stats")
def stats(session: Session = Depends(get_session)):
    rows = session.exec(select(Card.category, Card.status, func.sum(Card.quantity)).group_by(Card.category, Card.status)).all()
    out = {"total_cards": 0, "by_category": {}, "by_status": {}}
    for cat, st, qty in rows:
        out["total_cards"] += qty or 0
        out["by_category"][cat] = out["by_category"].get(cat, 0) + (qty or 0)
        out["by_status"][st] = out["by_status"].get(st, 0) + (qty or 0)
    # Value: what each unsold card sold for when there are enough sales, otherwise its asking-based estimate.
    live = col(Card.status) != CardStatus.sold
    total, asking = session.exec(select(func.coalesce(func.sum(func.coalesce(VALUE, 0) * Card.quantity), 0),
                                        func.coalesce(func.sum(func.coalesce(Card.est_value, 0) * Card.quantity), 0)).where(live)).one()
    sv, n, ask_of_sold = session.exec(select(func.coalesce(func.sum(Card.sold_value * Card.quantity), 0), func.count(),
                                             func.coalesce(func.sum(func.coalesce(Card.est_value, 0) * Card.quantity), 0))
                                      .where(col(Card.sold_value).is_not(None), col(Card.manual_value).is_(None), live)).one()
    fallback = session.exec(select(func.count()).where(col(Card.manual_value).is_(None), col(Card.sold_value).is_(None),
                                                       col(Card.est_value).is_not(None), live)).one()
    mv, mn = session.exec(select(func.coalesce(func.sum(Card.manual_value * Card.quantity), 0), func.count())
                          .where(col(Card.manual_value).is_not(None), live)).one()
    out["manual_value"], out["manual_cards"] = round(mv, 2), mn
    out["total_value"], out["asking_value"] = round(total, 2), round(asking, 2)  # headline value; the asking-only total beside it
    out["sold_value"], out["sold_cards"], out["asking_value_of_sold_cards"] = round(sv, 2), n, round(ask_of_sold, 2)
    out["asking_cards"] = fallback  # cards still valued at their asking estimate for lack of sales
    s = get_settings()
    out["modes"] = {"identifier": s.identifier, "ebay": s.ebay_mode}
    return out


@router.post("", response_model=CardRead, status_code=201)
def create_card(body: CardCreate, background: BackgroundTasks, session: Session = Depends(get_session)):
    card = Card.model_validate(body.model_dump(exclude={"price"}))
    if not card.search_query:
        card.search_query = build_search_query(CardIdentification(
            name=card.name, set_name=card.set_name, year=card.year, card_number=card.card_number,
            variant=card.variant, grade=card.grade, search_query=""))
    if body.price:
        card.est_value = body.price.median
        card.priced_at = utcnow()
    session.add(card)
    session.commit()
    session.refresh(card)
    if body.price:
        session.add(PriceSnapshot(card_id=card.id, **body.price.model_dump()))
        session.commit()
        session.refresh(card)
    if refresh_service.LOOKUP_ON_ADD:  # fills the listings table and graded price after the response
        background.add_task(refresh_service.price_new_card, card.id)
    return to_read(card)


class BulkEdit(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=5000)
    changes: CardUpdate


class BulkIds(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=5000)


# Fields a bulk edit may set. Identity fields (name, number, search query) stay per-card on purpose.
BULK_FIELDS = {"status", "location_id", "slot", "grade", "condition", "parallel", "category", "cost", "manual_value", "quantity", "reviewed", "notes"}


@router.post("/bulk/update")
def bulk_update(body: BulkEdit, session: Session = Depends(get_session)):
    changes = body.changes.model_dump(exclude_unset=True)
    bad = set(changes) - BULK_FIELDS
    if bad:
        raise HTTPException(422, f"Can't bulk-edit: {', '.join(sorted(bad))}")
    if not changes:
        raise HTTPException(422, "Nothing to change")
    cards = session.exec(select(Card).where(col(Card.id).in_(body.ids))).all()
    for c in cards:
        apply_update(session, c, changes)
    session.commit()
    return {"updated": len(cards), "missing": len(set(body.ids)) - len(cards)}


@router.post("/bulk/delete")
def bulk_delete(body: BulkIds, session: Session = Depends(get_session)):
    cards = session.exec(select(Card).where(col(Card.id).in_(body.ids))).all()
    for c in cards:
        session.delete(c)
    session.commit()
    return {"deleted": len(cards)}


@router.post("/bulk/reprice", status_code=202)
async def bulk_reprice(body: BulkIds | None = None):
    """Start re-pricing these cards (or every unsold card when no ids are given) in the background."""
    job = refresh_service.start_job(body.ids if body else None)
    if job is None:
        raise HTTPException(409, "A re-price is already running")
    return job.snapshot()


@router.get("/bulk/reprice")
def bulk_reprice_status():
    job = refresh_service.current_job()
    return job.snapshot() if job else {"state": "idle"}


def _norm_num(v: str | None) -> str:
    return (v or "").strip().lstrip("#").lower()


@router.get("/duplicates", response_model=list[CardRead])
def duplicates(name: str, set_name: str | None = None, card_number: str | None = None, grade: str | None = None,
               session: Session = Depends(get_session)):
    """Cards you already own that look like this one: same name and grade, and the same set and number
    wherever both sides have one. Used while scanning to offer "add a copy" instead of a second entry."""
    rows = session.exec(select(Card).where(func.lower(Card.name) == name.strip().lower(),
                                           col(Card.status) != CardStatus.sold)).all()
    want = normalize_grade(grade)
    out = []
    for c in rows:
        if normalize_grade(c.grade) != want:
            continue
        if _norm_num(c.card_number) and _norm_num(card_number) and _norm_num(c.card_number) != _norm_num(card_number):
            continue
        if (c.set_name or "").strip() and (set_name or "").strip() and c.set_name.strip().lower() != set_name.strip().lower():
            continue
        out.append(to_read(c))
    return out[:5]


@router.get("/{card_id}", response_model=CardRead)
def get_card(card_id: int, session: Session = Depends(get_session)):
    return to_read(get_card_or_404(session, card_id))


def apply_update(session: Session, card: Card, changes: dict) -> None:
    """Apply field changes to a card (single edit or bulk)."""
    old_grade, old_parallel = normalize_grade(card.grade), card.parallel
    card.sqlmodel_update(changes)
    new_grade = normalize_grade(card.grade)
    if new_grade != old_grade or card.parallel != old_parallel:
        # The estimate belonged to the old grade or version. Use the newest price we already have for the new one,
        # or clear it until the card is priced, rather than keep showing the old grade's value.
        snap = session.exec(select(PriceSnapshot).where(
            PriceSnapshot.card_id == card.id, col(PriceSnapshot.source).in_(ASKING_SOURCES), PriceSnapshot.grade == new_grade, PriceSnapshot.parallel == card.parallel,
            col(PriceSnapshot.median).is_not(None)).order_by(col(PriceSnapshot.created_at).desc()).limit(1)).first()
        card.est_value, card.priced_at = (snap.median, snap.created_at) if snap else (None, None)
        recompute_sold(session, card)  # the sold-based value belongs to the grade and version too
    elif {"name", "year", "card_number", "id_confidence", "set_name", "variant", "search_query"} & changes.keys():
        session.flush()
        recompute_sold(session, card)  # who the card is (and whether it's a redemption) decides which sales count
    card.updated_at = utcnow()
    session.add(card)


@router.patch("/{card_id}", response_model=CardRead)
def update_card(card_id: int, body: CardUpdate, session: Session = Depends(get_session)):
    card = get_card_or_404(session, card_id)
    apply_update(session, card, body.model_dump(exclude_unset=True))
    session.commit()
    session.refresh(card)
    return to_read(card)


@router.delete("/{card_id}", status_code=204)
def delete_card(card_id: int, session: Session = Depends(get_session)):
    card = get_card_or_404(session, card_id)
    session.delete(card)
    session.commit()


@router.post("/{card_id}/crop", response_model=CardRead)
async def crop_card_photo(card_id: int, box: CropBox, session: Session = Depends(get_session)):
    """Replace the card's photo with a crop of it, so photo-based eBay pricing sees only the card.

    If the old identification was poor (under 30%) and the cropped photo identifies clearly better,
    the name, set, year, number, variant and search words are replaced by what the crop identified.
    """
    card = get_card_or_404(session, card_id)
    if not card.image_path:
        raise HTTPException(422, "This card has no photo")
    before = card.image_path
    try:
        card.image_path, data = crop_saved_image(card.image_path, box)
    except CropError as e:
        raise HTTPException(422, str(e)) from e
    old = card.id_confidence or 0.0
    identifier = get_identifier()
    if old < 0.3 and hasattr(identifier, "match"):
        try:
            ident, found = await identifier.match(data)
            try:  # its own session, so a failure here can't undo the crop
                with Session(engine) as trace_session:
                    save_trace(trace_session, identification_trace(identifier.name, ident, found, card.image_path, before,
                                                                   box.model_dump(), "crop", len(data)), card.id)
            except Exception:
                pass
        except Exception:
            ident = None
        if ident and ident.name and ident.confidence >= 0.3 and ident.confidence > old:
            card.name, card.set_name, card.year = ident.name, ident.set_name, ident.year
            card.card_number, card.variant, card.search_query = ident.card_number, ident.variant, ident.search_query
            card.category, card.id_confidence = ident.category, ident.confidence
    card.updated_at = utcnow()
    session.add(card)
    session.commit()
    session.refresh(card)
    return to_read(card)
