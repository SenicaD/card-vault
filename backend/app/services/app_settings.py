"""Settings you change in the app (the Settings page), kept in the database rather than in .env."""
from sqlmodel import Session, select

from ..models import AppSettings

# What a card whose asking prices spread too widely gets: flagged with no asking value, or valued at the highest
# price group's median
WIDE_SPREAD_ACTIONS = ("flag", "highest_group")


def load_app_settings(session: Session) -> AppSettings:
    """The saved settings, or an unsaved row of defaults when nothing has been saved yet."""
    return session.exec(select(AppSettings)).first() or AppSettings()
