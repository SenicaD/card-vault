"""Mock eBay client: deterministic, plausible-looking data, no network.

Prices are seeded from the query string, so the same card always gets the same comps.
Swap in LiveEbayClient by setting EBAY_MODE=live; the interface is identical.
"""
import hashlib
import random
import uuid

from .base import EbayItem, ListingRequest, ListingResult

_CONDITIONS = ["Ungraded", "Ungraded", "Ungraded", "Graded"]
_TITLE_NOISE = ["", " NM", " 🔥", " Rare", " Invest", " Mint", " Look!", " L@@K", " Sharp corners"]


def _base_value(query: str, rng: random.Random) -> float:
    q = query.lower()
    base = rng.uniform(2, 40)
    if any(k in q for k in ("psa 10", "bgs 10", "cgc 10")):
        base *= rng.uniform(4, 10)
    elif any(k in q for k in ("psa", "bgs", "cgc", "sgc")):
        base *= rng.uniform(2, 4)
    if any(k in q for k in ("rookie", "1st edition", "base set", "refractor", "alternate art", "secret")):
        base *= rng.uniform(2, 6)
    if any(k in q for k in ("charizard", "griffey", "brady", "wembanyama", "one ring", "blue-eyes")):
        base *= rng.uniform(3, 8)
    return round(base, 2)


class MockEbayClient:
    mode = "mock"

    def __init__(self) -> None:
        self.listings: dict[str, ListingResult] = {}

    async def search_active(self, query: str, limit: int = 50) -> list[EbayItem]:
        seed = int(hashlib.sha256(query.lower().encode()).hexdigest(), 16)
        rng = random.Random(seed)
        base = _base_value(query, rng)
        n = min(limit, rng.randint(6, 30))
        items = []
        for i in range(n):
            # Lognormal spread around the base, like real asking prices (long right tail)
            price = round(max(0.99, base * rng.lognormvariate(0, 0.35)), 2)
            items.append(EbayItem(
                item_id=f"v1|mock{seed % 10**9}{i:03d}|0",
                title=(query + rng.choice(_TITLE_NOISE))[:80],
                price=price,
                condition=rng.choice(_CONDITIONS),
                url=f"https://www.ebay.com/sch/i.html?_nkw={query.replace(' ', '+')}",
            ))
        return items

    async def search_by_image(self, image_bytes: bytes, limit: int = 50) -> list[EbayItem]:
        """Same photo -> same card as the mock identifier, returned as messy listing titles."""
        from ..identify.base import build_search_query
        from ..identify.mock import sample_for

        query = build_search_query(sample_for(image_bytes))
        rng = random.Random(int(hashlib.sha256(image_bytes).hexdigest(), 16))
        items = await self.search_active(query, limit)
        for it in items:
            words = query.split()
            if rng.random() < 0.3:  # sellers drop words and shout; a few listings are a different card
                words.pop(rng.randrange(len(words)))
            it.title = (" ".join(words) + rng.choice([" RC", " 🔥", " Invest", "", " Sharp"]))[:80]
        if len(items) > 4:
            items[-1].title, items[-2].title = "Random card lot 50 cards", "Different Player 2019 Topps #12"
        return items

    async def create_listing(self, req: ListingRequest, publish: bool = True) -> ListingResult:
        offer_id = f"mock-offer-{uuid.uuid4().hex[:10]}"
        listing_id = f"mock-{uuid.uuid4().int % 10**12}" if publish else None
        result = ListingResult(
            sku=req.sku,
            offer_id=offer_id,
            listing_id=listing_id,
            url=f"https://www.ebay.com/itm/{listing_id}" if listing_id else None,
            status="published" if publish else "draft",
        )
        self.listings[req.sku] = result
        return result
