"""In-memory record of recent outgoing calls, for the Debug page. Lost on restart, which is fine:
it answers "what has the app been doing in the last hour", not "what happened last month"."""
from collections import deque
from contextvars import ContextVar
from typing import Any

from ..models import utcnow

EVENTS: deque[dict[str, Any]] = deque(maxlen=500)

# Which lookup is in progress, and why a search is being made. Set around a lookup so every eBay call
# made inside it lands on that lookup's trace without threading a parameter through every function.
current_trace: ContextVar[Any] = ContextVar("current_trace", default=None)
current_purpose: ContextVar[str] = ContextVar("current_purpose", default="search")


def log_event(**fields: Any) -> None:
    EVENTS.appendleft({"ts": utcnow().isoformat(), **fields})
