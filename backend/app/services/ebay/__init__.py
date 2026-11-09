from functools import lru_cache

from ...config import get_settings
from .base import EbayClient, EbayItem, ListingRequest, ListingResult


@lru_cache
def get_ebay() -> EbayClient:
    s = get_settings()
    if s.ebay_mode == "live":
        from .live import LiveEbayClient

        return LiveEbayClient(s)
    from .mock import MockEbayClient

    return MockEbayClient()


__all__ = ["EbayClient", "EbayItem", "ListingRequest", "ListingResult", "get_ebay"]
