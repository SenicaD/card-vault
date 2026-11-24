"""Is a listing title about this card at all?

eBay's photo search returns cards that merely look alike (same set, same colours), so a Surtain Mosaic
search also brings back Mahomes and Josh Jacobs cards. A title is rejected when it names a different
player, a different year, or a different card number. When we know too little about the card to judge
(no usable name), nothing is rejected.
"""
import re
import unicodedata
from typing import NamedTuple

from .parallels import AUTO_RX, REDEMPTION_RX, bare_code_keys, card_code, classify_listing, code_key

_SUFFIX = {"jr", "sr", "ii", "iii", "iv", "v"}
# Words a card name often ends in that are not the player's surname: insert and product names, card terms, and team,
# city and college names ("Samurai Japan Yoshinobu Yamamoto Sensational Samurai", "RJ Harvey Denver Broncos",
# "King Miller USC"). They are dropped from the end of a name before its last word is taken as the surname, so
# "Samurai" doesn't let every Samurai Japan player through. A real surname here (Washington) only makes the filter
# use the word before it; a name left with nothing ("Tampa Bay Buccaneers") filters by year and number alone.
_NOT_A_SURNAME = set("""
samurai japan sensational generational greats cosmic chrome star stars clusters stellar portraits ud rookies rookie
rated prospect prospects elite young guns sizzle reel speckle obsidian orbital turbocharged best topps bowman panini
prizm donruss optic select mosaic upper deck refractor auto autograph autographs chromographs rc sp ssp insert
parallel card baseball football basketball hockey soccer wbc team collection variation short print
cardinals falcons ravens bills panthers bears bengals browns cowboys broncos lions packers texans colts jaguars chiefs
raiders chargers rams dolphins vikings patriots saints giants jets eagles steelers niners seahawks buccaneers bucs
titans commanders diamondbacks dbacks braves orioles sox cubs reds guardians rockies tigers astros royals angels
dodgers marlins brewers twins mets yankees athletics phillies pirates padres mariners rays rangers jays nationals
hawks celtics nets hornets bulls cavaliers cavs mavericks mavs nuggets pistons warriors rockets pacers clippers lakers
grizzlies heat bucks timberwolves pelicans knicks thunder sixers suns blazers trail kings spurs raptors jazz wizards
ducks bruins sabres flames hurricanes blackhawks avalanche jackets wings oilers wild canadiens predators devils
islanders senators flyers penguins sharks kraken blues lightning leafs maple canucks knights capitals
arizona atlanta baltimore buffalo carolina chicago cincinnati cleveland dallas denver detroit green bay houston
indianapolis jacksonville kansas city las vegas los angeles miami minnesota new england orleans york philadelphia
pittsburgh san francisco diego jose seattle tampa tennessee washington boston brooklyn charlotte golden state memphis
milwaukee oklahoma orlando phoenix portland sacramento antonio toronto utah anaheim calgary columbus edmonton florida
montreal nashville jersey ottawa st louis vancouver winnipeg colorado texas oakland
usc ucla lsu tcu smu byu alabama clemson michigan georgia oregon auburn notre dame university college
""".split())
_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)(?:\s?[-/]\s?(\d{2}))?\b")
_NUM = re.compile(r"#\s?([A-Za-z]{0,4}-?\d+[A-Za-z]?)(?!\s?/)")


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


class Who(NamedTuple):
    surname: str | None
    year: str | None
    number: str | None


def surname_of(name: str | None) -> str | None:
    """The player's surname: the last word of the name once suffixes (Jr, II) and trailing insert, team, city and
    college words (_NOT_A_SURNAME) are dropped. None when nothing of 3+ letters is left."""
    words = [w.replace("'", "") for w in re.findall(r"[a-z']+", _fold(name or "")) if w not in _SUFFIX]
    while words and words[-1] in _NOT_A_SURNAME:
        words.pop()
    return words[-1] if words and len(words[-1]) >= 3 else None


def who_from(name: str | None, year: str | None, number: str | None) -> Who | None:
    surname = surname_of(name)
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
    auto: bool | None = None  # whether listings must (True) or must not (False) be autographs; None = either


def identity_of_card(card) -> CardIdentity | None:
    """The card code, redemption and autograph status listings must match; None when the card is too uncertain (as
    who_of_card). A card is a redemption or an autograph when its saved flag is set or its text says so: cards saved
    before the flags existed only say it in their variant ("Refractor, Auto, RC")."""
    if card.id_confidence is not None and card.id_confidence < 0.5:
        return None
    texts = [t for t in (card.name, card.set_name, card.variant, card.card_number, card.search_query) if t]
    redemption = card.is_redemption or any(REDEMPTION_RX.search(t) for t in texts)
    auto = bool(card.is_auto or redemption or any(AUTO_RX.search(t) for t in texts))  # a redemption is for an auto
    return CardIdentity(card_code(card.card_number), redemption, auto)


def fits_identity(title: str, ident: CardIdentity | None) -> bool:
    """False when the title shows only other card codes, is a redemption and the card isn't (or the other way round),
    or is an autograph and the card isn't (or the other way round).

    Only hyphenated codes ("PA-JM") mark a listing as another card; a no-hyphen word ("BTP2") can only confirm the
    card's own code, since "SP2" or "PS5" are ordinary title words. A redemption listing counts as an autograph, since
    sellers often leave "auto" out of a redemption's title.
    """
    if ident is None:
        return True
    c = classify_listing(title)
    if ident.code and c.codes and ident.code not in {code_key(k) for k in c.codes} | bare_code_keys(title):
        return False
    if c.redemption != ident.redemption:
        return False
    return ident.auto is None or (c.auto or c.redemption) == ident.auto
