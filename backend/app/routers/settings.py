from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, col, select

from ..db import get_session
from ..models import Card, CardStatus
from ..services.app_settings import WIDE_SPREAD_ACTIONS, load_app_settings
from ..services.target import update_target

router = APIRouter(prefix="/api/settings", tags=["settings"])

MIN_GROUP_SIZE, MAX_GROUP_SIZE = 2, 20
MIN_PRICE_WINDOW, MAX_PRICE_WINDOW = 1, 50
MIN_OUTLIER_RATIO, MAX_OUTLIER_RATIO = 1.5, 100
# Target price settings (services/target.py)
MIN_ASKING_DISCOUNT, MAX_ASKING_DISCOUNT = 0.3, 1.0
MIN_SMOOTHING, MAX_SMOOTHING = 0.0, 0.5
MIN_MARKUP, MAX_MARKUP = 1.0, 2.0
TARGET_FIELDS = ("asking_discount", "smoothing_band", "list_markup", "review_below")


class SettingsBody(BaseModel):
    cluster_min_size: int  # fewest listings a price group can have
    wide_spread_action: str  # what a card whose prices spread too widely gets: "flag" | "highest_group" | ...
    price_window: int  # how many listings the Highest, Median and Lowest rules take the average of
    outlier_ratio: float | None = None  # leave out listings above this many times the card's own median; None = no limit
    # Left out of a save, these keep their saved values
    asking_discount: float | None = None  # a card valued from asking prices alone is valued at this share of them
    smoothing_band: float | None = None  # a daily lookup moving the target by more than this share waits for a second
    list_markup: float | None = None  # list at the target times this
    review_below: int | None = None  # cards with less confidence than this (0-100) go to the review queue
    sold_auto: bool | None = None  # check sold prices automatically, riskiest first; read back as what is in effect
    sold_available: bool = False  # read only: whether a sold-price provider is set up at all


def _body(s, session: Session) -> SettingsBody:
    from ..services.sold import get_sold
    from ..services.sold_refresh import auto_enabled
    return SettingsBody(cluster_min_size=s.cluster_min_size, wide_spread_action=s.wide_spread_action,
                        price_window=s.price_window, outlier_ratio=s.outlier_ratio,
                        **{f: getattr(s, f) for f in TARGET_FIELDS},
                        sold_auto=auto_enabled(session), sold_available=get_sold() is not None)


@router.get("", response_model=SettingsBody)
def get_app_settings(session: Session = Depends(get_session)):
    return _body(load_app_settings(session), session)


def _check(ok: bool, message: str) -> None:
    if not ok:
        raise HTTPException(422, message)


@router.put("", response_model=SettingsBody)
def save_app_settings(body: SettingsBody, session: Session = Depends(get_session)):
    """Save the settings. Listing settings apply the next time a card is priced; target price settings re-value every
    card's target at once (no eBay calls)."""
    _check(MIN_GROUP_SIZE <= body.cluster_min_size <= MAX_GROUP_SIZE,
           f"Minimum listings per price group must be a whole number from {MIN_GROUP_SIZE} to {MAX_GROUP_SIZE}")
    _check(MIN_PRICE_WINDOW <= body.price_window <= MAX_PRICE_WINDOW,
           f"Listings to price from must be a whole number from {MIN_PRICE_WINDOW} to {MAX_PRICE_WINDOW}")
    _check(body.outlier_ratio is None or MIN_OUTLIER_RATIO <= body.outlier_ratio <= MAX_OUTLIER_RATIO,
           f"Ignore listings above must be from {MIN_OUTLIER_RATIO:g} to {MAX_OUTLIER_RATIO:g} times the card's median "
           "(or empty for no limit)")
    _check(body.wide_spread_action in WIDE_SPREAD_ACTIONS,
           f"When prices spread too widely must be one of: {', '.join(WIDE_SPREAD_ACTIONS)}")
    _check(body.asking_discount is None or MIN_ASKING_DISCOUNT <= body.asking_discount <= MAX_ASKING_DISCOUNT,
           f"Value asking-only cards at must be from {MIN_ASKING_DISCOUNT:.0%} to {MAX_ASKING_DISCOUNT:.0%} of asking")
    _check(body.smoothing_band is None or MIN_SMOOTHING <= body.smoothing_band <= MAX_SMOOTHING,
           f"Hold price jumps over must be from {MIN_SMOOTHING:.0%} to {MAX_SMOOTHING:.0%}")
    _check(body.list_markup is None or MIN_MARKUP <= body.list_markup <= MAX_MARKUP,
           f"List at must be from {MIN_MARKUP:.0%} to {MAX_MARKUP:.0%} of the target price")
    _check(body.review_below is None or 0 <= body.review_below <= 100, "Review below must be from 0 to 100")
    s = load_app_settings(session)
    before = {f: getattr(s, f) for f in TARGET_FIELDS}
    s.sqlmodel_update(body.model_dump(exclude_none=True, exclude={"sold_available"}) | {"outlier_ratio": body.outlier_ratio})
    session.add(s)
    session.commit()
    session.refresh(s)
    if before != {f: getattr(s, f) for f in TARGET_FIELDS}:
        for card in session.exec(select(Card).where(col(Card.status) != CardStatus.sold)).all():
            update_target(card, session, s)
            session.add(card)
        session.commit()
    return _body(s, session)
