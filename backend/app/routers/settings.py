from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from ..db import get_session
from ..services.app_settings import WIDE_SPREAD_ACTIONS, load_app_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])

MIN_GROUP_SIZE, MAX_GROUP_SIZE = 2, 20


class SettingsBody(BaseModel):
    cluster_min_size: int  # fewest listings a price group can have
    wide_spread_action: str  # what a card whose prices spread too widely gets: "flag" | "highest_group"


@router.get("", response_model=SettingsBody)
def get_app_settings(session: Session = Depends(get_session)):
    s = load_app_settings(session)
    return SettingsBody(cluster_min_size=s.cluster_min_size, wide_spread_action=s.wide_spread_action)


@router.put("", response_model=SettingsBody)
def save_app_settings(body: SettingsBody, session: Session = Depends(get_session)):
    """Save both settings. They apply the next time a card is priced; cards already priced keep their values."""
    if not MIN_GROUP_SIZE <= body.cluster_min_size <= MAX_GROUP_SIZE:
        raise HTTPException(422, f"Minimum listings per price group must be a whole number from {MIN_GROUP_SIZE} "
                                 f"to {MAX_GROUP_SIZE}")
    if body.wide_spread_action not in WIDE_SPREAD_ACTIONS:
        raise HTTPException(422, f"When prices spread too widely must be one of: {', '.join(WIDE_SPREAD_ACTIONS)}")
    s = load_app_settings(session)
    s.sqlmodel_update(body.model_dump())
    session.add(s)
    session.commit()
    session.refresh(s)
    return SettingsBody(cluster_min_size=s.cluster_min_size, wide_spread_action=s.wide_spread_action)
