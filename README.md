# Raven Eye

A single-page numismatic release tracker for coins, bars, and precious-metal collectibles. Raven Eye collects public seller and mint listings, preserves its observations, and presents a searchable snapshot with product images, source links, prices, availability, and a personal watchlist.

This standalone project is intended for its own repository, `yazidTheThird/raven-eye`, and its own GitHub Pages site. It contains a real data collector, the static interface, tests, and a GitHub Actions workflow. No account credentials, paid API keys, or application server are required.

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

Coverage is a bounded selection of supported feeds, not the entire market. Each source reports its coverage and collection health. Multiple dealers can list the same release; they remain separate source listings so stock and prices keep their provenance. Products that disappear from a bounded feed are not automatically labelled sold out. The last successful observation remains visible and can become stale.

The initial collection is a baseline. Later observations can establish price and availability changes, including restocks and sellouts. A sellout signal means the listing changed status between observations; it does not measure units sold, collector demand, or the precise moment the stock changed. The first run cannot reconstruct earlier sales or price history.

Watchlist selections are stored in the browser on this device. They do not sync between browsers and do not send email or push notifications.

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
sources.json              Supported source configuration
scripts/collect.py        Public-feed collector and history merge
data/releases.json        Persistent source observations
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

Images and product names belong to their respective owners. Raven Eye links to the original listings and is an independent monitoring interface, not an affiliated storefront.
