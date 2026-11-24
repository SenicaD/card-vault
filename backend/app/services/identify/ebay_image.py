"""Identify a card by eBay's image search: find listings that look like the photo, then read
the card details out of what their titles agree on.

No AI key needed, and the matched listings double as price comps. The weak spots are that
visually identical cards (base vs. colour parallels, raw vs. graded) can be mixed together,
and titles are free text, so the parsed fields are a best guess for the user to check.
"""
import re
from collections import Counter

from ...models import CardCategory
from ..ebay import EbayClient, EbayItem
from ..identity import split_player
from .base import CardIdentification

_JUNK = re.compile(r"\b(lot|lots|bundle|set of|choose|pick|you pick|reprint|proxy|custom|digital)\b", re.I)
_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)(?:-(\d{2}))?\b")
_GRADE = re.compile(r"\b(PSA|BGS|CGC|SGC|BCCG|Beckett)\s*(10|[1-9](?:\.5)?)\b", re.I)
_NUMBER = re.compile(r"(?:#\s?([A-Z]{0,4}-?\d{1,4}[A-Z]?)\b|\b(\d{1,3}/\d{2,3})\b|\b([A-Z]{2,5}-[A-Z]{0,2}\d{3})\b)")
# All-letter codes like "CRA-JMI" (set prefix + player initials), only tried when a title has no
# _NUMBER match. Not inside longer hyphenated runs ("YU-GI-OH") and not condition labels ("NM-MT").
_LETTER_CODE = re.compile(r"(?<![\w-])(?!(?:NM-MT|EX-MT|VG-EX|EX-NM|GEM-MT|ONE-OFF)(?![\w-]))([A-Z]{2,5}-[A-Z]{2,3})(?![\w-])")
_AUTO = re.compile(r"\b(auto|autograph|autographed|rpa)\b", re.I)
_REDEMPTION = re.compile(r"\bredemption\b", re.I)
# Words that say nothing about which card it is
_NOISE = {
    "rc", "rookie", "card", "cards", "mint", "nm", "near", "invest", "hot", "rare", "look", "l@@k", "wow",
    "sharp", "gem", "mt", "nice", "new", "free", "shipping", "ship", "graded", "raw", "ungraded",
    "the", "and", "of", "a", "-", "|", "/", "&", "+", "🔥", "📈", "💎", "!", "!!", "w/", "with", "read",
}
_VARIANTS = [
    "1st edition", "first edition", "shadowless", "unlimited", "alternate art", "alt art", "full art",
    "secret rare", "reverse holo", "holo", "foil", "borderless", "showcase", "extended art",
    "refractor", "silver", "gold", "prizm", "auto", "autograph", "patch", "numbered", "short print", "sp",
    "rookie", "rc",
]
_SETS = [
    "topps chrome", "topps flagship", "bowman chrome", "bowman", "topps", "panini prizm", "prizm", "donruss optic", "optic",
    "donruss", "select", "mosaic", "national treasures", "upper deck", "fleer", "score", "hoops",
    "base set", "jungle", "fossil", "team rocket", "evolving skies", "celebrations", "151",
    "legend of blue eyes", "metal raiders", "revised", "alpha", "beta",
]
_CATEGORY_WORDS = [
    (CardCategory.pokemon, r"pok[eé]mon|\bvmax\b|\bvstar\b"),
    (CardCategory.yugioh, r"yu-?gi-?oh|\bkonami\b"),
    # Before MTG, so "Magic Johnson" stays a sports card
    (CardCategory.sports, r"\b(nba|nfl|mlb|nhl|topps|panini|bowman|upper deck|donruss|fleer|prizm|baseball|basketball|football|hockey|soccer)\b"),
    (CardCategory.mtg, r"\bmtg\b|magic the gathering"),
    (CardCategory.other_tcg, r"\b(one piece|lorcana|dragon ball|digimon|flesh and blood)\b"),
]
_TOP = 12  # best matches to read titles from


def _tokens(title: str) -> list[str]:
    return [t for t in re.findall(r"[#\w'./-]+", title.lower()) if t.strip("-./") and t not in _NOISE]


def _most_common(found: list[str], min_share: float, n: int) -> str | None:
    if not found:
        return None
    value, count = Counter(found).most_common(1)[0]
    return value if count / n >= min_share else None


_CONNECTORS = {"of", "the", "and", "&", "de", "la", "le"}


def _name_from(title: str, keep: set[str]) -> str:
    """The kept words in the central title's own wording, with connectors like "of the" between them."""
    words = title.split()
    flags = [bool(_tokens(w)) and _tokens(w)[0] in keep for w in words]
    out = []
    for i, w in enumerate(words):
        if flags[i] or (w.lower() in _CONNECTORS and any(flags[:i]) and any(flags[i + 1:]) and out and out[-1] is not None):
            out.append(w)
        elif out and out[-1] is not None:
            out.append(None)  # a gap: only bridge connectors inside one run of kept words
    runs = " ".join(w if w is not None else "|" for w in out).split("|")
    best = max((r.strip() for r in runs), key=lambda r: len(r.split()), default="")
    best = re.sub(r"^(?:(?:of|the|and|&)\s+)+|(?:\s+(?:of|the|and|&))+$", "", best, flags=re.I)
    return best.title() if best.isupper() else best


def parse_matches(matches: list[EbayItem]) -> CardIdentification:
    """Turn the best image matches into one identification by reading what their titles agree on."""
    titles = [m.title for m in matches if not _JUNK.search(m.title)][:_TOP]
    if not titles:
        return CardIdentification(name="", confidence=0.0, search_query="")
    n = len(titles)
    token_sets = [set(_tokens(t)) for t in titles]

    # The "central" listing is the one sharing the most words with the others; its word order is
    # the most natural, so the name and search query are built from it.
    def overlap(a: set, b: set) -> float:
        return len(a & b) / max(1, min(len(a), len(b)))

    centre = max(range(n), key=lambda i: sum(overlap(token_sets[i], s) for s in token_sets))
    # Words at least half the matches agree on
    counts = Counter(t for s in token_sets for t in s)
    consensus = [t for t in dict.fromkeys(_tokens(titles[centre])) if counts[t] / n >= 0.5]

    joined = " ".join(titles)
    year_m = [m.group(0) for t in titles for m in [_YEAR.search(t)] if m]
    grade_m = [f"{m.group(1).upper().replace('BECKETT', 'BGS')} {m.group(2)}" for t in titles for m in [_GRADE.search(t)] if m]
    num_m = [next(g for g in m.groups() if g) for t in titles for m in [_NUMBER.search(t) or _LETTER_CODE.search(t)] if m]

    year = _most_common(year_m, 0.4, n)
    grade = _most_common(grade_m, 0.5, n) or ("Raw" if len(grade_m) / n < 0.3 else None)
    number = _most_common(num_m, 0.4, n)
    low = joined.lower()
    category = next((c for c, rx in _CATEGORY_WORDS if re.search(rx, low)), CardCategory.other)
    cons_text = " ".join(consensus)
    set_name = next((s for s in _SETS if re.search(rf"\b{re.escape(s)}\b", cons_text)), None)
    variants = [v for v in _VARIANTS if sum(bool(re.search(rf"\b{re.escape(v)}\b", t.lower())) for t in titles) / n >= 0.5]
    variants = [v for v in variants if not (set_name and v in set_name)]  # "Prizm" is the set, not a parallel
    if "rookie" in variants and "rc" in variants:
        variants.remove("rc")
    variant = ", ".join(dict.fromkeys(v.title() if v not in ("rc", "sp") else v.upper() for v in variants)) or None
    is_auto = sum(bool(_AUTO.search(t)) for t in titles) / n >= 0.5
    is_redemption = sum(bool(_REDEMPTION.search(t)) for t in titles) / n >= 0.5

    # Name: consensus words minus the ones already captured in other fields
    taken = set()
    for v in [year, number, set_name, *(variants or [])]:
        if v:
            taken |= set(_tokens(v)) | {f"#{v.lower()}", v.lower()}
    if grade and grade != "Raw":
        taken |= set(_tokens(grade))
    taken |= {"psa", "bgs", "cgc", "sgc", "pokemon", "pokémon", "mtg", "magic", "yugioh", "yu-gi-oh", "panini"}
    name_words = [w for w in consensus if w not in taken and not _YEAR.fullmatch(w) and not w.startswith("#")
                  and not re.fullmatch(r"\d+(/\d+)?|\d+(\.5)?", w)]
    name = _name_from(titles[centre], set(name_words[:6])) or titles[centre][:60]
    if category == CardCategory.sports:
        # keep the player in the name and move insert words ("Sensational Samurai") to the variant
        name, extras = split_player(name)
        if extras:
            variant = ", ".join(dict.fromkeys([*extras, *(variant.split(", ") if variant else [])]))
    if number is None:
        # a letter-only card code ("CHR-MT") is printed small and often left out of titles, but when a quarter of the
        # matches agree on one it names the card far better than nothing
        codes = [m.group(1) for t in titles for m in [_LETTER_CODE.search(t)] if m]
        number = _most_common(codes, 0.25, n)

    # Confidence: how many of the top matches are plainly the same listing as the central one,
    # discounted when there are only a few matches to go on.
    agree = sum(overlap(token_sets[centre], s) >= 0.6 for s in token_sets) / n
    confidence = round(min(0.95, agree * min(1.0, n / 8)), 2)

    query_words = [w for w in consensus if w not in {"psa", "bgs", "cgc", "sgc"} or grade != "Raw"][:10]
    return CardIdentification(
        category=category, name=name, set_name=set_name.title() if set_name else None, year=year,
        card_number=number, variant=variant, grade=grade, condition=None, confidence=confidence,
        search_query=" ".join(query_words) or titles[centre][:80], is_redemption=is_redemption, is_auto=is_auto,
    )


class EbayImageIdentifier:
    name = "ebay-image"

    def __init__(self, ebay: EbayClient):
        self._ebay = ebay

    async def match(self, image_bytes: bytes) -> tuple[CardIdentification, list[EbayItem]]:
        """Identification plus the matched listings, so the scan can price from them directly."""
        matches = await self._ebay.search_by_image(image_bytes)
        return parse_matches(matches), matches

    async def identify(self, image_bytes: bytes, media_type: str) -> CardIdentification:
        return (await self.match(image_bytes))[0]
