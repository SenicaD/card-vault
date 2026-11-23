# CardVault

Scan trading cards with your phone, track where each one is stored, price them against eBay,
and post listings. Handles sports cards, Pokémon, Magic, Yu-Gi-Oh! and other TCGs.

Runs fully offline in demo mode: card identification and eBay are both mocked until you add keys.

```
frontend/  Nuxt 3 SPA (port 3000). Proxies /api and /images to the backend, so a phone only needs port 3000.
backend/   FastAPI + SQLModel + SQLite (port 8000)
```

## Run it

```bash
# API
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000

# UI (second terminal)
cd frontend
npm install
npm run dev            # binds 0.0.0.0:3000
```

Open `http://localhost:3000`, or from your phone on the same Wi-Fi, `http://<your-machine-LAN-IP>:3000`.
Scanning uses the phone's native camera via a file input (`capture="environment"`), which works over
plain http. Live in-browser camera preview (getUserMedia) would need https.

Tests: `cd backend && pytest`. API docs: `http://localhost:8000/docs`.

## The flow

1. **Storage**: create the binders, boxes and shelves your cards live in.
2. **Scan & price**: take photos one after another, upload a batch, or drag photos onto the page.
   You can also choose or drop a whole folder (subfolders included); files in unsupported formats are skipped with a message.
   HEIC/HEIF photos (for example from an iPhone) are accepted and converted to JPEG on the server.
   Each photo joins a queue and is downscaled, uploaded, identified, and priced against eBay in one call
   (`POST /api/scan?price=true`), six at a time. A photo that hits a rate limit waits and retries by itself
   (up to 4 tries) before it shows as failed. Pick a "File new cards in" location once for the batch.
3. **Review**: the queue shows each card's identification, confidence and eBay median (low–high).
   Click a row to correct fields; if you change the eBay search, "Re-price" (`POST /api/price`) fetches
   a new quote. "Add N to inventory" saves every ready card, and the scan-time quote becomes the card's
   first price snapshot. Adding a card also starts a full eBay lookup in the background (one card at a
   time), which fills its listings table and graded price within a moment. Tick ready or failed rows to
   add or remove just those, and on a desktop click a column header to sort the queue (cards are still
   added in scan order). Turn on **Auto-add** to skip review for cards identified at 80% confidence or
   higher.
4. **Value**: on a card's page, "Refresh value" re-runs that eBay search (filters lots and reprints, trims
   outliers and, for a card with a photo and the optional CLIP install, leaves out listings whose picture
   clearly doesn't look like it) and stores a new low / median / high snapshot. The median becomes the card's estimate.
   A card whose number is a set code (e.g. CRA-JMI) is only priced from listings that show that code or no code, and a
   redemption card only from redemption listings (other cards never from redemption listings).
5. **Sell**: "List on eBay" creates an inventory item, an offer, and publishes it. The card moves to Listed.

The queue lives in memory: it survives moving between pages, but not a reload (the browser warns first).

## Going live

### Real card identification (Claude vision)
```
IDENTIFIER=claude
ANTHROPIC_API_KEY=sk-ant-...
```
The prompt asks for category, name, set, year, number, variant, slab grade, condition, a confidence
score, and an eBay-ready search query. See `app/services/identify/claude.py`.

### Card identification by eBay image search (no AI key)
```
IDENTIFIER=ebay
EBAY_MODE=live
```
Sends the photo to eBay's Browse `search_by_image`, then reads name, year, set, number, variant and
grade from what the best-matching listing titles agree on. Confidence is how many of the top matches
are clearly the same listing. The matches double as the price comps, so each scan is one eBay call.
Visually identical cards (base vs. colour parallels, raw vs. slabbed) can mix, so check the fields and
re-price with a tighter search when it matters. See `app/services/identify/ebay_image.py`.

### Look-alike pricing (CLIP + OpenCV, optional)
```bash
cd backend
pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-clip.txt   # drop the index for a GPU build
```
With a card photo, a lookup compares each listing's picture with it (the first 40 in eBay's order) and
leaves out the listings that clearly show another card. Both pictures are cropped to the card and straightened
with OpenCV, then compared with CLIP (`ViT-B-32`, about 600 MB, downloaded on first use into the Hugging Face
cache; the first lookup after a restart takes a few seconds longer). Below 80, SIFT feature matching also
checks for the same printed design: from 55 to 80 a listing is kept with 30+ matching points, and below 55 only
with 100+ (a strong match rescues a picture CLIP misjudges). Debug mode shows each listing's confidence and
matching points in "How this price was calculated". Without this install, or if the model can't be downloaded,
the filter is skipped: no listing is left out and prices are worked out as before, with a note in the trace.
The thresholds are in `app/services/lookalike.py`.

### eBay
Set `EBAY_MODE=live` and fill in keys from developer.ebay.com. Start with `EBAY_ENV=sandbox`.

| Feature | eBay API | Auth | Notes |
|---|---|---|---|
| Price lookup | Browse `item_summary/search` | App token (client credentials) | Only needs client ID + secret |
| Listing | Inventory `inventory_item` → `offer` → `publish` | **User** token, `sell.inventory` scope | Needs business policies + merchant location |

Things to know before flipping the switch:

- **Browse returns active listings, not sold prices.** Asking prices skew high. Sold comps require
  the Marketplace Insights API, which eBay gates behind an application. The snapshot `source`
  field (`ebay-active` vs `ebay-sold`) is there so you can add it later without a schema change.
- **Listing needs public photo URLs.** eBay fetches images itself, so set `PUBLIC_BASE_URL` to an https
  URL that serves the backend's `/images` (a Cloudflare tunnel works), or upload images via eBay's Media API.
- **Trading-card condition descriptors.** eBay requires grader/grade descriptors (graded) or card
  condition (ungraded) for categories 261328 and 183454. `live.py` has a TODO where those map in.
- `live.py` follows eBay's published REST shapes but has not been run against the sandbox yet.

## Swapping implementations

Both external dependencies sit behind small protocols, selected by env var:

- `app/services/identify/` — `CardIdentifier.identify(bytes, media_type) -> CardIdentification`
- `app/services/ebay/` — `EbayClient.search_active(query)` and `create_listing(req, publish)`

Adding, say, a TCGplayer or PSA price source is another implementation of the same shape.

## Ideas for next steps

- Sold-price comps (Marketplace Insights, or 130point-style scraping) and a price history chart
- eBay OAuth consent flow with refresh tokens instead of a pasted user token
- Order sync (Fulfillment API) so sold listings flip cards to Sold automatically
- Multi-card scan: one photo of a 9-pocket binder page, split into nine cards
- Postgres via `DATABASE_URL`, plus Alembic migrations
