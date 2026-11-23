"""Settings you change in the app (the Settings page), kept in the database rather than in .env."""
from typing import get_args

from sqlmodel import Session, select

from ..models import AppSettings, WideSpreadAction

WIDE_SPREAD_ACTIONS = get_args(WideSpreadAction)


def load_app_settings(session: Session) -> AppSettings:
    """The saved settings, or an unsaved row of defaults when nothing has been saved yet."""
    return session.exec(select(AppSettings)).first() or AppSettings()
