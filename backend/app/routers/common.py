from fastapi import HTTPException
from sqlmodel import Session

from ..models import Card, CardRead, CardStatus
from ..services.target import list_price, raw_target

LIST_MARKUP = 1.10  # used when no settings are at hand; the Settings page's list_markup otherwise


def card_value(card: Card) -> tuple[float | None, str | None]:
    """The value to show for a card: its target price (services/target.py), kept current whenever the card is saved.
    A sold card, or one not saved since targets existed, falls back to your value, else sold, else asking."""
    if card.target_basis is not None and card.status != CardStatus.sold:
        return card.target_value, card.target_basis
    return raw_target(card)


def to_read(card: Card, markup: float = LIST_MARKUP) -> CardRead:
    data = card.model_dump()
    data["image_url"] = f"/images/{card.image_path}" if card.image_path else None
    data["value"], data["value_basis"] = card_value(card)
    data["value_pending"] = card.target_pending
    data["list_price"] = list_price(data["value"], markup)
    return CardRead.model_validate(data)


def get_card_or_404(session: Session, card_id: int) -> Card:
    card = session.get(Card, card_id)
    if not card:
        raise HTTPException(404, f"Card {card_id} not found")
    return card
