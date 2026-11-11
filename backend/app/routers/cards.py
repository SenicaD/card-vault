from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, col, func, or_, select

from ..config import get_settings
from ..db import get_session
from ..models import Card, CardCategory, CardCreate, CardRead, CardStatus, CardUpdate, PriceSnapshot, utcnow
from ..services.grading import normalize_grade
from ..services.identify import CardIdentification, build_search_query
from .common import get_card_or_404, to_read

router = APIRouter(prefix="/api/cards", tags=["cards"])


@router.get("", response_model=list[CardRead])
def list_cards(
    q: str | None = None,
    category: CardCategory | None = None,
    status: CardStatus | None = None,
    location_id: int | None = None,
    sort: str = Query("recent", pattern="^(recent|value|name)$"),
    session: Session = Depends(get_session),
):
    stmt = select(Card)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(col(Card.name).ilike(like), col(Card.set_name).ilike(like),
                              col(Card.card_number).ilike(like), col(Card.variant).ilike(like)))
    if category:
        stmt = stmt.where(Card.category == category)
    if status:
        stmt = stmt.where(Card.status == status)
    if location_id is not None:
        stmt = stmt.where(Card.location_id == location_id)
    order = {"recent": col(Card.created_at).desc(),
             "value": col(Card.est_value).desc().nulls_last(),
             "name": col(Card.name).asc()}[sort]
    return [to_read(c) for c in session.exec(stmt.order_by(order)).all()]


@router.get("/stats")
def stats(session: Session = Depends(get_session)):
    rows = session.exec(select(Card.category, Card.status, func.sum(Card.quantity),
                               func.sum(func.coalesce(Card.est_value, 0) * Card.quantity))
                        .group_by(Card.category, Card.status)).all()
    out = {"total_cards": 0, "total_value": 0.0, "by_category": {}, "by_status": {}}
    for cat, st, qty, val in rows:
        out["total_cards"] += qty or 0
        out["total_value"] += val or 0
        out["by_category"][cat] = out["by_category"].get(cat, 0) + (qty or 0)
        out["by_status"][st] = out["by_status"].get(st, 0) + (qty or 0)
    out["total_value"] = round(out["total_value"], 2)
    s = get_settings()
    out["modes"] = {"identifier": s.identifier, "ebay": s.ebay_mode}
    return out


@router.post("", response_model=CardRead, status_code=201)
def create_card(body: CardCreate, session: Session = Depends(get_session)):
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
    return to_read(card)


@router.get("/{card_id}", response_model=CardRead)
def get_card(card_id: int, session: Session = Depends(get_session)):
    return to_read(get_card_or_404(session, card_id))


@router.patch("/{card_id}", response_model=CardRead)
def update_card(card_id: int, body: CardUpdate, session: Session = Depends(get_session)):
    card = get_card_or_404(session, card_id)
    old_grade = normalize_grade(card.grade)
    card.sqlmodel_update(body.model_dump(exclude_unset=True))
    new_grade = normalize_grade(card.grade)
    if new_grade != old_grade:
        # The estimate belonged to the old grade. Use the newest price we already have for the new one,
        # or clear it until the card is priced, rather than keep showing the old grade's value.
        snap = session.exec(select(PriceSnapshot).where(
            PriceSnapshot.card_id == card.id, PriceSnapshot.source == "ebay-active", PriceSnapshot.grade == new_grade,
            col(PriceSnapshot.median).is_not(None)).order_by(col(PriceSnapshot.created_at).desc()).limit(1)).first()
        card.est_value, card.priced_at = (snap.median, snap.created_at) if snap else (None, None)
    card.updated_at = utcnow()
    session.add(card)
    session.commit()
    session.refresh(card)
    return to_read(card)


@router.delete("/{card_id}", status_code=204)
def delete_card(card_id: int, session: Session = Depends(get_session)):
    card = get_card_or_404(session, card_id)
    session.delete(card)
    session.commit()
