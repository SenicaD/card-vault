"""Wraps an eBay client so every call is timed and recorded (Debug page, and the trace of the lookup it
belongs to). Behaviour is unchanged: results and errors pass straight through."""
import time
from typing import Any, Awaitable, Callable

from ..debuglog import current_purpose, current_trace, log_event


class LoggedEbay:
    def __init__(self, inner: Any):
        self._inner = inner
        self.mode = inner.mode

    def __getattr__(self, name: str) -> Any:  # create_listing, token details, ...
        return getattr(self._inner, name)

    async def _run(self, what: str, detail: str, fn: Callable[[], Awaitable[Any]], count: bool = True) -> Any:
        t0, err, n = time.monotonic(), None, None
        try:
            res = await fn()
            n = len(res) if count else None
            return res
        except Exception as e:
            err = repr(e)[:400]
            raise
        finally:
            rec = {"api": what, "purpose": current_purpose.get(), "detail": detail[:200],
                   "ms": round((time.monotonic() - t0) * 1000), "returned": n, "error": err}
            log_event(source="ebay", **rec)
            tr = current_trace.get()
            if tr is not None:
                tr.calls.append(rec)

    async def search_active(self, query: str, limit: int = 50):
        return await self._run("text search", query, lambda: self._inner.search_active(query, limit=limit))

    async def search_by_image(self, image_bytes: bytes, limit: int = 50):
        return await self._run("photo search", f"photo, {len(image_bytes) // 1024} KB",
                               lambda: self._inner.search_by_image(image_bytes, limit=limit))

    async def get_item_status(self, item_id: str):
        return await self._run("item check", item_id, lambda: self._inner.get_item_status(item_id), count=False)
