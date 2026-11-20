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
    cost: float | None = None  # what you paid for ONE copy; None = not recorded
    manual_value: float | None = None  # a value you set yourself for ONE copy; it overrides sold and asking values
    batch: str | None = None  # name of the scan session it was added in, e.g. "Box 3 lot"


class Card(CardBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    status: CardStatus = CardStatus.in_stock
    image_path: str | None = None
    id_confidence: float | None = None
    est_value: float | None = None  # latest median price
    priced_at: datetime | None = ts(default=None)
    # Listings eBay showed per grade the last time this card was searched, e.g. {"Raw": 0, "BGS 9": 4}.
    # Lets the card page suggest grades that do have listings without making a new search.
    grade_counts: dict[str, int] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    grade_counts_at: datetime | None = ts(default=None)
    # The colour/print-run version of the card ("Green /99", "Base") prices are limited to; None = all pooled.
    parallel: str | None = None
    # True once you've looked the card over in the review queue, so it stops being offered there.
    reviewed: bool = False
    # What matching copies actually sold for (median of the last 90 days' sales of the card's own grade and
    # version), kept beside the asking-price estimate. None until enough sales are on record.
    sold_value: float | None = None
    sold_count: int = 0  # sales behind it (counted even when too few to give a value)
    sold_low: float | None = None
    sold_high: float | None = None
    sold_at: datetime | None = ts(default=None)
    created_at: datetime = ts(default_factory=utcnow)
    updated_at: datetime = ts(default_factory=utcnow)


# Snapshot sources that are asking prices (text search, or the card photo through eBay image search)
ASKING_SOURCES = ("ebay-active", "ebay-image")


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
    # The listing behind each price: {"low": EbayItem, "median": EbayItem, "high": EbayItem}
    price_refs: dict[str, dict[str, Any]] | None = None
    # Grade this quote covers ("Raw", "PSA 10", ...). None = every listing pooled together (older data).
    grade: str | None = None
    # Version of the card the quote was limited to (None = all), and how spread out the prices were:
    # (Q3 - Q1) / median, plus how many outlier listings were left out of the numbers.
    parallel: str | None = None
    spread: float | None = None
    trimmed: int | None = None


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
    parallel: str | None = None
    status: CardStatus | None = None
    cost: float | None = None
    manual_value: float | None = None
    batch: str | None = None
    reviewed: bool | None = None


class CardRead(CardBase):
    id: int
    status: CardStatus
    image_url: str | None = None
    id_confidence: float | None
    est_value: float | None
    priced_at: datetime | None
    grade_counts: dict[str, int] | None = None
    grade_counts_at: datetime | None = None
    parallel: str | None = None
    reviewed: bool = False
    sold_value: float | None = None
    sold_count: int = 0
    sold_low: float | None = None
    sold_high: float | None = None
    sold_at: datetime | None = None
    # The value to show: the sold value when the card has enough sales, otherwise the asking-based estimate
    value: float | None = None
    value_basis: str | None = None  # "manual" | "sold" | "asking" | None (no price yet)
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
    price_refs: dict[str, dict[str, Any]] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    grade: str | None = Field(default=None, index=True)
    parallel: str | None = None
    spread: float | None = None
    trimmed: int | None = None
    created_at: datetime = ts(default_factory=utcnow)


# ---------- Sales you made ----------

class Sale(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    card_id: int = Field(foreign_key="card.id", index=True)
    price: float
    currency: str = "USD"
    grade: str | None = None  # the card's grade label when it sold, so the chart can put it on the right line
    note: str | None = None
    sold_at: datetime = ts(default_factory=utcnow)
    created_at: datetime = ts(default_factory=utcnow)


# ---------- Listings we have watched ----------

class ListingSeen(SQLModel, table=True):
    """An eBay listing seen in a card's searches. When one disappears, its last asking price is the
    best guess we have at what it sold for (it may also have been ended or relisted)."""

    id: int | None = Field(default=None, primary_key=True)
    card_id: int = Field(foreign_key="card.id", index=True)
    item_id: str = Field(index=True)
    grade: str
    title: str
    price: float  # the latest asking price while it was up
    currency: str = "USD"
    condition: str | None = None
    url: str | None = None
    image_url: str | None = None
    first_seen: datetime = ts(default_factory=utcnow)
    last_seen: datetime = ts(default_factory=utcnow)
    status: str = "active"  # active | gone
    gone_at: datetime | None = ts(default=None)
    verdict: str | None = None  # why we think it's gone: "not_found" | "out_of_stock"
    excluded: bool = False  # you marked it as not your card; left out of this card's prices


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


# ---------- Debugging: how a price was worked out, and what the background jobs did ----------

class PriceTrace(SQLModel, table=True):
    """One lookup, recorded step by step: the searches made, what happened to every listing, and the result.

    Cheap parts (calls, funnel) are kept for the newest 20 lookups per card. The per-listing table is kept
    for the newest 3 and the raw eBay data only for the newest manual lookup, to keep the database small.
    """

    id: int | None = Field(default=None, primary_key=True)
    card_id: int | None = Field(default=None, index=True)
    kind: str = "lookup"  # lookup | identify
    trigger: str | None = None  # manual | daily | bulk | crop | scan | add
    created_at: datetime = ts(default_factory=utcnow)
    image_path: str | None = Field(default=None, index=True)  # photo used (identify traces)
    source_image: str | None = None  # the photo a crop was cut from
    query: str | None = None
    mode: str | None = None  # ebay-image | ebay-active
    parallel: str | None = None
    duration_ms: int | None = None
    error: str | None = None
    grades: list[str] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    calls: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    funnel: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    summary: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    listings: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    raw: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    identity: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    notes: list[str] | None = Field(default=None, sa_column=Column(JSON, nullable=True))


class JobRun(SQLModel, table=True):
    """A background re-price run (the daily one, or a bulk run you started)."""

    id: int | None = Field(default=None, primary_key=True)
    kind: str  # daily | bulk
    started_at: datetime = ts(default_factory=utcnow)
    finished_at: datetime | None = ts(default=None)
    total: int = 0
    refreshed: int = 0
    failed: int = 0
    errors: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSON, nullable=True))


# ---------- Sold prices (from a sold-listings provider) ----------

class SoldSale(SQLModel, table=True):
    """A sale reported by the sold-prices provider for one of your cards' searches."""

    id: int | None = Field(default=None, primary_key=True)
    card_id: int = Field(foreign_key="card.id", index=True)
    item_id: str = Field(index=True)
    grade: str
    title: str
    price: float  # the sold price; for a Best Offer sale this is usually the asking price, so an upper bound
    currency: str = "USD"
    ended_at: datetime | None = ts(default=None)  # the provider gives a date only
    best_offer: bool = False
    url: str | None = None
    image_url: str | None = None
    condition: str | None = None
    shipping: float | None = None
    junk: bool = False  # looks like a lot, bundle or reprint: kept on record, left out of every price
    fetched_at: datetime = ts(default_factory=utcnow)


class SoldCall(SQLModel, table=True):
    """One request to the sold-prices provider, so spending can be capped and audited."""

    id: int | None = Field(default=None, primary_key=True)
    at: datetime = ts(default_factory=utcnow, index=True)
    card_id: int | None = Field(default=None, index=True)
    kind: str = "manual"  # manual | auto
    cost: int = 0  # requests charged (failed requests are free)
    ok: bool = True
    error: str | None = None
