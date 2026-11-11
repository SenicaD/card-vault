import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .db import init_db
from .routers import cards, ebay, locations, scan
from .services.refresh import refresh_loop

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    # The daily re-pricing job only runs against real eBay data; mock prices would just be noise.
    task = None
    if settings.ebay_mode == "live" and settings.price_refresh_hours > 0:
        task = asyncio.create_task(refresh_loop())
    yield
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="CardVault API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list,
                   allow_methods=["*"], allow_headers=["*"])

for r in (scan.router, scan.price_router, cards.router, locations.router, ebay.router):
    app.include_router(r)

settings.image_dir.mkdir(parents=True, exist_ok=True)
app.mount("/images", StaticFiles(directory=settings.image_dir), name="images")


@app.get("/api/health")
def health():
    return {"ok": True, "identifier": settings.identifier, "ebay": settings.ebay_mode}
