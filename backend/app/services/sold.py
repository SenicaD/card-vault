"""Sold prices from a provider that reports what eBay items actually sold for.

eBay's own sold-data API is closed to us, so this uses SoldComps (sold-comps.com), which returns sold
listings with their sale price and date. Switched on by setting SOLDCOMPS_API_KEY.
Each search costs one request against a monthly budget, which is enforced here before any call is made.
"""
from datetime import date, datetime, timedelta, UTC
from typing import Any

import httpx
from pydantic import BaseModel

from ..config import Settings, get_settings


class SoldItem(BaseModel):
    item_id: str
    title: str
    price: float
    currency: str = "USD"
    ended_at: date | None = None
    best_offer: bool = False
    url: str | None = None
    image_url: str | None = None


class SoldApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status, self.code = status, code


def _price(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


class SoldCompsClient:
    name = "soldcomps"

    def __init__(self, s: Settings):
        self.s = s
        self.usage: dict[str, str] = {}  # the provider's own usage headers from the latest call

    async def search(self, keyword: str, count: int = 240) -> list[SoldItem]:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(f"{self.s.soldcomps_base_url}/v1/scrape",
                                 params={"keyword": keyword, "count": count, "sold": "true", "ebaySite": "ebay.com"},
                                 headers={"Authorization": f"Bearer {self.s.soldcomps_api_key}"})
        self.usage = {k: v for k, v in r.headers.items() if k.lower().startswith(("x-usage", "x-credit", "x-plan"))}
        if r.status_code != 200:
            try:
                body = r.json()
            except ValueError:
                body = {}
            code = body.get("code") or body.get("error")
            raise SoldApiError(f"{r.status_code} {code or ''} {str(body.get('message') or body.get('error') or r.text)[:200]}".strip(),
                               r.status_code, code if isinstance(code, str) else None)
        return parse_items(r.json())

    async def account(self) -> dict[str, Any]:
        """The provider's own view of the plan and what has been used (a free call)."""
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"{self.s.soldcomps_base_url}/usage",
                                 headers={"Authorization": f"Bearer {self.s.soldcomps_api_key}"})
        if r.status_code != 200:
            raise SoldApiError(f"usage lookup failed: {r.status_code}", r.status_code)
        return r.json()


def parse_items(body: dict) -> list[SoldItem]:
    """SoldComps /v1/scrape response -> SoldItem. Anything without a US-dollar price is skipped."""
    out = []
    for it in body.get("items", []):
        price = _price(it.get("soldPrice"))
        if price is None or price <= 0 or (it.get("soldCurrency") or "USD") != "USD" or not it.get("itemId"):
            continue
        try:
            ended = date.fromisoformat(it["endedAt"]) if it.get("endedAt") else None
        except ValueError:
            ended = None
        out.append(SoldItem(item_id=str(it["itemId"]), title=it.get("title") or "", price=price, ended_at=ended,
                            best_offer=bool(it.get("bestOfferAccepted")), url=it.get("url"),
                            image_url=it.get("thumbnailUrl")))
    return out


class MockSoldClient:
    """Demo data for mock mode: sold prices a little under the mock asking prices."""

    name = "mock"
    usage: dict[str, str] = {}

    async def search(self, keyword: str, count: int = 240) -> list[SoldItem]:
        from .ebay import get_ebay

        found = await get_ebay().search_active(keyword, limit=60)
        today = datetime.now(UTC).date()
        return [SoldItem(item_id=f"sold-{i.item_id}", title=i.title, price=round(i.price * 0.85, 2),
                         ended_at=today - timedelta(days=(n * 3) % 80), url=i.url, image_url=i.image_url)
                for n, i in enumerate(found)]

    async def account(self) -> dict[str, Any]:
        return {"plan": "mock"}


_client: Any = None


def get_sold() -> Any | None:
    """The configured sold-prices provider, or None when the feature is off."""
    global _client
    s = get_settings()
    if s.soldcomps_api_key:
        if not isinstance(_client, SoldCompsClient):
            _client = SoldCompsClient(s)
    elif s.ebay_mode == "mock":
        if not isinstance(_client, MockSoldClient):
            _client = MockSoldClient()
    else:
        _client = None
    return _client
