"""Is a listing title about this card at all?

eBay's photo search returns cards that merely look alike (same set, same colours), so a Surtain Mosaic
search also brings back Mahomes and Josh Jacobs cards. A title is rejected when it names a different
player, a different year, or a different card number. When we know too little about the card to judge
(no usable name), nothing is rejected.
"""
import re
import unicodedata
from typing import NamedTuple

from .parallels import REDEMPTION_RX, bare_code_keys, card_code, classify_listing, code_key

_SUFFIX = {"jr", "sr", "ii", "iii", "iv", "v"}
_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)(?:\s?[-/]\s?(\d{2}))?\b")
_NUM = re.compile(r"#\s?([A-Za-z]{0,4}-?\d+[A-Za-z]?)(?!\s?/)")


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


class Who(NamedTuple):
    surname: str | None
    year: str | None
    number: str | None


def who_from(name: str | None, year: str | None, number: str | None) -> Who | None:
    words = [w for w in re.findall(r"[a-z']+", _fold(name or "")) if w not in _SUFFIX]
    surname = words[-1].replace("'", "") if words and len(words[-1]) >= 3 else None
    y = re.search(r"(19[5-9]\d|20[0-4]\d)", year or "")
    n = re.sub(r"[^a-z0-9-]", "", _fold(number or "")) or None
    if not (surname or y or n):
        return None
    return Who(surname, y.group(1) if y else None, n if n and any(c.isdigit() for c in n) else None)


def who_of_card(card) -> Who | None:
    """None when the card's details are too uncertain to filter by (a poor identification)."""
    if card.id_confidence is not None and card.id_confidence < 0.5:
        return None
    return who_from(card.name, card.year, card.card_number)


def same_card(title: str, who: Who | None) -> bool:
    if who is None:
        return True
    t = _fold(title).replace("'", "")
    if who.surname and not re.search(rf"\b{re.escape(who.surname)}", t):
        return False
    if who.year:
        years = set()
        for m in _YEAR.finditer(t):
            years.add(m.group(1))
            if m.group(2):  # "2024-25" also covers the following season's year
                years.add(m.group(1)[:2] + m.group(2))
        if years and who.year not in years:
            return False
    if who.number:
        nums = {re.sub(r"[^a-z0-9-]", "", m.group(1)).lstrip("0") for m in _NUM.finditer(t)}
        if nums and who.number.lstrip("0") not in nums:
            return False
    return True


class CardIdentity(NamedTuple):
    code: str | None  # code_key of the card number, when the number is a card code ("CRA-JMI")
    redemption: bool


def identity_of_card(card) -> CardIdentity | None:
    """The card code and redemption status listings must match; None when the card is too uncertain (as who_of_card)."""
    if card.id_confidence is not None and card.id_confidence < 0.5:
        return None
    texts = (card.name, card.set_name, card.variant, card.card_number, card.search_query)
    return CardIdentity(card_code(card.card_number), any(REDEMPTION_RX.search(t) for t in texts if t))


def fits_identity(title: str, ident: CardIdentity | None) -> bool:
    """False when the title shows only other card codes, or is a redemption and the card isn't (or the other way round).

    Only hyphenated codes ("PA-JM") mark a listing as another card; a no-hyphen word ("BTP2") can only confirm the
    card's own code, since "SP2" or "PS5" are ordinary title words.
    """
    if ident is None:
        return True
    c = classify_listing(title)
    if ident.code and c.codes and ident.code not in {code_key(k) for k in c.codes} | bare_code_keys(title):
        return False
    return c.redemption == ident.redemption
