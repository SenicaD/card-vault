"""Turn iPhone HEIC/HEIF photos into JPEGs, so the rest of the scan flow (identify, price, crop) only
ever sees formats it already handles."""
import io

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()

_TYPES = {"image/heic", "image/heif"}
_UNTYPED = {None, "", "application/octet-stream"}  # Chrome on Windows sends HEIC with no type
_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}
_MAX_EDGE = 1600  # same as shrinkImage in the frontend


class HeicError(ValueError):
    pass


def is_heic(content_type: str | None, filename: str | None, data: bytes) -> bool:
    if content_type in _TYPES:
        return True
    if content_type not in _UNTYPED:
        return False
    if (filename or "").lower().endswith((".heic", ".heif")):
        return True
    return data[4:8] == b"ftyp" and data[8:12] in _BRANDS


def heic_to_jpeg(data: bytes) -> bytes:
    try:
        img = Image.open(io.BytesIO(data), formats=["HEIF"])
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((_MAX_EDGE, _MAX_EDGE))  # keeps the aspect ratio, never upscales
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=88)
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as e:
        raise HeicError("Couldn't read this HEIC photo") from e
    return buf.getvalue()
