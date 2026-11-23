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
from .identity import CardIdentity, Who, fits_identity, same_card
from .parallels import parse_parallel
from .lookalike import LOOKALIKE_BORDERLINE, LOOKALIKE_MIN_CONFIDENCE, LOOKALIKE_MIN_MATCHES, LOOKALIKE_STRONG_MATCHES
from .lookalike import compare as compare_pictures

# Titles that are clearly not a single copy of the card
_JUNK = re.compile(r"\b(lot|lots|bundle|set of|choose|pick|you pick|reprint|proxy|custom|digital)\b", re.I)


def apply_asking(card, median: float | None, spread: float | None, prices: list[float] | None = None,
                 app_settings=None) -> None:
    """Set the card's asking estimate from a quote, unless its prices spread too widely to trust.

    Above MAX_PRICE_SPREAD the card is flagged and gets no asking value; otherwise the flag is cleared. With
    app_settings, the card's own wide_spread_action (else the saved default) can value it instead: at the median of
    the price_window most expensive, middle or cheapest asking listings, after outliers are left out (see
    window_value). spread_rule records which rule did.
    """
    card.spread_rule = None
    if median is not None and spread is not None and spread > get_settings().max_price_spread:
        rule = (card.wide_spread_action or app_settings.wide_spread_action) if app_settings is not None else "flag"
        value = None
        if rule in ("highest_group", "median", "lowest_group") and prices is not None:
            value = window_value(prices, rule, app_settings.price_window)
        if value is not None:
            card.wide_spread, card.est_value, card.spread_rule = False, value, rule
        else:
            card.wide_spread, card.est_value = True, None
    else:
        card.wide_spread, card.est_value = False, median


def window_value(prices: list[float], rule: str, n: int) -> float | None:
    """The median of a window of n prices, after outliers are left out: the n most expensive ("highest_group"), the
    n cheapest ("lowest_group") or the n in the middle of the price order ("median"; it leans one price toward the
    cheaper side when it can't be centred exactly). With n or fewer prices left, the median of all of them; with
    none, None."""
    fences = outlier_fences(prices)
    p = sorted(x for x in prices if fences is None or fences[2] <= x <= fences[3])
    if not p:
        return None
    if len(p) > n:
        s = (len(p) - n) // 2
        p = p[-n:] if rule == "highest_group" else p[:n] if rule == "lowest_group" else p[s:s + n]
    return round(statistics.median(p), 2)


def asking_listings(items: list, parallel: str | None, exclude: set[str], ident: CardIdentity | None) -> list:
    """The listings of the card's own grade that price its asking value, before the outlier trim: not ruled out,
    of its version when one is set, of its card code and redemption status, and not junk."""
    return [i for i in items if i.item_id not in exclude and (not parallel or parse_parallel(i.title) == parallel)
            and fits_identity(i.title, ident) and not _JUNK.search(i.title)]


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


def outlier_fences(prices: list[float]) -> tuple[float, float, float, float] | None:
    """Q1, Q3 and the box-plot fences lo and hi of the prices: a price outside lo..hi, more than 1.5 interquartile
    ranges outside the middle half, is an outlier. None with fewer than 8 prices, when nothing is left out."""
    if len(prices) < 8:
        return None
    q1, _, q3 = statistics.quantiles(prices, n=4)
    return q1, q3, q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)


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
    if (box := outlier_fences([i.price for i in usable])) is not None:
        q1, q3, lo, hi = box
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


CLUSTER_GAP_RATIO = 2.5  # split where the next price is at least this many times the one before it
CLUSTER_MIN_SIZE = 2  # a smaller group is a stray: left out, not shown and not counted
CLUSTER_MAX = 4  # the most groups returned
CLUSTER_MIN_PRICES = 4  # with fewer usable prices there are no groups


class PriceCluster(NamedTuple):
    median: float  # rounded to 2 places
    low: float
    high: float
    count: int
    members: list[int]  # positions in the input list


def price_clusters(prices: list[float], min_size: int = CLUSTER_MIN_SIZE) -> list[PriceCluster]:
    """Split listing prices into groups, cheapest first, so a card mixing several versions shows each one's price.

    Prices of 0 or less are ignored; with fewer than CLUSTER_MIN_PRICES left there are no groups. The sorted prices
    are split wherever a price is at least CLUSTER_GAP_RATIO times the one before it, and groups of fewer than
    min_size listings are dropped as strays. While more than CLUSTER_MAX groups remain, the two neighbouring
    groups closest in price (the higher one's low divided by the lower one's high; the cheaper pair on a tie) are
    merged. Fewer than 2 groups means there is nothing to tell apart, so [] is returned.
    """
    order = sorted((i for i, p in enumerate(prices) if p > 0), key=lambda i: prices[i])
    if len(order) < CLUSTER_MIN_PRICES:
        return []
    groups = [[order[0]]]
    for a, b in zip(order, order[1:]):
        if prices[b] >= CLUSTER_GAP_RATIO * prices[a]:
            groups.append([b])
        else:
            groups[-1].append(b)
    groups = [g for g in groups if len(g) >= min_size]
    while len(groups) > CLUSTER_MAX:
        k = min(range(len(groups) - 1), key=lambda k: prices[groups[k + 1][0]] / prices[groups[k][-1]])
        groups[k:k + 2] = [groups[k] + groups[k + 1]]
    if len(groups) < 2:
        return []
    return [PriceCluster(round(statistics.median(prices[i] for i in g), 2), prices[g[0]], prices[g[-1]], len(g), g)
            for g in groups]


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


LOOKALIKE_MAX_IMAGES = 40  # listing pictures fetched per lookup


async def _not_alike(image: bytes, items: list[EbayItem], groups: dict[str, list[EbayItem]], grades: list[str],
                     parallel: str | None, exclude: set[str], trace=None,
                     ident: CardIdentity | None = None) -> tuple[list[tuple[EbayItem, str]], str | None]:
    """The listings of the requested grades whose picture clearly doesn't look like the card's photo, with their
    grade, and how the pictures were compared ("clip", or None when nothing was: no pictures, or no CLIP).

    See _unlike for the rule. Only the first LOOKALIKE_MAX_IMAGES pictures in eBay's order are compared. Listings with no picture, past that limit or whose picture didn't load are kept, and so is every
    listing of a grade the rule would leave with nothing to price.
    """
    in_items = {i.item_id for i in items}
    wanted = {i.item_id for g in grades for i in groups.get(g, [])}
    # eBay's best-match order: the search results first, then the listings the top-up searches added
    walk = [i for i in items if i.item_id in wanted] + [i for g in grades for i in groups.get(g, []) if i.item_id not in in_items]
    urls = list(dict.fromkeys(i.image_url for i in walk if i.image_url))[:LOOKALIKE_MAX_IMAGES]
    if not urls:
        return [], None
    try:
        look = await compare_pictures(image, urls)
    except Exception as e:
        if trace is not None:
            trace.note(f"The photo comparison failed ({e!r}), so no listing was left out for not looking like the photo.")
        return [], None
    scores = look.scores
    if trace is not None:
        if look.note:
            trace.note(look.note)
        for i in walk:
            if i.image_url in scores:
                trace.looks[i.item_id] = {"s": scores[i.image_url], "m": look.matches.get(i.image_url)}
    priceable = lambda i: (i.item_id not in exclude and (not parallel or parse_parallel(i.title) == parallel)  # noqa: E731
                           and fits_identity(i.title, ident))
    out = []
    for g in grades:
        group = groups.get(g, [])
        drop = {i.item_id for i in group if i.image_url in scores and _unlike(scores[i.image_url], look.matches.get(i.image_url))}
        if any(map(priceable, group)) and not any(priceable(i) for i in group if i.item_id not in drop):
            if trace is not None and drop:
                trace.note(f"{g}: the look-alike filter was skipped, because leaving out the listings that don't "
                           "look like the photo would have left nothing to price.")
            continue
        out += [(i, g) for i in group if i.item_id in drop]
    return out, look.method


def _unlike(confidence: int, matches: int | None) -> bool:
    """CLIP's verdict, with the feature check as a second opinion below LOOKALIKE_BORDERLINE.

    Clearly low (below LOOKALIKE_MIN_CONFIDENCE) is left out unless the printed design matches strongly
    (LOOKALIKE_STRONG_MATCHES); borderline is left out with fewer than LOOKALIKE_MIN_MATCHES points, and kept if the
    check couldn't run.
    """
    if confidence < LOOKALIKE_MIN_CONFIDENCE:
        return matches is None or matches < LOOKALIKE_STRONG_MATCHES
    return confidence < LOOKALIKE_BORDERLINE and matches is not None and matches < LOOKALIKE_MIN_MATCHES


class Priced(NamedTuple):
    quotes: list[PriceQuote]  # one per grade asked for, in the order asked
    counts: dict[str, int]  # listings eBay showed per grade (every grade, every version of the card)
    comps: list[EbayItem]  # the first 100 listings used for the quotes, best match first
    groups: dict[str, list[EbayItem]]  # every listing found, by grade
    items: list[EbayItem]  # what the search returned, in eBay's order
    source: str  # "ebay-image" or "ebay-active"
    not_alike: frozenset[str] = frozenset()  # item ids left out because their picture doesn't look like the photo


def build_quotes(groups: dict[str, list[EbayItem]], grades: list[str], query: str, source: str,
                 parallel: str | None = None, exclude: set[str] = frozenset(),
                 trace=None, ident: CardIdentity | None = None) -> tuple[list[PriceQuote], list[EbayItem]]:
    """Price each grade from already-fetched listings, leaving out the versions and listings you ruled out.

    parallel limits a card to one colour/print-run version ("Green /99"); exclude holds item ids you
    marked as "not my card"; ident leaves out listings for another card code or redemption status.
    Returns the quotes and the listings they were built from. With a trace, what happened to every
    listing is recorded.
    """
    quotes, used = [], []
    other_identity = 0
    for g in grades:
        full = groups.get(g, [])
        fits = {i.item_id for i in full if i.item_id not in exclude and fits_identity(i.title, ident)}
        group = [i for i in full if i.item_id in fits and (not parallel or parse_parallel(i.title) == parallel)]
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
                elif i.item_id not in fits:
                    d = "other_identity"
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
            dropped_identity = sum(i.item_id not in exclude and i.item_id not in fits for i in full)
            other_identity += dropped_identity
            trace.funnel.append({
                "grade": g, "found": len(full), "excluded": sum(i.item_id in exclude for i in full),
                "other_identity": dropped_identity,
                "other_version": sum(i.item_id in fits and i.item_id not in kept_ids for i in full),
                "outliers": len(dropped), "used": stats["sample_size"], "low": stats["low"], "median": stats["median"],
                "high": stats["high"], "spread": stats["spread"], "fences": fences})
    if other_identity:
        code = f"shows a different card code than {ident.code.upper()} or " if ident.code else ""
        rule = "isn't for a redemption card" if ident.redemption else "is for a redemption card, which this card isn't"
        trace.note(f"{other_identity} listings were left out because their title {code}{rule}.")
    return quotes, used


async def quote_grades(query: str, grades: list[str], image: bytes | None = None,
                       parallel: str | None = None, exclude: set[str] = frozenset(), trace=None,
                       who: Who | None = None, ident: CardIdentity | None = None) -> Priced:
    """One eBay search, priced separately for each requested grade.

    Asking for more grades costs no extra API calls, because the grade is read from the same results.
    With a photo, eBay's image search is used (falling back to words if it finds nothing).
    Pass a Trace to record how the numbers were reached.
    """
    with (trace.active() if trace is not None else nullcontext()):
        return await _quote_grades(query, grades, image, parallel, exclude, trace, who, ident)


async def _quote_grades(query, grades, image, parallel, exclude, trace, who=None, ident=None) -> Priced:
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
    rejected: list[EbayItem] = []  # look-alikes: another player, year or card number
    if who is not None:
        for g in list(groups):
            groups[g] = [i for i in groups[g] if same_card(i.title, who) or rejected.append(i)]
        items = [i for i in items if same_card(i.title, who)]
    if rejected and trace is not None:
        trace.note(f"{len(rejected)} look-alike listings were left out before pricing: another player, year or card number.")
    unlike: list[tuple[EbayItem, str]] = []  # pictures that clearly don't look like the card's photo
    method = None
    if image and ident is not None and ident.redemption:  # every redemption shares the same generic back
        if trace is not None:
            trace.note("Photo matching was skipped because this is a redemption card: redemption cards share a generic "
                       "back, so pictures can't tell them apart. Listings were matched on card number and redemption "
                       "status instead.")
    elif image:
        unlike, method = await _not_alike(image, items, groups, grades, parallel, exclude, trace, ident)
    gone = {i.item_id for i, _ in unlike}
    if gone:
        for g in grades:
            if g in groups:
                groups[g] = [i for i in groups[g] if i.item_id not in gone]
        items = [i for i in items if i.item_id not in gone]
        if trace is not None:
            trace.note(f"{len(unlike)} listings were left out because their picture doesn't look like the card's photo "
                       f"(CLIP confidence below {LOOKALIKE_MIN_CONFIDENCE} without {LOOKALIKE_STRONG_MATCHES}+ matching "
                       f"feature points, or below {LOOKALIKE_BORDERLINE} with fewer than {LOOKALIKE_MIN_MATCHES}).")
    quotes, used = build_quotes(groups, grades, query, source, parallel, exclude, trace, ident)
    counts = {g: len(v) for g, v in sorted(groups.items(), key=lambda kv: grade_sort_key(kv[0]) if kv[0] != "Unknown" else (9, "", 0))}
    if trace is not None:
        trace.mode, trace.query = source, query
        for f in trace.funnel:  # left out before build_quotes, so its own counts don't include them
            f["not_alike"] = sum(g == f["grade"] for _, g in unlike)
        listed = {i.item_id for g in grades for i in groups.get(g, [])}
        everything = list({i.item_id: i for i in [*items, *(i for v in groups.values() for i in v)]}.values())
        for i in rejected:
            trace.decide(i, parse_grade(i.title, i.condition), "other_card", parse_parallel(i.title))
        for i, g in unlike:
            trace.decide(i, g, "not_alike", parse_parallel(i.title))
        for i in everything:
            if i.item_id in listed:
                continue  # already decided in build_quotes
            if _JUNK.search(i.title):
                trace.decide(i, None, "junk")
            else:
                g = parse_grade(i.title, i.condition)
                trace.decide(i, g, "unknown_grade" if g is None else "grade_not_requested", parse_parallel(i.title))
        trace.summary = {"returned": len(items) + len(rejected) + len(unlike),
                         "after_top_up": len(everything) + len(rejected) + len(unlike),
                         "other_card": len(rejected), "not_alike": len(unlike), "lookalike": method, "mode": source,
                         "other_identity": sum(f["other_identity"] for f in trace.funnel),
                         "junk": sum(1 for i in everything if _JUNK.search(i.title)),
                         "unknown_grade": len(groups.get("Unknown", [])),
                         "grades_found": counts}
        trace.raw = [i.model_dump() for i in everything]
    return Priced(quotes, counts, used[:100], groups, items, source, frozenset(gone))
