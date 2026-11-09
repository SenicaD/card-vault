import uuid

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from ..config import get_settings
from ..models import PriceQuote
from ..services.ebay import EbayItem
from ..services.identify import CardIdentification, get_identifier
from ..services.pricing import quote, summarize

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
    if file.content_type not in _ALLOWED:
        raise HTTPException(415, f"Unsupported image type {file.content_type}. Use JPEG, PNG or WebP.")
    data = await file.read()
    if len(data) > _MAX_BYTES:
        raise HTTPException(413, "Image is larger than 10 MB")

    settings = get_settings()
    settings.image_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{_ALLOWED[file.content_type]}"
    (settings.image_dir / name).write_bytes(data)

    identifier = get_identifier()
    matches = None  # eBay image matching returns the matched listings too
    try:
        if hasattr(identifier, "match"):
            ident, matches = await identifier.match(data)
        else:
            ident = await identifier.identify(data, file.content_type)
    except Exception as e:  # surface identifier failures without losing the saved photo
        raise HTTPException(502, f"Identification failed ({identifier.name}): {e}") from e

    result = ScanResult(image_path=name, image_url=f"/images/{name}",
                        identifier=identifier.name, identification=ident)
    if price and matches is not None:
        # Price straight from the visual matches: no second eBay call.
        usable = [m for m in matches if m.price > 0]
        if not ident.name or not usable:
            result.price_error = "No matching listings on eBay. Enter the card details, then re-price."
        else:
            result.quote = PriceQuote(query=ident.search_query, source="ebay-image", **summarize(usable))
            result.comps = sorted(usable, key=lambda c: c.price)[:25]
    elif price:
        try:
            result.quote, result.comps = await quote(ident.search_query or ident.name)
        except Exception as e:
            result.price_error = f"eBay search failed: {e}"
    return result


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
