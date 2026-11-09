import re
import statistics

from ..models import PriceQuote
from .ebay import EbayItem, get_ebay

# Titles that are clearly not a single copy of the card
_JUNK = re.compile(r"\b(lot|lots|bundle|set of|choose|pick|you pick|reprint|proxy|custom|digital)\b", re.I)


def summarize(items: list[EbayItem]) -> dict:
    prices = sorted(i.price for i in items if not _JUNK.search(i.title))
    if len(prices) >= 10:  # trim 10% each side to blunt outliers
        k = len(prices) // 10
        prices = prices[k:-k]
    if not prices:
        return dict(sample_size=0, low=None, median=None, mean=None, high=None)
    return dict(
        sample_size=len(prices),
        low=prices[0],
        median=round(statistics.median(prices), 2),
        mean=round(statistics.fmean(prices), 2),
        high=prices[-1],
    )


async def quote(query: str) -> tuple[PriceQuote, list[EbayItem]]:
    """Search active eBay listings and summarise them. Returns the quote and the cheapest 25 comps."""
    comps = await get_ebay().search_active(query)
    return (PriceQuote(query=query, source="ebay-active", **summarize(comps)),
            sorted(comps, key=lambda c: c.price)[:25])
