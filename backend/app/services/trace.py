"""Record how a price was worked out, so it can be inspected afterwards (Debug mode)."""
import time
from contextlib import contextmanager
from typing import Any

from sqlmodel import Session, col, select

from ..models import PriceTrace
from .debuglog import current_trace
from .parallels import classify_listing

KEEP_TRACES = 20  # per card
KEEP_LISTINGS = 3  # newest traces per card that keep the listing-by-listing table
MAX_LISTINGS = 250  # per trace

# What happened to each listing, in the order the checks are made
DECISIONS = {
    "junk": "Looks like a lot, bundle or reprint",
    "unknown_grade": "The title doesn't say a grade",
    "grade_not_requested": "A grade you didn't ask about",
    "other_card": "A different player, year or card number",
    "other_identity": "Doesn't match the card's number or redemption status",
    "not_alike": "Doesn't look like your photo",
    "excluded": "You unticked it",
    "other_version": "A different version of the card",
    "outlier": "Price far from the rest",
    "used": "Counted in the price",
}


class Trace:
    def __init__(self, kind: str = "lookup", trigger: str | None = None, query: str | None = None,
                 grades: list[str] | None = None, parallel: str | None = None):
        self.kind, self.trigger, self.query, self.grades, self.parallel = kind, trigger, query, grades, parallel
        self.calls: list[dict[str, Any]] = []
        self.notes: list[str] = []
        self.funnel: list[dict[str, Any]] = []
        self.summary: dict[str, Any] = {}
        self.mode: str | None = None
        self.error: str | None = None
        self.raw: list[dict[str, Any]] | None = None
        self.identity: dict[str, Any] | None = None
        self.image_path: str | None = None
        self.source_image: str | None = None
        self._listings: dict[str, dict[str, Any]] = {}
        self.looks: dict[str, dict[str, Any]] = {}  # item id -> look-alike confidence "s" and feature matches "m"
        self._t0 = time.monotonic()

    @contextmanager
    def active(self):
        """Every eBay call made inside this block is added to the trace."""
        token = current_trace.set(self)
        try:
            yield self
        finally:
            current_trace.reset(token)

    def note(self, text: str) -> None:
        self.notes.append(text)

    def decide(self, item, grade: str | None, decision: str, version: str | None = None) -> None:
        look = self.looks.get(item.item_id, {})
        c = classify_listing(item.title)
        tags = [*c.codes, *(["Auto"] if c.auto else []), *(["Redemption"] if c.redemption else [])]
        self._listings[item.item_id] = {"id": item.item_id, "t": item.title[:110], "p": item.price, "g": grade,
                                        "v": version, "d": decision, "r": None, "u": item.url,
                                        "s": look.get("s"), "m": look.get("m"), "k": " · ".join(tags) or None}

    def role(self, item_id: str, role: str) -> None:
        if item_id in self._listings and self._listings[item_id]["d"] == "used":
            self._listings[item_id]["r"] = role

    @property
    def listings(self) -> list[dict[str, Any]]:
        return list(self._listings.values())[:MAX_LISTINGS]

    @property
    def duration_ms(self) -> int:
        return round((time.monotonic() - self._t0) * 1000)


def save_trace(session: Session, trace: Trace, card_id: int | None = None, keep_raw: bool = False) -> PriceTrace:
    row = PriceTrace(card_id=card_id, kind=trace.kind, trigger=trace.trigger, query=trace.query, mode=trace.mode,
                     parallel=trace.parallel, duration_ms=trace.duration_ms, error=trace.error, grades=trace.grades,
                     calls=trace.calls, funnel=trace.funnel, summary=trace.summary, listings=trace.listings,
                     raw=trace.raw if keep_raw else None, identity=trace.identity, notes=trace.notes,
                     image_path=trace.image_path, source_image=trace.source_image)
    session.add(row)
    session.commit()
    session.refresh(row)
    if card_id is not None and trace.kind in ("lookup", "sold"):
        _prune(session, card_id, trace.kind)
    return row


def _prune(session: Session, card_id: int, kind: str = "lookup") -> None:
    rows = session.exec(select(PriceTrace).where(PriceTrace.card_id == card_id, PriceTrace.kind == kind)
                        .order_by(col(PriceTrace.id).desc())).all()
    for r in rows[KEEP_TRACES:]:
        session.delete(r)
    for r in rows[KEEP_LISTINGS:KEEP_TRACES]:
        r.listings = None
        session.add(r)
    seen_raw = False
    for r in rows[:KEEP_TRACES]:  # only the newest lookup that has raw data keeps it
        if r.raw is not None:
            if seen_raw:
                r.raw = None
                session.add(r)
            seen_raw = True
    session.commit()


def identification_trace(identifier: str, ident, matches, image_path: str, source_image: str | None = None,
                         crop: dict | None = None, trigger: str = "scan", photo_bytes: int | None = None) -> Trace:
    """How a photo was turned into a card name: which identifier, what it concluded, and (for eBay image
    matching) which listings it read the details from."""
    from .identify.ebay_image import _JUNK, _TOP

    t = Trace("identify", trigger, ident.search_query or None)
    t.image_path, t.source_image = image_path, source_image
    read, rows = 0, []
    for m in (matches or [])[:40]:
        junk = bool(_JUNK.search(m.title))
        used = not junk and read < _TOP
        read += used
        rows.append({"id": m.item_id, "t": m.title[:110], "p": m.price, "u": m.url, "read": used, "junk": junk})
    t.identity = {"identifier": identifier, "photo_kb": round(photo_bytes / 1024) if photo_bytes else None,
                  "crop": crop, "matches": rows, "matched_total": len(matches or []), "read_limit": _TOP,
                  "result": ident.model_dump(mode="json")}
    if matches is not None and not matches:
        t.note("eBay found no listings that look like this photo, so nothing could be read from titles.")
    return t


def compare_traces(old: PriceTrace, new: PriceTrace) -> dict[str, Any]:
    """What changed between two lookups of the same card: listings that appeared, vanished or changed
    price, how each grade's price moved, and whether the search itself was different."""
    a = {r["id"]: r for r in (old.listings or [])}
    b = {r["id"]: r for r in (new.listings or [])}
    grades_a = {f["grade"]: f for f in (old.funnel or [])}
    grades_b = {f["grade"]: f for f in (new.funnel or [])}
    meta = lambda t: {"id": t.id, "created_at": t.created_at, "trigger": t.trigger, "mode": t.mode, "query": t.query}  # noqa: E731
    return {
        "a": meta(old), "b": meta(new),
        "search_changed": {"query": old.query != new.query, "mode": old.mode != new.mode,
                           "version": old.parallel != new.parallel},
        "grades": [{"grade": g, "a_median": (grades_a.get(g) or {}).get("median"), "b_median": (grades_b.get(g) or {}).get("median"),
                    "a_used": (grades_a.get(g) or {}).get("used"), "b_used": (grades_b.get(g) or {}).get("used")}
                   for g in dict.fromkeys([*grades_a, *grades_b])],
        "new": [b[i] for i in b if i not in a],
        "gone": [a[i] for i in a if i not in b],
        "price_changed": [{**b[i], "was": a[i]["p"]} for i in b if i in a and a[i]["p"] != b[i]["p"]],
        "decision_changed": [{**b[i], "was_decision": a[i]["d"]} for i in b if i in a and a[i]["d"] != b[i]["d"]],
        "unchanged": sum(1 for i in b if i in a and a[i]["p"] == b[i]["p"] and a[i]["d"] == b[i]["d"]),
    }
