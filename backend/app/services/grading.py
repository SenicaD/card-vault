"""Work out a card's grade from an eBay listing title, and normalise the grade a user typed.

eBay's Browse API doesn't return the grade as a clean field, but sellers put it in the title
("PSA 10", "BGS 9.5", "CGC10") and set the item condition to Graded/Ungraded. Grades come out as
labels like "PSA 10" or "Raw". Titles we can't classify come out as None.
"""
import re

RAW = "Raw"

_GRADER_NAMES = {"PSA": "PSA", "BGS": "BGS", "BECKETT": "BGS", "CGC": "CGC", "CSG": "CGC", "SGC": "SGC", "HGA": "HGA"}
_NUM = r"(10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4\.5|4|3|2\.5|2|1\.5|1)"
_GRADED = re.compile(
    r"\b(PSA|BGS|BECKETT|CGC|CSG|SGC|HGA)(?![A-Za-z])\s*[#:\-]?\s*"
    r"(?:(?:GEM\s*(?:MT|MINT)|NM-?MT|NEAR\s*MINT|MINT|PRISTINE)\s*)?" + _NUM + r"(?![\d.]*\d)",
    re.I,
)
# "PSA 10 candidate", "ready for PSA 10": the card is raw, the seller is just hinting
_HINT = re.compile(
    r"(candidate|\bcand\b|ready for|potential|worthy|could be|might be|may be|to grade|for grading|"
    r"submit|submission|crossover|grade it|pre-?grade)", re.I)
_RAW = re.compile(r"\b(raw|ungraded|un-graded|not graded)\b", re.I)

# Chips offered in the UI before any listing has been seen, in display order.
STANDARD_GRADES = ["Raw", "PSA 10", "PSA 9", "PSA 8", "BGS 9.5", "BGS 9", "CGC 10", "CGC 9", "SGC 10"]


def _label(m: re.Match) -> str:
    return f"{_GRADER_NAMES[m.group(1).upper()]} {m.group(2)}"


def parse_grade(title: str, condition: str | None = None) -> str | None:
    """Grade label for a listing, 'Raw' for ungraded, or None when the title doesn't say."""
    if (condition or "").lower() == "ungraded":
        return RAW
    hinted = bool(_HINT.search(title))
    if not hinted and (m := _GRADED.search(title)):
        return _label(m)
    if hinted or _RAW.search(title):
        return RAW
    return None


def normalize_grade(grade: str | None) -> str:
    """Canonical label for a grade the user entered. Empty, 'raw' or anything unreadable is Raw."""
    if not grade or not grade.strip():
        return RAW
    m = _GRADED.search(grade)
    return _label(m) if m else RAW


def grade_sort_key(label: str) -> tuple:
    """Raw first, then graders alphabetically, highest grade first."""
    if label == RAW:
        return (0, "", 0.0)
    grader, _, num = label.partition(" ")
    try:
        return (1, grader, -float(num))
    except ValueError:
        return (2, label, 0.0)
