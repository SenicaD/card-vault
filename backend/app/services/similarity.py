"""How alike two card pictures look, by colour. Used to rank eBay listings against your own photo.

Colour is the main thing that tells parallels apart (a green card vs a blue one of the same set), and it
is cheap to compare. It is a hint, not proof: slabs, backgrounds and lighting move the numbers.
"""
import asyncio
import io
from urllib.parse import urlparse

import httpx
from PIL import Image

_HUE_BINS = 12
_MAX_BYTES = 3 * 1024 * 1024


def color_signature(data: bytes) -> list[float]:
    """A small colour histogram of the middle of the picture: 12 hue bins plus dark / grey / light."""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = img.size
    img = img.crop((int(w * .12), int(h * .12), int(w * .88), int(h * .88))).resize((48, 48)).convert("HSV")
    bins = [0.0] * (_HUE_BINS + 3)
    for hue, sat, val in img.getdata():
        if val < 50:
            bins[_HUE_BINS] += 1  # dark
        elif sat < 60:
            bins[_HUE_BINS + (2 if val >= 170 else 1)] += 1  # light / grey
        else:
            bins[min(_HUE_BINS - 1, hue * _HUE_BINS // 256)] += 1
    total = sum(bins) or 1
    return [b / total for b in bins]


def similarity(a: list[float], b: list[float]) -> int:
    """0-100: how much of the two histograms overlap."""
    return round(100 * sum(min(x, y) for x, y in zip(a, b)))


def _ebay_image(url: str) -> bool:
    p = urlparse(url)
    host = p.hostname or ""
    return p.scheme == "https" and (host == "ebayimg.com" or host.endswith(".ebayimg.com"))


async def score_urls(photo: bytes, urls: list[str], concurrency: int = 8) -> dict[str, int]:
    """Colour similarity to `photo` for each listing picture that could be fetched (others are left out)."""
    mine = color_signature(photo)
    sem = asyncio.Semaphore(concurrency)
    out: dict[str, int] = {}

    async def one(client: httpx.AsyncClient, url: str) -> None:
        if not _ebay_image(url):
            return
        async with sem:
            try:
                r = await client.get(url)
                if r.status_code == 200 and len(r.content) <= _MAX_BYTES:
                    out[url] = similarity(mine, color_signature(r.content))
            except Exception:
                pass  # a picture that won't load is just left unranked

    async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
        await asyncio.gather(*(one(client, u) for u in dict.fromkeys(urls)))
    return out
