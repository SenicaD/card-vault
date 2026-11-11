from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Column
from sqlalchemy.types import JSON, DateTime, TypeDecorator
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """Stores UTC; always returns tz-aware UTC (SQLite drops tzinfo), so the API emits ISO 8601 with Z."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=UTC) if value is not None else None


def ts(**kw):
    return Field(sa_type=UTCDateTime, **kw)


class CardCategory(StrEnum):
    sports = "sports"
    pokemon = "pokemon"
    mtg = "mtg"
    yugioh = "yugioh"
    other_tcg = "other_tcg"
    other = "other"


class CardStatus(StrEnum):
    in_stock = "in_stock"
    listed = "listed"
    sold = "sold"


# ---------- Storage locations (box / binder / shelf) ----------

class LocationBase(SQLModel):
    name: str = Field(index=True)  # e.g. "Binder A", "Shoebox 3"
    kind: str = "box"  # box | binder | case | shelf
    notes: str | None = None


class Location(LocationBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = ts(default_factory=utcnow)


class LocationCreate(LocationBase):
    pass


class LocationRead(LocationBase):
    id: int
    created_at: datetime
    card_count: int = 0


# ---------- Cards ----------

class CardBase(SQLModel):
    category: CardCategory = CardCategory.other
    name: str  # player or card name
    set_name: str | None = None
    year: str | None = None
    card_number: str | None = None
    variant: str | None = None  # parallel, foil, 1st edition, refractor...
    grade: str | None = None  # "Raw", "PSA 10", "BGS 9.5"
    condition: str | None = None  # NM, LP, MP... for raw cards
    quantity: int = 1
    location_id: int | None = Field(default=None, foreign_key="location.id")
    slot: str | None = None  # page/pocket or row, e.g. "p3-5"
    notes: str | None = None
    search_query: str | None = None  # query used for eBay lookup


class Card(CardBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    status: CardStatus = CardStatus.in_stock
    image_path: str | None = None
    id_confidence: float | None = None
    est_value: float | None = None  # latest median price
    priced_at: datetime | None = ts(default=None)
    created_at: datetime = ts(default_factory=utcnow)
    updated_at: datetime = ts(default_factory=utcnow)


class PriceQuote(SQLModel):
    """A price summary not yet tied to a card (scan-time lookup, or a re-price before saving)."""
    query: str
    source: str = "ebay-active"
    sample_size: int
    low: float | None
    median: float | None
    mean: float | None
    high: float | None
    currency: str = "USD"
    # Best-match listings behind this quote (EbayItem dicts), kept so the card page can show them later
    top_listings: list[dict[str, Any]] | None = None
    # Grade this quote covers ("Raw", "PSA 10", ...). None = every listing pooled together (older data).
    grade: str | None = None


class CardCreate(CardBase):
    image_path: str | None = None
    id_confidence: float | None = None
    # A quote fetched at scan time; stored as the card's first price snapshot so it isn't re-fetched.
    price: PriceQuote | None = None


class CardUpdate(SQLModel):
    category: CardCategory | None = None
    name: str | None = None
    set_name: str | None = None
    year: str | None = None
    card_number: str | None = None
    variant: str | None = None
    grade: str | None = None
    condition: str | None = None
    quantity: int | None = None
    location_id: int | None = None
    slot: str | None = None
    notes: str | None = None
    search_query: str | None = None
    status: CardStatus | None = None


class CardRead(CardBase):
    id: int
    status: CardStatus
    image_url: str | None = None
    id_confidence: float | None
    est_value: float | None
    priced_at: datetime | None
    created_at: datetime
    updated_at: datetime


# ---------- Price snapshots ----------

class PriceSnapshot(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    card_id: int = Field(foreign_key="card.id", index=True)
    query: str
    source: str  # "ebay-active" (Browse API, asking) | "ebay-image" (look-alikes) | "ebay-sold" (Marketplace Insights)
    sample_size: int
    low: float | None
    median: float | None
    mean: float | None
    high: float | None
    currency: str = "USD"
    top_listings: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    grade: str | None = Field(default=None, index=True)
    created_at: datetime = ts(default_factory=utcnow)


# ---------- Listings ----------

class Listing(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    card_id: int = Field(foreign_key="card.id", index=True)
    sku: str = Field(index=True)
    ebay_offer_id: str | None = None
    ebay_listing_id: str | None = None
    title: str
    price: float
    currency: str = "USD"
    status: str = "draft"  # draft | published | ended
    url: str | None = None
    created_at: datetime = ts(default_factory=utcnow)
