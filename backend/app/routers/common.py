from fastapi import HTTPException
from sqlmodel import Session

from ..models import Card, CardRead


def card_value(card: Card) -> tuple[float | None, str | None]:
    """The value to show for a card: the one you set by hand, else what it sold for when there are enough sales,
    else the asking-based estimate."""
    if card.manual_value is not None:
        return card.manual_value, "manual"
    if card.sold_value is not None:
        return card.sold_value, "sold"
    if card.est_value is not None:
        return card.est_value, "asking"
    return None, None


def to_read(card: Card) -> CardRead:
    data = card.model_dump()
    data["image_url"] = f"/images/{card.image_path}" if card.image_path else None
    data["value"], data["value_basis"] = card_value(card)
    return CardRead.model_validate(data)


def get_card_or_404(session: Session, card_id: int) -> Card:
    card = session.get(Card, card_id)
    if not card:
        raise HTTPException(404, f"Card {card_id} not found")
    return card
