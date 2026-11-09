from fastapi import HTTPException
from sqlmodel import Session

from ..models import Card, CardRead


def to_read(card: Card) -> CardRead:
    data = card.model_dump()
    data["image_url"] = f"/images/{card.image_path}" if card.image_path else None
    return CardRead.model_validate(data)


def get_card_or_404(session: Session, card_id: int) -> Card:
    card = session.get(Card, card_id)
    if not card:
        raise HTTPException(404, f"Card {card_id} not found")
    return card
