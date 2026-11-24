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

## Deploy to the UGREEN NAS

Run from the repository in WSL or on a Linux machine with Git, Bash and SSH access to
`senicad@ugreen-nas-1`. The NAS's SSH host key must already be trusted and the account
must be able to run Docker Compose.

```bash
./scripts/deploy-ugreen.sh --check  # read-only check of the NAS project and live app
./scripts/deploy-ugreen.sh          # deploy the current commit
./scripts/deploy-ugreen.sh <git-revision>
```

The script packages committed source, so commit any local changes you want to deploy.
It briefly pauses the backend to back up SQLite and the photos, verifies the archive,
and resumes the old app while the new images build. If the data changes during the build,
the script takes a fresh backup at cutover. Backups and checksums are stored on the NAS
under `/volume1/docker/projects/cardvault-backups/`.

Only the two CardVault services are restarted. The script checks the live app at
`http://ugreen-nas-1:3020`, including the configured identification and eBay modes,
settings, inventory, SQLite integrity and photo checksums. If the update fails those
checks, the script restores the previous images and source and verifies the app again.
The verified archives are available if the database or photos need a separate restore.

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
   A card whose number is a set code (e.g. CRA-JMI) is only priced from listings that show that code or no other
   hyphenated code (words like SP2 or PS5 don't count as another card's code), and a redemption card only from
   redemption listings (other cards never from redemption listings).
   A card counts as a redemption when its "Redemption card" box is ticked (set from the identification when scanned,
   editable on the card page) or when its name, set, variant, number or search words say "redemption".
   When the middle half of the prices spans more than `MAX_PRICE_SPREAD` (default 100%) of the median, the card is
   flagged "wide spread" and gets no asking value until picking its version, unticking listings or a later re-price
   narrows it, and flagged cards are left out of value totals.
   A flagged card's page lists the price groups found among its listings (typical price, range, count and most
   common version), so you can tell which version is which.
   The **Settings** page (in the sidebar) has three settings for this. "Minimum listings per price group" (2 to 20,
   default 2) leaves smaller groups out as strays; it only shapes the groups shown on the card page. "When prices
   spread too widely" is the default rule for a card that would otherwise be flagged: flag it (the default), or value
   it with Highest, Median or Lowest, the average of the N most expensive listings, the N in the middle of the price
   order, or the N cheapest. N is "Listings to price from" (1 to 50, default 10). "Ignore listings priced above" (empty by default) is a multiple of each card's own median (1.5 to 100): with 5, a listing over 5× the median of the card's listings is left out first, so the N are picked from what is under it. It is relative so it scales with the card; a fixed dollar cap would cap every card's value. The window is taken from the card's
   asking listings after the same outlier trim as the asking median; with N or fewer listings left, every rule takes
   the average of all of them, and a card with no listings left is still flagged. The card page says which rule set
   the value and lists the groups.
   Changes apply the next time a card is priced (Refresh value, ticking or unticking listings, the daily or bulk
   re-price, or the lookup after adding a card); saving does not re-price existing cards.
   A card can override the default with "When this card's prices spread too widely" on its page, shown while its
   prices spread too widely. That choice re-values the card straight away from its saved listings.
5. **Sell**: "List on eBay" creates an inventory item, an offer, and publishes it. The card moves to Listed.

The Storage list and the card page's Sold / gone, Matching listings and Price lookups tables can be sorted by clicking a column header, searched, and paged.

The queue lives in memory: it survives moving between pages, but not a reload (the browser warns first).

## Going live

### Real card identification (Claude vision)
```
IDENTIFIER=claude
ANTHROPIC_API_KEY=sk-ant-...
```
The prompt asks for category, name, set, year, number (as printed, letter-only codes like `CRA-JMI`
too), variant, slab grade, condition, a confidence score, an eBay-ready search query, and whether the
card is a redemption and/or an autograph (`is_redemption`, `is_auto`). See `app/services/identify/claude.py`.

### Card identification by eBay image search (no AI key)
```
IDENTIFIER=ebay
EBAY_MODE=live
```
Sends the photo to eBay's Browse `search_by_image`, then reads name, year, set, number, variant and
grade from what the best-matching listing titles agree on. Letter-only codes like `CRA-JMI` are read
when a title has no numeric one, and the redemption and autograph flags are set when at least half the
titles say "redemption" or "auto"/"autograph"/"RPA". Confidence is how many of the top matches
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
Redemption cards (as in step 4: the "Redemption card" box ticked, or "redemption" in the card's text) skip the
picture comparison too, because they all share a generic back: they are matched on card number and redemption
status instead, with a note in the trace. The thresholds are in `app/services/lookalike.py`.

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
