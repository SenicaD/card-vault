from pathlib import Path

from ..config import get_settings
import re
import statistics
from contextlib import nullcontext
from typing import NamedTuple

from ..models import PriceQuote
from .debuglog import current_purpose
from .ebay import EbayItem, get_ebay
from .grading import grade_sort_key, parse_grade
from .parallels import parse_parallel

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
    return summarize_ex(items)[0]


def summarize_ex(items: list[EbayItem]) -> tuple[dict, list[EbayItem], dict | None]:
    """Low / median / mean / high over the usable listings, plus the listing behind each of the three.

    Outliers are left out first: with 8 or more listings, any price more than 1.5 interquartile
    ranges outside the middle half (the usual box-plot rule) is dropped, so a $2 junk listing or a
    $5,000 one can't drag the numbers. `trimmed` says how many were dropped and `spread` how wide the
    middle half of what remains is, as a share of the median: a big spread means "many different cards".

    price_refs lets the UI link a price to a real listing: low and high are exact, and the median
    points at the listing priced closest to it (the true median can fall between two listings).
    """
    usable = sorted((i for i in items if not _JUNK.search(i.title)), key=lambda i: i.price)
    trimmed, dropped, fences = 0, [], None
    if len(usable) >= 8:
        q1, _, q3 = statistics.quantiles([i.price for i in usable], n=4)
        lo, hi = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
        fences = {"q1": round(q1, 2), "q3": round(q3, 2), "lo": round(lo, 2), "hi": round(hi, 2)}
        kept = [i for i in usable if lo <= i.price <= hi]
        dropped = [i for i in usable if not lo <= i.price <= hi]
        trimmed, usable = len(usable) - len(kept), kept
    if not usable:
        return (dict(sample_size=0, low=None, median=None, mean=None, high=None, price_refs=None, spread=None,
                     trimmed=None), dropped, fences)
    prices = [i.price for i in usable]
    median = round(statistics.median(prices), 2)
    spread = None
    if len(prices) >= 4 and median > 0:
        q1, _, q3 = statistics.quantiles(prices, n=4)
        spread = round((q3 - q1) / median, 2)
    return (dict(
        sample_size=len(prices),
        low=prices[0],
        median=median,
        mean=round(statistics.fmean(prices), 2),
        high=prices[-1],
        price_refs={"low": usable[0].model_dump(), "high": usable[-1].model_dump(),
                    "median": min(usable, key=lambda i: abs(i.price - median)).model_dump()},
        spread=spread,
        trimmed=trimmed,
    ), dropped, fences)


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


def card_image(card) -> bytes | None:
    """The card's saved photo, for eBay image search; None if it has none or the file is gone."""
    if not card.image_path:
        return None
    path = get_settings().image_dir / Path(card.image_path).name
    try:
        return path.read_bytes()
    except OSError:
        return None


MIN_PER_GRADE = 5


async def _top_up_graded(items: list[EbayItem], groups: dict[str, list[EbayItem]], grades: list[str]) -> None:
    """A raw photo mostly matches raw copies, so graded copies of the same card are thin in the image results.

    For each requested graded grade with few listings, search for the card the photo matched,
    named from the matched listings' own titles (not the form fields), plus that grade.
    """
    thin = [g for g in grades if g not in ("Raw", "Unknown") and len(groups.get(g, [])) < MIN_PER_GRADE][:3]
    if not thin:
        return
    from .identify.ebay_image import parse_matches
    base = parse_matches(items).search_query
    if not base:
        return
    seen = {i.item_id for i in items}
    token = current_purpose.set("top-up for thin grades")
    for g in thin:
        try:
            found = await get_ebay().search_active(f"{base} {g}", limit=100)
        except Exception:
            continue
        for i in found:
            if i.item_id not in seen and parse_grade(i.title, i.condition) == g:
                seen.add(i.item_id)
                groups.setdefault(g, []).append(i)
    current_purpose.reset(token)


class Priced(NamedTuple):
    quotes: list[PriceQuote]  # one per grade asked for, in the order asked
    counts: dict[str, int]  # listings eBay showed per grade (every grade, every version of the card)
    comps: list[EbayItem]  # the first 100 listings used for the quotes, best match first
    groups: dict[str, list[EbayItem]]  # every listing found, by grade
    items: list[EbayItem]  # what the search returned, in eBay's order
    source: str  # "ebay-image" or "ebay-active"


def build_quotes(groups: dict[str, list[EbayItem]], grades: list[str], query: str, source: str,
                 parallel: str | None = None, exclude: set[str] = frozenset(),
                 trace=None) -> tuple[list[PriceQuote], list[EbayItem]]:
    """Price each grade from already-fetched listings, leaving out the versions and listings you ruled out.

    parallel limits a card to one colour/print-run version ("Green /99"); exclude holds item ids you
    marked as "not my card". Returns the quotes and the listings they were built from. With a trace,
    what happened to every listing is recorded.
    """
    quotes, used = [], []
    for g in grades:
        full = groups.get(g, [])
        group = [i for i in full if i.item_id not in exclude
                 and (not parallel or parse_parallel(i.title) == parallel)]
        stats, dropped, fences = summarize_ex(group)
        quotes.append(PriceQuote(query=query, source=source, grade=g, parallel=parallel,
                                 top_listings=top_listings(group), **stats))
        used += [i.model_copy(update={"grade": g}) for i in group]
        if trace is not None:
            kept_ids = {i.item_id for i in group}
            out_ids = {i.item_id for i in dropped}
            for i in full:
                v = parse_parallel(i.title)
                if i.item_id in exclude:
                    d = "excluded"
                elif i.item_id not in kept_ids:
                    d = "other_version"
                elif i.item_id in out_ids:
                    d = "outlier"
                elif _JUNK.search(i.title):
                    d = "junk"
                else:
                    d = "used"
                trace.decide(i, g, d, v)
            refs = stats.get("price_refs") or {}
            for role, ref in refs.items():
                trace.role(ref["item_id"], role)
            trace.funnel.append({
                "grade": g, "found": len(full), "excluded": sum(i.item_id in exclude for i in full),
                "other_version": sum(i.item_id not in exclude and i.item_id not in kept_ids for i in full),
                "outliers": len(dropped), "used": stats["sample_size"], "low": stats["low"], "median": stats["median"],
                "high": stats["high"], "spread": stats["spread"], "fences": fences})
    return quotes, used


async def quote_grades(query: str, grades: list[str], image: bytes | None = None,
                       parallel: str | None = None, exclude: set[str] = frozenset(), trace=None) -> Priced:
    """One eBay search, priced separately for each requested grade.

    Asking for more grades costs no extra API calls, because the grade is read from the same results.
    With a photo, eBay's image search is used (falling back to words if it finds nothing).
    Pass a Trace to record how the numbers were reached.
    """
    with (trace.active() if trace is not None else nullcontext()):
        return await _quote_grades(query, grades, image, parallel, exclude, trace)


async def _quote_grades(query, grades, image, parallel, exclude, trace) -> Priced:
    source = "ebay-active"
    items = []
    if image:  # the card's own photo finds the right card far better than words do
        token = current_purpose.set("search by the card photo")
        try:
            items = await get_ebay().search_by_image(image, limit=200)
            source = "ebay-image"
            if not items and trace is not None:
                trace.note("The photo search found no listings, so the words were searched instead.")
        except Exception as e:
            items = []
            if trace is not None:
                trace.note(f"The photo search failed ({e!r}), so the words were searched instead.")
        finally:
            current_purpose.reset(token)
    elif trace is not None:
        trace.note("The card has no photo, so the words were searched.")
    if not items:
        source = "ebay-active"
        token = current_purpose.set("search by the words")
        try:
            items = await get_ebay().search_active(query, limit=200)
        finally:
            current_purpose.reset(token)
    groups = group_by_grade(items)
    if source == "ebay-image":
        await _top_up_graded(items, groups, grades)
    quotes, used = build_quotes(groups, grades, query, source, parallel, exclude, trace)
    counts = {g: len(v) for g, v in sorted(groups.items(), key=lambda kv: grade_sort_key(kv[0]) if kv[0] != "Unknown" else (9, "", 0))}
    if trace is not None:
        trace.mode, trace.query = source, query
        listed = {i.item_id for g in grades for i in groups.get(g, [])}
        everything = list({i.item_id: i for i in [*items, *(i for v in groups.values() for i in v)]}.values())
        for i in everything:
            if i.item_id in listed:
                continue  # already decided in build_quotes
            if _JUNK.search(i.title):
                trace.decide(i, None, "junk")
            else:
                g = parse_grade(i.title, i.condition)
                trace.decide(i, g, "unknown_grade" if g is None else "grade_not_requested", parse_parallel(i.title))
        trace.summary = {"returned": len(items), "after_top_up": len(everything), "mode": source,
                         "junk": sum(1 for i in everything if _JUNK.search(i.title)),
                         "unknown_grade": len(groups.get("Unknown", [])),
                         "grades_found": counts}
        trace.raw = [i.model_dump() for i in everything]
    return Priced(quotes, counts, used[:100], groups, items, source)
