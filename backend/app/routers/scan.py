import asyncio
import uuid

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from sqlmodel import Session

from ..config import get_settings
from ..db import engine
from ..models import PriceQuote
from ..services.ebay import EbayItem
from ..services.heic import HeicError, heic_to_jpeg, is_heic
from ..services.imagecrop import CropBox, CropError, crop_saved_image
from ..services.identify import CardIdentification, get_identifier
from ..services.pricing import quote, summarize, top_listings
from ..services.trace import identification_trace, save_trace

router = APIRouter(prefix="/api/scan", tags=["scan"])

_ALLOWED = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}
_MAX_BYTES = 10 * 1024 * 1024


class ScanResult(BaseModel):
    image_path: str
    image_url: str
    identifier: str
    identification: CardIdentification
    # Present when the scan was requested with ?price=true
    quote: PriceQuote | None = None
    comps: list[EbayItem] = []
    price_error: str | None = None


@router.post("", response_model=ScanResult)
async def scan_card(file: UploadFile = File(...),
                    price: bool = Query(False, description="Also price the card against eBay")) -> ScanResult:
    """Save the photo and return a best-guess identification for the user to confirm.

    With price=true the identification's search query is priced against eBay in the same call.
    A pricing failure doesn't fail the scan; it comes back as price_error.
    """
    data = await file.read()
    content_type = file.content_type
    heic = content_type not in _ALLOWED and is_heic(content_type, file.filename, data)
    if content_type not in _ALLOWED and not heic:
        raise HTTPException(415, f"Unsupported image type {content_type}. Use JPEG, PNG, WebP, GIF or HEIC.")
    if len(data) > _MAX_BYTES:
        raise HTTPException(413, "Image is larger than 10 MB")
    if heic:  # stored and identified as a JPEG, so crop and re-scan work unchanged
        try:
            data = await asyncio.to_thread(heic_to_jpeg, data)  # off the event loop: other requests keep going
        except HeicError as e:
            raise HTTPException(422, str(e)) from e
        content_type = "image/jpeg"

    settings = get_settings()
    settings.image_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{_ALLOWED[content_type]}"
    (settings.image_dir / name).write_bytes(data)

    return await _identify_and_price(data, content_type, name, price, trigger="scan")


class CropRequest(BaseModel):
    image_path: str
    box: CropBox
    price: bool = True


@router.post("/crop", response_model=ScanResult)
async def rescan_cropped(body: CropRequest) -> ScanResult:
    """Crop an already-saved scan photo to the card and identify and price the cropped photo."""
    try:
        name, data = crop_saved_image(body.image_path, body.box)
    except CropError as e:
        raise HTTPException(422, str(e)) from e
    return await _identify_and_price(data, "image/jpeg", name, body.price, source_image=body.image_path,
                                     crop=body.box.model_dump(), trigger="crop")


async def _identify_and_price(data: bytes, content_type: str, name: str, price: bool, source_image: str | None = None,
                              crop: dict | None = None, trigger: str = "scan") -> ScanResult:
    identifier = get_identifier()
    matches = None  # eBay image matching returns the matched listings too
    try:
        if hasattr(identifier, "match"):
            ident, matches = await identifier.match(data)
        else:
            ident = await identifier.identify(data, content_type)
    except Exception as e:  # surface identifier failures without losing the saved photo
        limited, retry_after = _rate_limit(e)
        if limited:  # 429 so the scan queue backs off and retries instead of failing the row
            raise HTTPException(429, f"Identification was rate limited ({identifier.name}). Try again in a moment.",
                                headers={"Retry-After": retry_after} if retry_after else None) from e
        raise HTTPException(502, f"Identification failed ({identifier.name}): {e}") from e

    result = ScanResult(image_path=name, image_url=f"/images/{name}",
                        identifier=identifier.name, identification=ident)
    try:  # a debugging aid must never break a scan
        with Session(engine) as session:
            save_trace(session, identification_trace(identifier.name, ident, matches, name, source_image, crop,
                                                     trigger, len(data)))
    except Exception:
        pass
    if price and matches is not None:
        # Price straight from the visual matches: no second eBay call.
        usable = [m for m in matches if m.price > 0]
        if not ident.name or not usable:
            result.price_error = "No matching listings on eBay. Enter the card details, then re-price."
        else:
            result.quote = PriceQuote(query=ident.search_query, source="ebay-image",
                                      top_listings=top_listings(usable), **summarize(usable))
            result.comps = sorted(usable, key=lambda c: c.price)[:25]
    elif price:
        try:
            result.quote, result.comps = await quote(ident.search_query or ident.name)
        except Exception as e:
            result.price_error = f"eBay search failed: {e}"
    return result


_RATE = ("429", "rate limit", "ratelimit", "too many requests")


def _rate_limit(e: Exception) -> tuple[bool, str | None]:
    """Is this identifier error a rate limit, and did the upstream say when to retry (whole seconds only)?"""
    limited = 429 in (getattr(e, "status_code", None), getattr(e, "status", None)) or any(
        k in str(e).lower() for k in _RATE)
    value = getattr(e, "retry_after", None)
    if value is None and getattr(e, "response", None) is not None:
        value = getattr(e.response, "headers", {}).get("retry-after")
    value = str(value).strip() if value is not None else None
    return limited, value if value and value.isascii() and value.isdigit() else None


price_router = APIRouter(prefix="/api/price", tags=["scan"])


class QuoteRequest(BaseModel):
    query: str = Field(min_length=2, max_length=200)


class QuoteResult(BaseModel):
    quote: PriceQuote
    comps: list[EbayItem]


@price_router.post("", response_model=QuoteResult)
async def price_query(body: QuoteRequest) -> QuoteResult:
    """Price a search query without a saved card, e.g. after correcting a scan before adding it."""
    try:
        q, comps = await quote(body.query.strip())
    except Exception as e:
        raise HTTPException(502, f"eBay search failed: {e}") from e
    return QuoteResult(quote=q, comps=comps)
