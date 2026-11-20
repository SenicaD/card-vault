"""How sure we are that a listing's picture shows the same card as your photo.

With the optional install (requirements-clip.txt), both pictures are cropped to the card and straightened with
OpenCV, then compared as CLIP image embeddings: a 0-100 confidence. Borderline listings get a second look: local
features (SIFT) matched between the two cards and confirmed with RANSAC, which finds the same printed design even
when the photos differ. Without the install, or when the model can't be loaded, the colour score is used instead.
"""
import asyncio
import hashlib
import threading
import time
from collections import OrderedDict
from typing import NamedTuple

import httpx

from .similarity import _MAX_BYTES, _ebay_image, score_urls

LOOKALIKE_MIN_CONFIDENCE = 60  # CLIP confidence below this is clearly not your card: the listing is left out
LOOKALIKE_BORDERLINE = 70  # from LOOKALIKE_MIN_CONFIDENCE up to this, kept only if the feature check finds your card
LOOKALIKE_MIN_MATCHES = 40  # matching feature points (RANSAC inliers) that confirm the same card

CLIP_MODEL = ("ViT-B-32", "laion2b_s34b_b79k")  # weights are downloaded on first use
_RETRY_AFTER = 600  # seconds before trying to load the model again after it failed
_CACHE_SIZE = 5000  # embeddings kept in memory, by picture URL


class Look(NamedTuple):
    method: str  # "clip" or "colour"
    scores: dict[str, int]  # picture URL -> 0-100; pictures that didn't load are left out
    matches: dict[str, int]  # borderline picture URL -> matching feature points (clip only)
    note: str | None = None  # why CLIP wasn't used


class _NotInstalled(Exception):
    pass


_lock = threading.Lock()
_clip = None  # (model, preprocess, device) once loaded
_failed_at = 0.0
_embeddings: "OrderedDict[str, object]" = OrderedDict()


def _model():
    """The CLIP model, loaded (and downloaded) once. Raises when it isn't installed or can't be loaded."""
    global _clip, _failed_at
    try:
        import cv2  # noqa: F401
        import open_clip
        import torch
    except ImportError as e:
        raise _NotInstalled(str(e)) from e
    with _lock:
        if _clip is not None:
            return _clip
        if _failed_at and time.monotonic() - _failed_at < _RETRY_AFTER:
            raise RuntimeError("it failed to load a few minutes ago")
        try:
            device = "cuda" if torch.cuda.is_available() else "cpu"
            model, _, preprocess = open_clip.create_model_and_transforms(CLIP_MODEL[0], pretrained=CLIP_MODEL[1], device=device)
            model.eval()
        except Exception:
            _failed_at = time.monotonic()
            raise
        _clip = (model, preprocess, device)
        return _clip


def _fallback_note(e: Exception) -> str:
    if isinstance(e, _NotInstalled):
        return "CLIP isn't installed (backend/requirements-clip.txt), so listing pictures were compared by colour."
    return f"The CLIP model couldn't be loaded ({e!r}), so listing pictures were compared by colour."


def card(data: bytes):
    """The card in a picture, cropped and straightened (BGR array); the whole picture if no card outline is found.

    The card is the largest four-sided outline with a card's proportions (a toploader or slab counts too).
    Returns None for bytes that aren't a picture.
    """
    import cv2
    import numpy as np

    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    scale = min(1.0, 600 / max(h, w))
    small = cv2.resize(img, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    area = small.shape[0] * small.shape[1]
    best, best_score = None, 0.0
    for lo, hi in ((30, 90), (50, 150), (80, 200)):  # edges at a few strengths: glare and sleeves vary a lot
        edges = cv2.dilate(cv2.Canny(gray, lo, hi), np.ones((3, 3), np.uint8))
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for c in sorted(contours, key=cv2.contourArea, reverse=True)[:15]:
            if cv2.contourArea(c) < 0.12 * area:
                break
            hull = cv2.convexHull(c)
            quad = cv2.approxPolyDP(hull, 0.03 * cv2.arcLength(hull, True), True)
            if len(quad) != 4:
                quad = cv2.boxPoints(cv2.minAreaRect(hull))
            quad = _corners(quad.reshape(4, 2).astype(np.float32))
            wd = (np.linalg.norm(quad[0] - quad[1]) + np.linalg.norm(quad[3] - quad[2])) / 2
            ht = (np.linalg.norm(quad[0] - quad[3]) + np.linalg.norm(quad[1] - quad[2])) / 2
            quad_area = cv2.contourArea(quad)
            if not 0.6 <= min(wd, ht) / max(wd, ht) <= 0.85 or quad_area > 0.98 * area:
                continue  # not a card's shape, or just the picture's own edge
            score = quad_area / area * min(cv2.contourArea(c) / max(quad_area, 1), 1)  # big, and filled by the outline
            if score > best_score:
                best, best_score = (quad / scale, wd >= ht), score
    if best is None:
        return img
    quad, landscape = best
    size = (700, 500) if landscape else (500, 700)
    target = np.array([[0, 0], [size[0], 0], [size[0], size[1]], [0, size[1]]], np.float32)
    return cv2.warpPerspective(img, cv2.getPerspectiveTransform(quad, target), size)


def _corners(pts):
    """Top-left, top-right, bottom-right, bottom-left."""
    import numpy as np
    s, d = pts.sum(1), np.diff(pts, axis=1).ravel()
    return np.array([pts[s.argmin()], pts[d.argmin()], pts[s.argmax()], pts[d.argmax()]], np.float32)


def feature_matches(a, b) -> int:
    """How many SIFT feature points of two card pictures agree on one perspective mapping (RANSAC inliers)."""
    import cv2
    import numpy as np

    sift = cv2.SIFT_create()
    found = []
    for img in (a, b):
        img = cv2.resize(img, (500, 700) if img.shape[0] >= img.shape[1] else (700, 500))
        found.append(sift.detectAndCompute(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), None))
    (ka, da), (kb, db) = found
    if da is None or db is None or len(ka) < 2 or len(kb) < 2:
        return 0
    good = [m for m, n in (p for p in cv2.BFMatcher().knnMatch(da, db, k=2) if len(p) == 2) if m.distance < 0.75 * n.distance]
    if len(good) < 8:
        return 0
    _, mask = cv2.findHomography(np.float32([ka[m.queryIdx].pt for m in good]),
                                 np.float32([kb[m.trainIdx].pt for m in good]), cv2.RANSAC, 5.0)
    return int(mask.sum()) if mask is not None else 0


def _embed(clip, img):
    import cv2
    import torch
    from PIL import Image

    model, preprocess, device = clip
    with torch.no_grad():
        e = model.encode_image(preprocess(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))).unsqueeze(0).to(device))
    return torch.nn.functional.normalize(e, dim=-1)[0].cpu()


def _remember(key: str, value) -> None:
    _embeddings[key] = value
    _embeddings.move_to_end(key)
    while len(_embeddings) > _CACHE_SIZE:
        _embeddings.popitem(last=False)


def _clip_scores(clip, photo: bytes, mine, urls: list[str], pics: dict[str, bytes]) -> dict[str, int]:
    with _lock:
        key = "photo:" + hashlib.sha1(photo).hexdigest()
        if key not in _embeddings:
            _remember(key, _embed(clip, mine))
        ref = _embeddings[key]
        out = {}
        for u in urls:
            if u not in _embeddings:
                img = card(pics[u]) if u in pics else None
                if img is None:
                    continue
                _remember(u, _embed(clip, img))
            out[u] = max(0, round(100 * float(ref @ _embeddings[u])))
        return out


async def _fetch(urls: list[str], concurrency: int = 8) -> dict[str, bytes]:
    sem = asyncio.Semaphore(concurrency)
    out: dict[str, bytes] = {}

    async def one(client: httpx.AsyncClient, url: str) -> None:
        async with sem:
            try:
                r = await client.get(url)
                if r.status_code == 200 and len(r.content) <= _MAX_BYTES:
                    out[url] = r.content
            except Exception:
                pass  # a picture that won't load is just left unscored

    async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
        await asyncio.gather(*(one(client, u) for u in urls))
    return out


async def compare(photo: bytes, urls: list[str]) -> Look:
    """Confidence that each listing picture shows the card in `photo`. Raises if the photo can't be read."""
    try:
        clip = await asyncio.to_thread(_model)
    except Exception as e:
        return Look("colour", await score_urls(photo, urls), {}, _fallback_note(e))
    mine = await asyncio.to_thread(card, photo)
    if mine is None:
        raise ValueError("the card's photo can't be read")
    urls = [u for u in dict.fromkeys(urls) if _ebay_image(u)]
    pics = await _fetch([u for u in urls if u not in _embeddings])
    scores = await asyncio.to_thread(_clip_scores, clip, photo, mine, urls, pics)
    borderline = [u for u, s in scores.items() if LOOKALIKE_MIN_CONFIDENCE <= s < LOOKALIKE_BORDERLINE]
    pics |= await _fetch([u for u in borderline if u not in pics])  # cached embeddings skipped the download

    def check() -> dict[str, int]:
        cards = {u: card(pics[u]) for u in borderline if u in pics}
        return {u: feature_matches(mine, img) for u, img in cards.items() if img is not None}

    return Look("clip", scores, await asyncio.to_thread(check))
