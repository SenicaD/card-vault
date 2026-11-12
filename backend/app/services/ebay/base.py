from typing import Protocol

from pydantic import BaseModel


class EbayItem(BaseModel):
    item_id: str
    title: str
    price: float
    currency: str = "USD"
    condition: str | None = None
    url: str | None = None
    image_url: str | None = None
    grade: str | None = None  # filled in when listings are grouped by grade


class ListingRequest(BaseModel):
    sku: str
    title: str  # eBay max 80 chars
    description: str
    price: float
    currency: str = "USD"
    quantity: int = 1
    category_id: str  # 261328 sports singles, 183454 CCG singles
    graded: bool = False
    grade: str | None = None
    condition: str | None = None
    image_urls: list[str] = []  # must be public https URLs for the live API


class ListingResult(BaseModel):
    sku: str
    offer_id: str
    listing_id: str | None
    url: str | None
    status: str  # published | draft


class EbayClient(Protocol):
    mode: str

    async def search_active(self, query: str, limit: int = 50) -> list[EbayItem]: ...

    async def search_by_image(self, image_bytes: bytes, limit: int = 50) -> list[EbayItem]: ...

    async def get_item_status(self, item_id: str) -> str:
        """'active' if the listing is still up, 'out_of_stock' if it sold out, 'not_found' if it is gone.
        Raises when eBay can't say (rate limit, outage), so the caller can try again later."""
        ...

    async def create_listing(self, req: ListingRequest, publish: bool = True) -> ListingResult: ...
