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
    """The kept area as fractions (0-1) of the photo, measured from its top-left corner.

    x/y/w/h is the plain upright box. If `corners` is given (top-left, top-right, bottom-right,
    bottom-left, each [x, y]) the card is cut out along those four points and straightened instead,
    for photos where the card is tilted or seen at an angle.
    """
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)
    corners: list[tuple[float, float]] | None = Field(default=None, min_length=4, max_length=4)


class CropError(ValueError):
    pass


def _perspective_coeffs(dst: list[tuple[float, float]], src: list[tuple[float, float]]) -> list[float]:
    """The 8 numbers Pillow's PERSPECTIVE transform wants: for each output corner (dst), where in the
    source photo (src) to read from. Solved as an 8x8 linear system by Gaussian elimination."""
    a: list[list[float]] = []
    for (x, y), (u, v) in zip(dst, src):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y, u])
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y, v])
    n = 8
    for i in range(n):
        piv = max(range(i, n), key=lambda r: abs(a[r][i]))
        if abs(a[piv][i]) < 1e-9:
            raise CropError("Those four corners don't make a usable shape")
        a[i], a[piv] = a[piv], a[i]
        for r in range(n):
            if r != i:
                f = a[r][i] / a[i][i]
                a[r] = [rv - f * iv for rv, iv in zip(a[r], a[i])]
    return [a[i][8] / a[i][i] for i in range(n)]


def _straighten(img: Image.Image, corners: list[tuple[float, float]]) -> Image.Image:
    W, H = img.size
    pts = [(cx * W, cy * H) for cx, cy in corners]
    dist = lambda p, q: ((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2) ** 0.5  # noqa: E731
    tl, tr, br, bl = pts
    w = round((dist(tl, tr) + dist(bl, br)) / 2)
    h = round((dist(tl, bl) + dist(tr, br)) / 2)
    if w < 40 or h < 40:
        raise CropError("The crop is too small")
    scale = min(1.0, 2000 / max(w, h))
    w, h = round(w * scale), round(h * scale)
    coeffs = _perspective_coeffs([(0, 0), (w, 0), (w, h), (0, h)], pts)
    return img.transform((w, h), Image.PERSPECTIVE, coeffs, Image.BICUBIC)


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
    if box.corners:
        if any(not (0 <= cx <= 1 and 0 <= cy <= 1) for cx, cy in box.corners):
            raise CropError("The corners must be inside the photo")
        out = _straighten(img.convert("RGB"), box.corners)
    else:
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
