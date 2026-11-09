"""Live eBay client (Browse API for comps, Inventory API for listings).

Written against eBay's public REST docs but NOT yet exercised against the sandbox:
expect to adjust payloads (especially trading-card condition descriptors) on first run.

Auth:
  * Browse (search, search_by_image): application token via client-credentials grant. Only needs
    EBAY_CLIENT_ID / EBAY_CLIENT_SECRET.
  * Inventory (listing): a *user* access token with the sell.inventory scope, obtained via
    eBay's authorization-code consent flow. Put it in EBAY_USER_TOKEN for now; a real app
    would store a refresh token and mint access tokens as needed.

Pricing caveat: Browse returns ACTIVE listings (asking prices), which skew high.
Sold comps need the Marketplace Insights API, which requires eBay approval.
"""
import base64
import time

import httpx

from ...config import Settings
from .base import EbayItem, ListingRequest, ListingResult

_HOSTS = {"sandbox": "https://api.sandbox.ebay.com", "production": "https://api.ebay.com"}
_WEB = {"sandbox": "https://sandbox.ebay.com", "production": "https://www.ebay.com"}


class EbayApiError(RuntimeError):
    pass


class LiveEbayClient:
    mode = "live"

    def __init__(self, s: Settings):
        if not (s.ebay_client_id and s.ebay_client_secret):
            raise ValueError("EBAY_MODE=live requires EBAY_CLIENT_ID and EBAY_CLIENT_SECRET")
        self.s = s
        self.host = _HOSTS[s.ebay_env]
        self.web = _WEB[s.ebay_env]
        self._app_token: str | None = None
        self._app_token_exp = 0.0

    async def _application_token(self, client: httpx.AsyncClient) -> str:
        if self._app_token and time.time() < self._app_token_exp - 60:
            return self._app_token
        basic = base64.b64encode(f"{self.s.ebay_client_id}:{self.s.ebay_client_secret}".encode()).decode()
        r = await client.post(
            f"{self.host}/identity/v1/oauth2/token",
            headers={"Authorization": f"Basic {basic}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials",
                  "scope": "https://api.ebay.com/oauth/api_scope"},
        )
        if r.status_code != 200:
            raise EbayApiError(f"token request failed: {r.status_code} {r.text}")
        body = r.json()
        self._app_token = body["access_token"]
        self._app_token_exp = time.time() + body.get("expires_in", 7200)
        return self._app_token

    async def search_active(self, query: str, limit: int = 50) -> list[EbayItem]:
        async with httpx.AsyncClient(timeout=20) as client:
            token = await self._application_token(client)
            r = await client.get(
                f"{self.host}/buy/browse/v1/item_summary/search",
                params={"q": query, "limit": min(limit, 200),
                        "filter": "buyingOptions:{FIXED_PRICE}"},
                headers={"Authorization": f"Bearer {token}",
                         "X-EBAY-C-MARKETPLACE-ID": self.s.ebay_marketplace_id},
            )
        if r.status_code != 200:
            raise EbayApiError(f"browse search failed: {r.status_code} {r.text}")
        return _items(r.json())

    async def search_by_image(self, image_bytes: bytes, limit: int = 50) -> list[EbayItem]:
        """Browse API searchByImage: listings that look like the photo, best match first."""
        async with httpx.AsyncClient(timeout=30) as client:
            token = await self._application_token(client)
            r = await client.post(
                f"{self.host}/buy/browse/v1/item_summary/search_by_image",
                params={"limit": min(limit, 200), "filter": "buyingOptions:{FIXED_PRICE}"},
                json={"image": base64.b64encode(image_bytes).decode()},
                headers={"Authorization": f"Bearer {token}",
                         "X-EBAY-C-MARKETPLACE-ID": self.s.ebay_marketplace_id},
            )
        if r.status_code != 200:
            raise EbayApiError(f"image search failed: {r.status_code} {r.text}")
        return _items(r.json())

    async def create_listing(self, req: ListingRequest, publish: bool = True) -> ListingResult:
        s = self.s
        missing = [k for k in ("ebay_user_token", "ebay_fulfillment_policy_id", "ebay_payment_policy_id",
                               "ebay_return_policy_id", "ebay_merchant_location_key") if not getattr(s, k)]
        if missing:
            raise EbayApiError(f"listing requires settings: {', '.join(m.upper() for m in missing)}")
        if not req.image_urls:
            raise EbayApiError("eBay requires at least one public https image URL")

        headers = {"Authorization": f"Bearer {s.ebay_user_token}",
                   "Content-Type": "application/json", "Content-Language": "en-US"}
        # Trading-card categories: LIKE_NEW -> Graded (2750), USED_VERY_GOOD -> Ungraded (4000).
        # eBay also requires conditionDescriptors (grader, grade / card condition); TODO: map
        # req.grade and req.condition to descriptor IDs from getItemConditionPolicies.
        item = {
            "availability": {"shipToLocationAvailability": {"quantity": req.quantity}},
            "condition": "LIKE_NEW" if req.graded else "USED_VERY_GOOD",
            "product": {"title": req.title[:80], "description": req.description,
                        "imageUrls": req.image_urls},
        }
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.put(f"{self.host}/sell/inventory/v1/inventory_item/{req.sku}",
                                 json=item, headers=headers)
            if r.status_code not in (200, 201, 204):
                raise EbayApiError(f"inventory_item failed: {r.status_code} {r.text}")

            offer = {
                "sku": req.sku,
                "marketplaceId": s.ebay_marketplace_id,
                "format": "FIXED_PRICE",
                "availableQuantity": req.quantity,
                "categoryId": req.category_id,
                "listingDescription": req.description,
                "pricingSummary": {"price": {"value": f"{req.price:.2f}", "currency": req.currency}},
                "listingPolicies": {"fulfillmentPolicyId": s.ebay_fulfillment_policy_id,
                                    "paymentPolicyId": s.ebay_payment_policy_id,
                                    "returnPolicyId": s.ebay_return_policy_id},
                "merchantLocationKey": s.ebay_merchant_location_key,
            }
            r = await client.post(f"{self.host}/sell/inventory/v1/offer", json=offer, headers=headers)
            if r.status_code not in (200, 201):
                raise EbayApiError(f"create offer failed: {r.status_code} {r.text}")
            offer_id = r.json()["offerId"]

            listing_id = None
            if publish:
                r = await client.post(f"{self.host}/sell/inventory/v1/offer/{offer_id}/publish",
                                      headers=headers)
                if r.status_code != 200:
                    raise EbayApiError(f"publish failed: {r.status_code} {r.text}")
                listing_id = r.json().get("listingId")

        return ListingResult(
            sku=req.sku, offer_id=offer_id, listing_id=listing_id,
            url=f"{self.web}/itm/{listing_id}" if listing_id else None,
            status="published" if listing_id else "draft",
        )


def _items(body: dict) -> list[EbayItem]:
    """Browse API itemSummaries -> EbayItem, skipping anything without a fixed price."""
    out = []
    for it in body.get("itemSummaries", []):
        price = it.get("price") or {}
        if "value" not in price:
            continue
        out.append(EbayItem(
            item_id=it["itemId"],
            title=it.get("title", ""),
            price=float(price["value"]),
            currency=price.get("currency", "USD"),
            condition=it.get("condition"),
            url=it.get("itemWebUrl"),
            image_url=(it.get("image") or {}).get("imageUrl"),
        ))
    return out
