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
   Each photo joins a queue and is downscaled, uploaded, identified, and priced against eBay in one call
   (`POST /api/scan?price=true`), three at a time. Pick a "File new cards in" location once for the batch.
3. **Review**: the queue shows each card's identification, confidence and eBay median (low–high).
   Click a row to correct fields; if you change the eBay search, "Re-price" (`POST /api/price`) fetches
   a new quote. "Add N to inventory" saves every ready card, and the scan-time quote becomes the card's
   first price snapshot, so nothing is fetched twice. Turn on **Auto-add** to skip review for cards
   identified at 80% confidence or higher.
4. **Value**: on a card's page, "Refresh value" re-runs the eBay search (filters lots and reprints, trims
   outliers) and stores a new low / median / high snapshot. The median becomes the card's estimate.
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
