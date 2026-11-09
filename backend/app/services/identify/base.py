from typing import Protocol

from pydantic import BaseModel, Field

from ...models import CardCategory


class CardIdentification(BaseModel):
    """What the identifier thinks the card is. Every field is a best guess the user can edit."""

    category: CardCategory = CardCategory.other
    name: str = Field(description="Player name (sports) or card name (TCG)")
    set_name: str | None = None
    year: str | None = None
    card_number: str | None = None
    variant: str | None = None
    grade: str | None = None
    condition: str | None = None
    confidence: float = Field(ge=0, le=1, default=0.5)
    search_query: str = Field(description="Query string tuned for eBay search")


class CardIdentifier(Protocol):
    name: str

    async def identify(self, image_bytes: bytes, media_type: str) -> CardIdentification: ...


def build_search_query(ident: CardIdentification) -> str:
    """Fallback query builder: year + set + name + number + variant + grade."""
    parts = [ident.year, ident.set_name, ident.name]
    if ident.card_number:
        parts.append(f"#{ident.card_number.lstrip('#')}")
    parts += [ident.variant]
    if ident.grade and ident.grade.lower() != "raw":
        parts.append(ident.grade)
    return " ".join(p for p in parts if p)
