from functools import lru_cache

from ...config import get_settings
from .base import CardIdentification, CardIdentifier, build_search_query


@lru_cache
def get_identifier() -> CardIdentifier:
    s = get_settings()
    if s.identifier == "claude":
        from .claude import ClaudeIdentifier

        return ClaudeIdentifier(s.anthropic_api_key, s.anthropic_model)
    if s.identifier == "ebay":
        from ..ebay import get_ebay
        from .ebay_image import EbayImageIdentifier

        return EbayImageIdentifier(get_ebay())
    from .mock import MockIdentifier

    return MockIdentifier()


__all__ = ["CardIdentification", "CardIdentifier", "build_search_query", "get_identifier"]
