import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .db import init_db
from .routers import cards, debug, ebay, locations, portfolio, sales, scan
from .services.refresh import refresh_loop
from .services.sold import SoldCompsClient, get_sold
from .services.sold_refresh import sold_loop

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    # The daily re-pricing job only runs against real eBay data; mock prices would just be noise.
    tasks = []
    if settings.ebay_mode == "live" and settings.price_refresh_hours > 0:
        tasks.append(asyncio.create_task(refresh_loop()))
    if isinstance(get_sold(), SoldCompsClient):  # real sold prices are only fetched when a key is set
        tasks.append(asyncio.create_task(sold_loop()))
    yield
    for task in tasks:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="CardVault API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list,
                   allow_methods=["*"], allow_headers=["*"])

for r in (scan.router, scan.price_router, cards.router, locations.router, ebay.router, sales.router, portfolio.router, debug.router, debug.card_router):
    app.include_router(r)

settings.image_dir.mkdir(parents=True, exist_ok=True)
app.mount("/images", StaticFiles(directory=settings.image_dir), name="images")


@app.get("/api/health")
def health():
    return {"ok": True, "identifier": settings.identifier, "ebay": settings.ebay_mode}
