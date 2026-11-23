from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from ..db import get_session
from ..services.app_settings import WIDE_SPREAD_ACTIONS, load_app_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])

MIN_GROUP_SIZE, MAX_GROUP_SIZE = 2, 20
MIN_PRICE_WINDOW, MAX_PRICE_WINDOW = 1, 50


class SettingsBody(BaseModel):
    cluster_min_size: int  # fewest listings a price group can have
    wide_spread_action: str  # what a card whose prices spread too widely gets: "flag" | "highest_group" | ...
    price_window: int  # how many listings the Highest, Median and Lowest rules take the average of
    price_ceiling: float | None = None  # a listing priced above this is left out of the calculation; None = no limit


@router.get("", response_model=SettingsBody)
def get_app_settings(session: Session = Depends(get_session)):
    s = load_app_settings(session)
    return SettingsBody(cluster_min_size=s.cluster_min_size, wide_spread_action=s.wide_spread_action,
                        price_window=s.price_window, price_ceiling=s.price_ceiling)


@router.put("", response_model=SettingsBody)
def save_app_settings(body: SettingsBody, session: Session = Depends(get_session)):
    """Save the settings. They apply the next time a card is priced; cards already priced keep their values."""
    if not MIN_GROUP_SIZE <= body.cluster_min_size <= MAX_GROUP_SIZE:
        raise HTTPException(422, f"Minimum listings per price group must be a whole number from {MIN_GROUP_SIZE} "
                                 f"to {MAX_GROUP_SIZE}")
    if not MIN_PRICE_WINDOW <= body.price_window <= MAX_PRICE_WINDOW:
        raise HTTPException(422, f"Listings to price from must be a whole number from {MIN_PRICE_WINDOW} "
                                 f"to {MAX_PRICE_WINDOW}")
    if body.price_ceiling is not None and not 0 < body.price_ceiling <= 1_000_000:
        raise HTTPException(422, "Ignore listings above must be a price above $0 (or empty for no limit)")
    if body.wide_spread_action not in WIDE_SPREAD_ACTIONS:
        raise HTTPException(422, f"When prices spread too widely must be one of: {', '.join(WIDE_SPREAD_ACTIONS)}")
    s = load_app_settings(session)
    s.sqlmodel_update(body.model_dump())
    session.add(s)
    session.commit()
    session.refresh(s)
    return SettingsBody(cluster_min_size=s.cluster_min_size, wide_spread_action=s.wide_spread_action,
                        price_window=s.price_window, price_ceiling=s.price_ceiling)
