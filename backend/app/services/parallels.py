"""Read a card's colour / print-run version ("parallel") out of an eBay listing title.

Sellers write these in free text ("Red Orbital RPA /99", "Green Mojo /99 Auto"), so this is a best guess:
the first colour word plus a print run written as "/NN". Titles with neither are "Base".

classify_listing also reads which card a title is for: its card codes ("CRA-JMI", "BTP-2", "LOB-EN001"),
and whether it is a redemption or an autograph. Only hyphenated codes count as a title's codes, so only they can
mark a listing as another card; a no-hyphen word ("BTP2", but also "SP2", "PS5") can only confirm a card's own code.
"""
import re
from typing import NamedTuple

# Longest first, so "lava flow" is not read as just "lava"
_COLORS = ["neon green", "lava flow", "rainbow", "camo", "black", "blue", "red", "green", "gold", "orange", "purple",
           "pink", "silver", "white", "yellow", "bronze", "teal", "aqua", "emerald", "ruby", "sapphire", "magenta",
           "ice", "copper", "platinum", "lime", "navy"]
_COLOR_RX = re.compile(r"\b(" + "|".join(_COLORS) + r")\b", re.I)
_RUN_RX = re.compile(r"(?<![\w/])/\s?(\d{1,4})\b")
BASE = "Base"

# A card code is letters + digits in any case ("BTP-2", "btp2", "LOB-EN001", region letters optional), or
# capitals-capitals ("CRA-JMI", "I-JM"); the 2-3 letter suffix keeps "ON-CARD" and "ALL-STAR" out.
# In titles the no-hyphen form ("btp2") is also an ordinary word ("SP2", "PS5", "Top100"), so it is never one of a
# title's codes; it can only confirm a card's own code (bare_code_keys)
_FORM_A = r"([a-z]{1,5})-([a-z]{1,2})?(\d{1,4})|([a-z]{2,5})(\d{1,4})"
_FORM_B = r"([A-Z]{1,5})-([A-Z]{2,3})"
_CODE_RX = re.compile(rf"\b(?:(?i:{_FORM_A})|{_FORM_B})\b")
_WHOLE_CODE_RX = re.compile(rf"{_FORM_A}|{_FORM_B}", re.I)
# Graders and condition words written against a number ("PSA10", "MT10") are grades, not codes
_NOT_PREFIX = {"PSA", "BGS", "SGC", "CGC", "CSG", "HGA", "TAG", "GMA", "BVG", "BCCG", "KSA", "ISA", "AGS", "GEM",
               "MINT", "MT", "NM", "EX", "VG", "NO"}
_CONDITION = {"NM", "EX", "MT", "VG", "GEM", "GD", "PR", "FR"}  # "NM-MT", "GEM-MT"
REDEMPTION_RX = re.compile(r"\bredemp", re.I)
_AUTO_RX = re.compile(r"\b(auto|autos|autograph\w*|rpa)\b", re.I)


class ListingTags(NamedTuple):
    codes: tuple[str, ...]  # hyphenated card codes in the title, upper-cased, in order
    redemption: bool
    auto: bool
    color: str | None
    run: int | None  # print run, from "/NN"


def _key(m: re.Match) -> str:
    if m.group(1):
        return m.group(1).lower() + str(int(m.group(3)))
    if m.group(4):
        return m.group(4).lower() + str(int(m.group(5)))
    return (m.group(6) + m.group(7)).lower()


def _is_code(m: re.Match) -> bool:
    letters, digits = (m.group(1), m.group(3)) if m.group(1) else (m.group(4), m.group(5))
    if letters:
        return letters.upper() not in _NOT_PREFIX and not (len(digits) == 4 and 1950 <= int(digits) <= 2049)
    return not (m.group(6).upper() in _CONDITION and m.group(7).upper() in _CONDITION)


def code_key(code: str) -> str:
    """What two codes are compared by: case, punctuation, region letters and leading zeros don't count."""
    m = _WHOLE_CODE_RX.fullmatch(code.strip().lstrip("#").strip())
    return _key(m) if m else re.sub(r"[^a-z0-9]", "", code.lower())


def card_code(card_number: str | None) -> str | None:
    """The code key of a card number that is a card code ("CRA-JMI", "LOB-EN001"); None for "136" and the like."""
    m = _WHOLE_CODE_RX.fullmatch((card_number or "").strip().lstrip("#").strip())
    return _key(m) if m and _is_code(m) else None


def bare_code_keys(title: str) -> set[str]:
    """Code keys of the no-hyphen code-shaped words in a title ("BTP2", "SP2"): they may be ordinary words."""
    return {_key(m) for m in _CODE_RX.finditer(title) if m.group(4) and _is_code(m)}


def classify_listing(title: str) -> ListingTags:
    codes = [m.group(0).upper() for m in _CODE_RX.finditer(title) if not m.group(4) and _is_code(m)]
    color = _COLOR_RX.search(title)
    run = _RUN_RX.search(title)
    return ListingTags(tuple(dict.fromkeys(codes)), bool(REDEMPTION_RX.search(title)), bool(_AUTO_RX.search(title)),
                   color.group(1).title() if color else None, int(run.group(1)) if run else None)


def parse_parallel(title: str) -> str:
    c = classify_listing(title)
    parts = []
    if c.color:
        parts.append(c.color)
    if c.run is not None:
        parts.append(f"/{c.run}")
    return " ".join(parts) or BASE
