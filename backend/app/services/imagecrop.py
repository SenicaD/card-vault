"""Crop a saved card photo to just the card, so eBay's image search matches the card and not the
hand, table or stream overlay around it."""
import io
import re
import uuid

from PIL import Image, ImageOps
from pydantic import BaseModel, Field

from ..config import get_settings

_NAME = re.compile(r"^[0-9a-f]{32}\.(jpg|png|webp|gif)$")


class CropBox(BaseModel):
    """The kept area as fractions (0-1) of the photo, measured from its top-left corner."""
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)


class CropError(ValueError):
    pass


def crop_saved_image(name: str, box: CropBox) -> tuple[str, bytes]:
    """Save a cropped copy of the stored photo and return (new file name, its bytes). The original stays."""
    if not _NAME.match(name):
        raise CropError("Unknown image")
    path = get_settings().image_dir / name
    if not path.is_file():
        raise CropError("Image file not found")
    try:
        img = ImageOps.exif_transpose(Image.open(path))  # phones store rotation as metadata
        img.load()
    except (OSError, SyntaxError) as e:
        raise CropError("Couldn't read the image") from e
    W, H = img.size
    left, top = round(box.x * W), round(box.y * H)
    right, bottom = min(W, round((box.x + box.w) * W)), min(H, round((box.y + box.h) * H))
    if right - left < 40 or bottom - top < 40:
        raise CropError("The crop is too small")
    out = img.crop((left, top, right, bottom)).convert("RGB")
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=92)
    new = f"{uuid.uuid4().hex}.jpg"
    (get_settings().image_dir / new).write_bytes(buf.getvalue())
    return new, buf.getvalue()
