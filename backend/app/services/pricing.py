import re
import statistics

from ..models import PriceQuote
from .ebay import EbayItem, get_ebay
from .grading import grade_sort_key, parse_grade

# Titles that are clearly not a single copy of the card
_JUNK = re.compile(r"\b(lot|lots|bundle|set of|choose|pick|you pick|reprint|proxy|custom|digital)\b", re.I)


def top_listings(items: list[EbayItem], n: int = 4) -> list[dict]:
    """The first n real single-card listings in eBay's own best-match order (not cheapest-first)."""
    seen, out = set(), []
    for i in items:
        if i.price <= 0 or i.item_id in seen or _JUNK.search(i.title):
            continue
        seen.add(i.item_id)
        out.append(i.model_dump())
        if len(out) == n:
            break
    return out


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
    return (PriceQuote(query=query, source="ebay-active", top_listings=top_listings(comps),
                       **summarize(comps)),
            sorted(comps, key=lambda c: c.price)[:25])


def group_by_grade(items: list[EbayItem]) -> dict[str, list[EbayItem]]:
    """Split listings by grade, keeping eBay's best-match order inside each group.

    Listings whose title doesn't state a grade go under "Unknown" and are never priced as any grade.
    """
    groups: dict[str, list[EbayItem]] = {}
    for i in items:
        if _JUNK.search(i.title):
            continue
        groups.setdefault(parse_grade(i.title, i.condition) or "Unknown", []).append(i)
    return groups


async def quote_grades(query: str, grades: list[str]) -> tuple[list[PriceQuote], dict[str, int], list[EbayItem]]:
    """One eBay search, priced separately for each requested grade.

    Returns (a quote per grade in the order asked, how many listings eBay showed for every grade,
    the cheapest 25 listings across the requested grades). Asking for more grades costs no extra
    API calls, because the grade is read from the same results.
    """
    items = await get_ebay().search_active(query, limit=200)
    groups = group_by_grade(items)
    quotes = []
    for g in grades:
        group = groups.get(g, [])
        quotes.append(PriceQuote(query=query, source="ebay-active", grade=g,
                                 top_listings=top_listings(group), **summarize(group)))
    wanted = [i for g in grades for i in groups.get(g, [])]
    counts = {g: len(v) for g, v in sorted(groups.items(), key=lambda kv: grade_sort_key(kv[0]) if kv[0] != "Unknown" else (9, "", 0))}
    return quotes, counts, sorted(wanted, key=lambda c: c.price)[:25]
