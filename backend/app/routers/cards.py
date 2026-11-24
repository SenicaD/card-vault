from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, col, func, or_, select

from ..config import get_settings
from ..db import engine, get_session
from ..models import ASKING_SOURCES, Card, CardCategory, CardCreate, CardRead, CardStatus, CardUpdate, ListingSeen, PriceSnapshot, utcnow
from ..services.imagecrop import CropBox, CropError, crop_saved_image
from ..services.grading import normalize_grade
from ..services.identify import CardIdentification, build_search_query, get_identifier
from ..services import refresh as refresh_service
from ..services.app_settings import load_app_settings
from ..services.identity import identity_of_card
from ..services.pricing import apply_asking, asking_listings
from ..services.sold_refresh import recompute_sold
from ..services.tracker import own_snap, revalue
from ..services.trace import identification_trace, save_trace
from .common import get_card_or_404, to_read

router = APIRouter(prefix="/api/cards", tags=["cards"])
# the value shown: the target price (services/target.py), else (a card not saved since targets existed) yours, else
# sold, else asking
VALUE = func.coalesce(Card.target_value, Card.manual_value, Card.sold_value, Card.est_value)
# Dollars at risk: what a wrong price costs, the value times how unsure it is. Unpriced cards come first.
RISK = func.coalesce(VALUE, 1e9) * (100 - func.coalesce(Card.confidence, 0))


def filtered(stmt, q=None, category=None, status=None, location_id=None, grade=None, min_value=None,
             max_value=None, unpriced=False, stale_days=None, low_confidence=False, no_photo=False,
             no_cost=False, needs_review=False, review_below: int = 40, unsure=False):
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
    unsure_price = col(Card.confidence) < review_below
    if needs_review:  # not yet looked over, and poorly identified, without a price, or its price is doubtful
        stmt = stmt.where(col(Card.reviewed) == False, col(Card.status) != CardStatus.sold,  # noqa: E712
                          or_(col(Card.id_confidence) < LOW_CONFIDENCE, VALUE.is_(None), unsure_price))
    if unsure:
        stmt = stmt.where(unsure_price)
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
    unsure: bool = False,
    sort: str = Query("recent", pattern="^(recent|value|name|oldest_price|risk)$"),
    session: Session = Depends(get_session),
):
    st = load_app_settings(session)
    stmt = filtered(select(Card), q, category, status, location_id, normalize_grade(grade) if grade else None,
                    min_value, max_value, unpriced, stale_days, low_confidence, no_photo, no_cost, needs_review,
                    st.review_below, unsure)
    order = {"recent": col(Card.created_at).desc(),
             "value": VALUE.desc().nulls_last(),
             "name": col(Card.name).asc(),
             "oldest_price": col(Card.priced_at).asc().nulls_first(),
             "risk": RISK.desc()}[sort]
    return [to_read(c, st.list_markup) for c in session.exec(stmt.order_by(order)).all()]


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
    # cards whose asking prices spread too widely to give an asking value
    out["flagged_cards"] = session.exec(select(func.count()).where(Card.wide_spread == True, live)).one()  # noqa: E712
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
        apply_asking(card, body.price.median, body.price.spread)
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


@router.post("/bulk/revalue")
def bulk_revalue(session: Session = Depends(get_session)):
    """Re-value every unsold card from its saved quote and listings (no eBay calls, takes seconds). Use it after
    changing the price settings; the rest of the work a full re-price does is only needed for fresh listings."""
    app_settings = load_app_settings(session)
    changed = same = skipped = 0
    for card in session.exec(select(Card).where(col(Card.status) != CardStatus.sold)).all():
        before = card.est_value
        if not revalue(session, card, app_settings):
            skipped += 1
            continue
        if card.est_value != before:
            card.updated_at = utcnow()
            changed += 1
        else:
            same += 1
        session.add(card)
    session.commit()
    return {"changed": changed, "unchanged": same, "skipped": skipped}


@router.post("/bulk/sold")
async def bulk_sold(limit: int = Query(10, ge=1, le=50)):
    """Check sold prices now for up to `limit` due cards, the most dollars at risk first. Spends the monthly
    lookup budget (one lookup per card, sometimes more), so you choose how many."""
    from ..services.sold import get_sold
    from ..services.sold_refresh import budget_left, sold_once
    if get_sold() is None:
        raise HTTPException(422, "Sold prices aren't set up. Add a SoldComps key to turn them on.")
    done, failed = await sold_once(pause=0.5, limit=limit, kind="manual")
    with Session(engine) as session:
        left = budget_left(session, "manual")
    return {"checked": done, "failed": failed, "budget_left": left}


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


def _dup_key(c: Card) -> tuple:
    """Cards with the same key are the same card entered twice: name, grade, set, number and version all agree."""
    return ((c.name or "").strip().lower(), normalize_grade(c.grade), (c.set_name or "").strip().lower(),
            _norm_num(c.card_number), c.parallel or "")


@router.get("/duplicate-groups", response_model=list[list[CardRead]])
def duplicate_groups(session: Session = Depends(get_session)):
    """Unsold cards entered more than once (same name, grade, set, number and version), oldest first in each group,
    so they can be merged into one card with the quantities added up."""
    groups: dict[tuple, list[Card]] = {}
    for c in session.exec(select(Card).where(col(Card.status) != CardStatus.sold).order_by(col(Card.id))).all():
        groups.setdefault(_dup_key(c), []).append(c)
    markup = load_app_settings(session).list_markup
    return [[to_read(c, markup) for c in g] for g in groups.values() if len(g) > 1]


class MergeBody(BaseModel):
    keep: int  # the card that stays (its photo, prices and history)
    merge: list[int] = Field(min_length=1, max_length=500)  # cards folded into it, then deleted


@router.post("/merge", response_model=CardRead)
def merge_cards(body: MergeBody, session: Session = Depends(get_session)):
    """Fold duplicate cards into one: the quantities are added up, a cost or value missing on the kept card is taken
    from the others (cost averaged over the copies), notes are joined, and the other cards are deleted."""
    keep = get_card_or_404(session, body.keep)
    others = session.exec(select(Card).where(col(Card.id).in_(set(body.merge) - {keep.id}))).all()
    if not others:
        raise HTTPException(422, "Nothing to merge")
    if any(c.status == CardStatus.sold for c in [keep, *others]):
        raise HTTPException(409, "Sold cards can't be merged")
    if any(_dup_key(c) != _dup_key(keep) for c in others):
        raise HTTPException(422, "Only the same card can be merged: name, grade, set, number and version must match")
    cards = [keep, *others]
    costed = [c for c in cards if c.cost is not None]
    if costed:  # the average paid per copy, over the copies whose cost is known
        keep.cost = round(sum(c.cost * c.quantity for c in costed) / sum(c.quantity for c in costed), 2)
    keep.quantity = sum(c.quantity for c in cards)
    keep.manual_value = keep.manual_value if keep.manual_value is not None else next(
        (c.manual_value for c in others if c.manual_value is not None), None)
    keep.location_id = keep.location_id if keep.location_id is not None else next(
        (c.location_id for c in others if c.location_id is not None), None)
    notes = [n for n in dict.fromkeys(c.notes.strip() for c in cards if c.notes and c.notes.strip())]
    keep.notes = "\n".join(notes) or None
    keep.updated_at = utcnow()
    session.add(keep)
    for c in others:
        session.delete(c)
    session.commit()
    session.refresh(keep)
    return to_read(keep, load_app_settings(session).list_markup)


@router.get("/{card_id}", response_model=CardRead)
def get_card(card_id: int, session: Session = Depends(get_session)):
    return to_read(get_card_or_404(session, card_id), load_app_settings(session).list_markup)


def apply_update(session: Session, card: Card, changes: dict) -> None:
    """Apply field changes to a card (single edit or bulk)."""
    old_grade, old_parallel = normalize_grade(card.grade), card.parallel
    card.sqlmodel_update(changes)
    new_grade = normalize_grade(card.grade)
    if new_grade != old_grade or card.parallel != old_parallel:
        # The estimate belonged to the old grade or version. Use the newest price we already have for the new one,
        # or clear it until the card is priced, rather than keep showing the old grade's value.
        snap = own_snap(session, card)
        if snap and revalue(session, card):  # the price window applies to the new version's saved listings too
            card.priced_at = snap.created_at
        else:
            apply_asking(card, None, None)
            card.priced_at = None
        recompute_sold(session, card)  # the sold-based value belongs to the grade and version too
    elif {"name", "year", "card_number", "id_confidence", "set_name", "variant", "search_query", "is_redemption"} & changes.keys():
        session.flush()
        recompute_sold(session, card)  # who the card is (and whether it's a redemption) decides which sales count
    if "wide_spread_action" in changes:
        revalue(session, card)  # a new choice for wide-spread prices shows at once
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
            card.is_redemption, card.is_auto = ident.is_redemption, ident.is_auto
    card.updated_at = utcnow()
    session.add(card)
    session.commit()
    session.refresh(card)
    return to_read(card)
