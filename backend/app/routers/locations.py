from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, func, select

from ..db import get_session
from ..models import Card, Location, LocationCreate, LocationRead

router = APIRouter(prefix="/api/locations", tags=["locations"])


@router.get("", response_model=list[LocationRead])
def list_locations(session: Session = Depends(get_session)):
    counts = dict(session.exec(
        select(Card.location_id, func.coalesce(func.sum(Card.quantity), 0)).group_by(Card.location_id)
    ).all())
    locs = session.exec(select(Location).order_by(Location.name)).all()
    return [LocationRead(**loc.model_dump(), card_count=counts.get(loc.id, 0)) for loc in locs]


@router.post("", response_model=LocationRead, status_code=201)
def create_location(body: LocationCreate, session: Session = Depends(get_session)):
    loc = Location.model_validate(body)
    session.add(loc)
    session.commit()
    session.refresh(loc)
    return LocationRead(**loc.model_dump(), card_count=0)


@router.delete("/{location_id}", status_code=204)
def delete_location(location_id: int, session: Session = Depends(get_session)):
    loc = session.get(Location, location_id)
    if not loc:
        raise HTTPException(404, "Location not found")
    for card in session.exec(select(Card).where(Card.location_id == location_id)):
        card.location_id = None  # unassign rather than delete cards
        session.add(card)
    session.delete(loc)
    session.commit()
