from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlmodel import Session

from ..db import get_session
from ..services.app_settings import load_app_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsBody(BaseModel):
    min_group_size: int = Field(ge=2, le=20)  # fewest listings a price group can have
    wide_spread_action: Literal["flag", "highest_group"]  # what a card whose prices spread too widely gets


@router.get("", response_model=SettingsBody)
def get_app_settings(session: Session = Depends(get_session)):
    s = load_app_settings(session)
    return SettingsBody(min_group_size=s.min_group_size, wide_spread_action=s.wide_spread_action)


@router.put("", response_model=SettingsBody)
def save_app_settings(body: SettingsBody, session: Session = Depends(get_session)):
    """Save both settings. They apply the next time a card is priced; cards already priced keep their values."""
    s = load_app_settings(session)
    s.sqlmodel_update(body.model_dump())
    session.add(s)
    session.commit()
    session.refresh(s)
    return SettingsBody(min_group_size=s.min_group_size, wide_spread_action=s.wide_spread_action)
