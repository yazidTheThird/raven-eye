# Raven Eye

A single-page numismatic release tracker for coins, bars, and precious-metal collectibles. Raven Eye collects public seller and mint listings, preserves its observations, and presents a searchable snapshot with product images, source links, prices, availability, and a personal watchlist.

This standalone project lives at [yazidTheThird/raven-eye](https://github.com/yazidTheThird/raven-eye), with its interface at [Raven Eye](https://yazidthethird.github.io/raven-eye/). It contains a real data collector, the static interface, tests, and a GitHub Actions workflow. No account credentials, paid API keys, or application server are required.

## Reading the data

| Field | Meaning |
| --- | --- |
| First seen | The first time this Raven Eye installation observed the listing. This is never backdated to a seller timestamp. |
| Published | The source's listing-publication timestamp, when available. It is not a verified worldwide launch date. |
| Last checked | When Raven Eye last successfully retrieved that listing. |
| Available / Presale / Out of stock | The last observed source status, with its evidence retained in the dataset. Confirm at the seller before ordering. |
| Upcoming | The source announces a coming release; this does not mean orders are currently accepted. |
| Backorder | The source explicitly marks an item as backordered, distinct from a new-release presale. Fulfilment timing must be checked with the seller. |
| Unknown | The source did not expose reliable purchase availability. |
| Price | The source price in its own currency. Shipping, tax, currency conversion, and checkout-specific pricing are not included. |
| Mintage / weight | Source-provided or conservatively extracted product specifications; missing values stay blank. Shipping weight is not treated as precious-metal weight. |

The source directory covers sovereign mints, private makers, aftermarket colorizers, refiners, and dealers around the world. **Automatically tracked** sources have supported public catalogue feeds. Entries without a direct feed explain the gap. Where a named dealer collection is connected, its health is reported separately; remaining **Directory only** sources are links for discovery. A directory entry is not evidence of live inventory tracking; it is excluded from the connected-source health total.

Coverage is a bounded selection of supported feeds, not the entire market. Each automatic source reports its coverage, pagination limit, and collection health. Multiple dealers can list the same release; they remain separate source listings so stock and prices keep their provenance. Products that disappear from a bounded feed are not automatically labelled sold out. The last successful observation remains visible and can become stale. A reported brand comes from the source's vendor or manufacturer field; a retailer's vendor label is not independent verification of who physically made the coin.

Initial source imports, newly added focused collections, and discoveries from rotating older catalogue pages are baselines. They do not create misleading new-release alerts for historical stock. Later observations can establish price and availability changes, including restocks and sellouts. A sellout signal means the listing changed status between observations; it does not measure units sold, collector demand, or the precise moment the stock changed. The first run cannot reconstruct earlier sales or price history.

## Colorized silver focus and retrieval

The default view is **Colorized silver coins**. **All colorized silver** also includes bars and other physical collectibles; **Copper** includes copper coins, rounds, bars, medals, statues, and other physical collectibles; **All releases** includes every retained listing. Material, product form, and color finish are separate checks. Silver-plated copper and gilding alone do not qualify as colorized silver coins. Unknown finishes remain discoverable in All releases.

Classification reads the full product description, all available tags, and structured finish attributes before shortening text for display. Positive finish evidence is shown in the details and exported to CSV. Packaging colors and generic “art coin” wording are not sufficient. This is text-based classification: it does not infer a finish from images, and sources with incomplete metadata can still have missed matches.

Retrieval has three layers:

1. Refresh each supported source's latest catalogue window on every run.
2. Also read seven verified collections/categories for Chromatics, Parthava coloured coins, GR Reserve colorized coins, Coin Shoppe colorized silver, PiK ennobled coins, NumisCollect PopCoins, and Art in Coins color-finish tags. Category membership aids discovery; each listing still requires its own material and finish evidence.
3. Rotate through four additional catalogue pages per run at those seven sources and My Collectables, starting beyond the fresh window. Successful pages advance a persistent cursor; failed pages are retried. Scans restart on a confirmed end or the configured page-100 cap. Reaching the cap is not proof of complete coverage.

Listings from overlapping feeds merge using the seller's original product ID, preserving first-seen dates and history. Different sellers' offers remain separate. Sources & coverage exposes capped windows, focused-feed results, and deeper-scan progress, making incomplete retrieval visible. Directory-only sources still have no automatic product monitoring.

Watchlist selections are stored in the browser on this device. They do not sync between browsers and do not send email or push notifications.

## Copper tracking

The **Copper** focus and sidebar shortcut include plain, colorized, and silver- or gold-plated copper products. Category, stock, source, search, and watchlist filters work within that focus. Copper-plated silver, copper-colored finishes, and copper-nickel alloys are not treated as copper bullion. Explicit composition takes precedence over theme words such as “Gold Tribute” or broad shop categories.

In addition to copper listings found in the existing mixed-metal feeds, Raven Eye reads Metal2go’s public Copper collection, bounded to four pages of 250 entries per run. Listing-level material evidence still controls classification because that collection also contains other metals. These are seller listings, not a count of unique designs across shops. First-seen, publication, availability, and price-history rules are the same as for silver.

## Direct mint and colorizer tracking

Carpathian Mint and Hayleybug Mint are now automatic sources, rather than directory-only entries. Direct feeds also cover Asturmint, SCE Coins, Precious Metal Collectors, Lithuanian Mint, Arverni Mint, and Pressburg Mint.

- **Carpathian Mint:** reads its public own-mint catalogue, following the site's pagination links through up to eight pages and 80 product details. Structured product metadata supplies images, material, weight, mintage, finishing, and publication dates. This manufacturer catalogue does not expose retail stock or prices, so availability remains unconfirmed. Partner listings outside that category are excluded.
- **Asturmint:** reads both the maker catalogue and separate retail listings. Only the source's verified `-store/` product paths establish purchase availability; catalogue entries remain unconfirmed even when generic storefront flags say they are in stock. Catalogue and retail records may describe the same design and retain their separate source IDs and URLs.
- **SCE Coins, Precious Metal Collectors, Lithuanian Mint, Arverni Mint, Pressburg Mint, and Hayleybug Mint:** use public storefront feeds with source-reported prices and availability. Lithuanian Mint uses its English catalogue. Hayleybug also rotates through two older catalogue pages per run, beyond its latest 400 entries, up to page 100.

These sources are subject to the same first-observation baselines and history rules as all other feeds. Product finishing attributes such as “Additional: UV printing” count as color evidence; printed packaging and gilding alone do not. New source imports are not announcements of new coin releases.

## Agaunews guide coverage

The [50-entry comparison](docs/agaunews-coverage.md) maps every named organization in the Agaunews sponsor guide to a configured monitoring route: 32 direct public catalogues/shops and 18 named dealer routes as of 20 November 2026. Three generic collecting categories are not counted as mints. Eleven previously absent guide names are now represented. Banco de México is kept separate from the physical Casa de Moneda mint, and Mint of MK is distinct from MK Barz.

This expansion adds 16 automatic source connections and 19 targeted dealer feeds. Direct additions include Armageddon, Artisan, Royal Krakow, Tower, Numistrade, Bank of Russia, Coins Today, Mint of Gdańsk, Power Coin, Mint of Poland, Austrian Mint, Monnaie de Paris, NZ Post, The Coin Company, and Emporium Hamburg’s owned retailer. A separate Magikos collection supplies JVP listings while JVP’s own shop is paused. Existing sources retain their IDs and observation history.

Dealer collection names are searchable and appear in listing details without replacing the seller or its reported manufacturer. Sources & coverage distinguishes direct feeds, dealer coverage, and directory-only entries, including each dealer check’s result. Initial imports remain baselines. The guide comparison describes configured routes, not guaranteed healthy or exhaustive catalogues; consult the current snapshot for collection health.

Public HTML readers use observed catalogue links, explicit product specifications, and scoped Product/Offer data. Visible preorder labels override misleading generic stock metadata; contradictory retail signals remain unconfirmed. Manufacturer-only catalogues do not imply retail availability. Bank of Russia’s exact issuer release dates appear separately from source-publication dates. Detail enrichment is bounded and older verified specifications are retained when only a catalogue card is refreshed.

Chinese coverage is currently the dealer’s Panda programme, including aftermarket versions, not all Chinese issues. Pobjoy’s direct coin sales ended in 2023; dealer tracking concerns historical releases. Numistrade covers current release pages and their variants. English-only Mint of Poland coverage can lag Polish releases. All other per-source bounds and limitations are listed in the comparison and application.

The retained snapshot is compressed in Git as `data/releases.json.gz` to keep catalogue growth below GitHub’s individual-file limit. Collection migrates the legacy JSON only after writing a valid replacement. The static build emits ordinary `data/releases.json` for the browser; first-seen dates and history are unchanged.

## Local use

Requirements: Node.js 22 or newer and Python 3.11 or newer. The application has no package-install step.

From the repository root:

```bash
python3 scripts/collect.py
npm test
npm run build
python3 -m http.server 4173 --directory dist
```

Open the local address printed by Python's server. Serve the page over HTTP; opening `index.html` as a `file:` URL prevents normal dataset loading in many browsers.

```text
index.html                Interface
styles.css                Responsive visual design
raven.svg                 Raven Eye logo and favicon
app.js                    Rendering and interactions
lib.mjs                   Shared filtering and formatting logic
sources.json              Automatic feeds and official source directory
scripts/collect.py        Public-feed collector and history merge
scripts/catalog_html.py   Bounded public maker-catalogue reader
data/releases.json.gz     Compressed persistent source observations
scripts/build.mjs         Static-only deployment packaging
tests/                    Data and interface-logic checks
.github/workflows/        Scheduled refresh and Pages deployment
dist/                     Generated static site, excluded from Git
```

## GitHub Pages and scheduled collection

The included `.github/workflows/refresh-and-deploy.yml` is configured to run on pushes to `main`, on manual dispatch, and every six hours at 00:17, 06:17, 12:17, and 18:17 UTC. A schedule in this repository is configuration, not proof that GitHub Actions or Pages is enabled.

To activate the standalone repository:

1. Push this project to the `main` branch of `yazidTheThird/raven-eye`.
2. In **Settings → Pages**, choose **GitHub Actions** as the publishing source.
3. Allow the workflow's requested repository-content and Pages permissions. Repository or organization policies may restrict these.
4. Open **Actions → Refresh and deploy Raven Eye**, run the workflow, and confirm both jobs finish successfully. The deployment job returns the site's actual URL.

Each run tests the code, collects source observations, builds `dist/`, commits an updated dataset when needed, and publishes the resulting site in the same run. It does not rely on a bot-authored commit triggering a second workflow. Refreshes are serialized using this repository's `raven-eye-refresh-and-pages` concurrency group, and no force-push is used.

GitHub may delay scheduled work, and inactive public repositories can have schedules disabled. Source websites can also change their schemas or become unavailable. The interface's last-update and source-health details are the practical way to check freshness. Its reload control reads the latest published snapshot; it does not run the collector from the browser.

## Adding sources

Add only public, permitted endpoints to `sources.json`, using an adapter supported by `scripts/collect.py`. Test extraction against real products and ambiguous stock states before enabling a new source. Do not treat promotional copy as a live inventory count. Preserve first-seen timestamps and source identity when changing adapters.

Automatic JSON adapters support Shopify product feeds, WooCommerce Store API products, and WordPress product posts. Additional bounded readers cover verified PrestaShop catalogue data, public retail Product/Offer markup, Royal Krakow, Numistrade, Bank of Russia, and Coins Today. Their configured page/detail budgets are independent; absent or conflicting fields stay unknown. `maxPages` sets the latest JSON window of up to four pages. The `html_catalog` adapter follows observed same-origin pagination and product links, up to eight catalogue pages and 80 product details, and requires explicit Product metadata; catalogue entries do not establish retail availability. Optional `discoveryFeeds` entries use the same adapter and specify `id`, `label`, `endpoint`, and `maxPages` (up to four each). Optional `catalogueBackfill: {"pagesPerRun": 4, "maxPage": 100}` adds bounded rotating deeper scans. Each source's coverage text explains the scope. Request pacing respects published crawl delays. Blocked endpoints are not bypassed. A failed source keeps its previous observations and their actual last-check times.

Use `type: "directory"` with `trackingReason` for verified official sources without a supported feed. Include `kind`, `region`, and `evidenceUrl` for the searchable directory. The collector makes no requests to directory-only sources. Feed availability can change, so a supported adapter is not a promise of perpetual access.

Images and product names belong to their respective owners. Raven Eye links to the original listings and is an independent monitoring interface, not an affiliated storefront.
