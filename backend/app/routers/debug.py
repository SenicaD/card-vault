"""Debug endpoints: how prices were worked out, what the background jobs did, and the app's health."""
import json
import os
import shutil
import time
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import text
from sqlmodel import Session, SQLModel, col, select

from .. import __version__
from ..config import get_settings
from ..db import engine, get_session
from ..models import JobRun, PriceTrace, utcnow
from ..services import refresh as refresh_service
from ..services.debuglog import EVENTS
from ..services.ebay import get_ebay
from ..services.sold import SoldCompsClient, get_sold
from ..services.sold_refresh import auto_enabled, used_this_month
from ..services.trace import compare_traces
from .common import get_card_or_404

STARTED = time.time()

card_router = APIRouter(prefix="/api/cards/{card_id}/debug", tags=["debug"])
router = APIRouter(prefix="/api/debug", tags=["debug"])


def _summary(t: PriceTrace) -> dict:
    return {"id": t.id, "kind": t.kind, "created_at": t.created_at, "trigger": t.trigger, "mode": t.mode, "query": t.query,
            "grades": t.grades, "parallel": t.parallel, "duration_ms": t.duration_ms, "error": t.error,
            "has_listings": t.listings is not None, "has_raw": t.raw is not None,
            "calls": len(t.calls or []), "notes": len(t.notes or []),
            "medians": {f["grade"]: f.get("median") for f in (t.funnel or [])}}


def _lookups(session: Session, card_id: int) -> list[PriceTrace]:
    return session.exec(select(PriceTrace).where(PriceTrace.card_id == card_id, col(PriceTrace.kind).in_(("lookup", "sold")))
                        .order_by(col(PriceTrace.id).desc())).all()


@card_router.get("/traces")
def list_traces(card_id: int, session: Session = Depends(get_session)):
    get_card_or_404(session, card_id)
    return [_summary(t) for t in _lookups(session, card_id)]


def _trace_or_404(session: Session, card_id: int, trace_id: int) -> PriceTrace:
    t = session.get(PriceTrace, trace_id)
    if not t or t.card_id != card_id:
        raise HTTPException(404, "Trace not found")
    return t


@card_router.get("/traces/{trace_id}")
def get_trace(card_id: int, trace_id: int, session: Session = Depends(get_session)):
    t = _trace_or_404(session, card_id, trace_id)
    return {**t.model_dump(exclude={"raw"}), "has_raw": t.raw is not None, "has_listings": t.listings is not None}


@card_router.get("/traces/{trace_id}/raw")
def get_trace_raw(card_id: int, trace_id: int, session: Session = Depends(get_session)):
    t = _trace_or_404(session, card_id, trace_id)
    if t.raw is None:
        raise HTTPException(404, "The raw eBay data is only kept for the newest manual lookup of each card")
    body = json.dumps({"query": t.query, "mode": t.mode, "taken_at": t.created_at.isoformat(), "listings": t.raw}, indent=1)
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="card{card_id}-lookup{trace_id}-raw.json"'})


@card_router.get("/compare")
def compare(card_id: int, a: int | None = None, b: int | None = None, session: Session = Depends(get_session)):
    """Compare two lookups (default: the two newest that still have their listing table)."""
    get_card_or_404(session, card_id)
    if a is None or b is None:
        rows = [t for t in _lookups(session, card_id) if t.listings is not None and t.kind == "lookup"]
        if len(rows) < 2:
            raise HTTPException(404, "Need two lookups with listing details to compare")
        new, old = rows[0], rows[1]
    else:
        old, new = _trace_or_404(session, card_id, a), _trace_or_404(session, card_id, b)
        if old.id > new.id:
            old, new = new, old
        if old.listings is None or new.listings is None:
            raise HTTPException(422, "Listing details are only kept for the 3 newest lookups")
    return compare_traces(old, new)


@card_router.get("/identification")
def identification(card_id: int, session: Session = Depends(get_session)):
    """How the card's photo was identified: this photo's trace, then the photos it was cropped from."""
    card = get_card_or_404(session, card_id)
    chain, name, seen = [], card.image_path, set()
    while name and name not in seen and len(chain) < 6:
        seen.add(name)
        t = session.exec(select(PriceTrace).where(PriceTrace.kind == "identify", PriceTrace.image_path == name)
                         .order_by(col(PriceTrace.id).desc())).first()
        if not t:
            break
        chain.append(t.model_dump(exclude={"raw", "listings", "funnel", "calls"}))
        name = t.source_image
    return chain


# ---------- App-wide ----------

@router.get("/jobs")
def jobs(session: Session = Depends(get_session)):
    rows = session.exec(select(JobRun).order_by(col(JobRun.id).desc()).limit(50)).all()
    cur = refresh_service.current_job()
    s = get_settings()
    return {"runs": [r.model_dump() for r in rows], "current": cur.snapshot() if cur else None,
            "daily_enabled": s.ebay_mode == "live" and s.price_refresh_hours > 0, "every_hours": s.price_refresh_hours}


_RATE = ("429", "ratelimit", "rate limit", "too many")


@router.get("/events")
def events(limit: int = Query(200, ge=1, le=500)):
    """Recent eBay calls (since the app last started), with problems flagged."""
    rows = list(EVENTS)[:limit]
    hour = (utcnow() - timedelta(hours=1)).isoformat()
    recent = [e for e in EVENTS if e["ts"] >= hour]
    for e in rows:
        e["rate_limited"] = bool(e.get("error")) and any(k in e["error"].lower() for k in _RATE)
    return {"events": rows, "last_hour": {"calls": len(recent), "errors": sum(1 for e in recent if e.get("error")),
            "rate_limited": sum(1 for e in recent if e.get("error") and any(k in e["error"].lower() for k in _RATE)),
            "avg_ms": round(sum(e["ms"] for e in recent) / len(recent)) if recent else None}}


def _db_path() -> Path | None:
    url = get_settings().database_url
    return Path(url.removeprefix("sqlite:///")) if url.startswith("sqlite:///") else None


def _mb(n: int) -> float:
    return round(n / 1_048_576, 2)


@router.get("/health")
def health():
    s = get_settings()
    ebay = get_ebay()
    left = getattr(ebay, "_app_token_exp", None)
    info: dict = {
        "version": __version__, "uptime_seconds": round(time.time() - STARTED),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(STARTED)),
        "build_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.path.getmtime(Path(__file__).parents[1] / "main.py"))),
        "identifier": s.identifier,
        "ebay": {"mode": s.ebay_mode, "env": s.ebay_env, "marketplace": s.ebay_marketplace_id,
                 "credentials_set": bool(s.ebay_client_id and s.ebay_client_secret),
                 "token_seconds_left": max(0, round(left - time.time())) if left else None},
        "refresh": {"every_hours": s.price_refresh_hours, "enabled": s.ebay_mode == "live" and s.price_refresh_hours > 0},
    }
    path = _db_path()
    db: dict = {"url_kind": s.database_url.split(":")[0]}
    with engine.connect() as conn:
        db["tables"] = {t.name: conn.execute(text(f'SELECT COUNT(*) FROM "{t.name}"')).scalar() for t in SQLModel.metadata.sorted_tables}
    backups = []
    if path and path.exists():
        db["path"], db["size_mb"] = str(path), _mb(path.stat().st_size)
        for f in sorted(path.parent.glob(path.name + ".bak*"), key=lambda f: f.stat().st_mtime, reverse=True)[:10]:
            backups.append({"name": f.name, "size_mb": _mb(f.stat().st_size),
                            "modified": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(f.stat().st_mtime))})
        usage = shutil.disk_usage(path.parent)
        info["disk"] = {"free_gb": round(usage.free / 1e9, 1), "total_gb": round(usage.total / 1e9, 1)}
    info["db"], info["backups"] = db, backups
    imgs = [f for f in s.image_dir.glob("*") if f.is_file()] if s.image_dir.exists() else []
    info["images"] = {"count": len(imgs), "size_mb": _mb(sum(f.stat().st_size for f in imgs))}
    provider = get_sold()
    with Session(engine) as session:
        info["sold"] = {"provider": provider.name if provider else None, "configured": provider is not None,
                        "budget": s.sold_monthly_budget, "used_this_month": used_this_month(session),
                        "auto_used": used_this_month(session, "auto"), "refresh_days": s.sold_refresh_days,
                        "min_value": s.sold_min_value, "window_days": s.sold_window_days,
                        "auto_enabled": isinstance(provider, SoldCompsClient) and auto_enabled(session),
                        "auto_setting": auto_enabled(session)}  # the Settings page's switch, else .env
    return info


@router.post("/health/ebay-test")
async def ebay_test():
    """Make one small real call to eBay and report how it went."""
    t0 = time.monotonic()
    try:
        found = await get_ebay().search_active("baseball card", limit=1)
        return {"ok": True, "ms": round((time.monotonic() - t0) * 1000), "returned": len(found), "mode": get_settings().ebay_mode}
    except Exception as e:
        return {"ok": False, "ms": round((time.monotonic() - t0) * 1000), "error": repr(e)[:400], "mode": get_settings().ebay_mode}
