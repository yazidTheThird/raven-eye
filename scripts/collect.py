#!/usr/bin/env python3
"""Raven Eye public-feed collector. Python 3.11+, standard library only.

Run from any directory. The first successful observation of each source is a
baseline, not 100 newly released products. Absent products retain their last
known state and timestamp; disappearance or network failure is never sold-out
evidence. The catalogue is a bounded observation window, not market coverage.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import copy
import datetime as dt
import html
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser

ROOT = Path(__file__).resolve().parents[1]
USER_AGENT = "RavenEye/1.0 (+https://github.com/yazidTheThird/raven-eye; public-release-monitor)"
TIMEOUT = 25
MAX_RESPONSE = 24 * 1024 * 1024
RECHECK_LIMIT = 12
VALID_STATUSES = {"available", "presale", "upcoming", "backorder", "out_of_stock", "unknown"}


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.ignore = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.ignore += 1
        if tag in {"p", "br", "li", "td", "th", "div", "h1", "h2", "h3"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.ignore:
            self.ignore -= 1
        if tag in {"p", "li", "td", "th", "div", "span"}:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.ignore:
            self.parts.append(data)


def text(value):
    parser = TextExtractor()
    parser.feed(str(value or ""))
    return re.sub(r"\s+", " ", html.unescape("".join(parser.parts))).strip()


def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def iso(value, utc=False):
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            if not utc:
                return None
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    except (TypeError, ValueError):
        return None


def safe_url(value):
    if isinstance(value, str) and urllib.parse.urlsplit(value).scheme in {"https", "http"}:
        return value
    return None


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) and n >= 0 else None
    except (TypeError, ValueError):
        return None


def detect_metal(title, detail=""):
    # Examine the product title/type before the prose, which may mention other
    # products or describe a gold-coloured surface on a silver substrate.
    for candidate in [title, detail]:
        s = candidate.lower()
        if re.search(r"(?:silver|gold)[ -]plated.{0,25}\bcopper\b", s) or re.search(r"\bcopper\b.{0,25}(?:silver|gold)[ -]plated", s):
            return "Copper"
        if re.search(r"\b(?:silver\s*(?:&|and|or)\s*(?:pure\s+)?gold|gold\s*(?:&|and|or)\s*(?:pure\s+)?silver)\b", s):
            return "Other"  # Mixed-metal sets or chance-based variants.
        if re.search(r"\bsilver\b", s):
            return "Silver"
        for metal in ["Platinum", "Palladium", "Gold", "Copper"]:
            if re.search(r"\b" + metal.lower() + r"\b", s):
                return metal if metal != "Palladium" else "Other"
    return "Other"


def detect_category(title, product_type=""):
    s = (title + " " + product_type).lower()
    if re.search(r"\b(bar|bars|barre|ingot|ingots)\b", s):
        return "Bar"
    if re.search(r"\b(foil|blind box|sculpture|statue|figurine|coin.?card|medallion|note|pendant)\b", s):
        return "Collectible"
    if re.search(r"\b(coin|coins|round|rounds)\b", s):
        return "Coin"
    return "Collectible"


def details(title, product_type, description, tags):
    primary = title + " " + product_type
    weight = re.search(r"(?<!\w)(\d+(?:[.,]\d+)?(?:/\d+)?)\s*(?:troy\s*)?(oz|ounces?|kilograms?|kg|grams?|gr|gm|g)\b", primary, re.I)
    if not weight:
        weight = re.search(r"\bweight\s*:?\s*(\d+(?:[.,]\d+)?(?:/\d+)?)\s*(?:troy\s*)?(oz|ounces?|kilograms?|kg|grams?|gr|gm|g)\b", description, re.I)
    weight_text = None
    if weight:
        unit = weight[2].lower()
        unit = "oz" if unit.startswith(("oz", "ounce")) else "kg" if unit.startswith(("kg", "kilo")) else "g"
        weight_text = weight[1].replace(",", ".") + " " + unit
        # Preserve set quantities rather than silently reporting a per-piece
        # weight as if it were the weight of the whole purchase.
        multiple = re.search(r"(\d+)\s*[x×]\s*$", primary[:weight.start()], re.I)
        if multiple:
            weight_text = multiple[1] + " × " + weight_text
    year = re.search(r"(?<!\d)(20\d{2})(?!\d)", title)
    mintage = re.search(r"\b(?:mintage|limited edition)\s*(?:of|is|:|to|only|just|worldwide|limited|a|maximum|\s){0,45}\s*([\d][\d, .]*)\s*(?:pieces|coins|units|worldwide|$|\.)", description, re.I)
    if not mintage:
        mintage = re.search(r"\b(?:mintage|limited edition)\s*:?\s*(\d[\d,]*)(?![\d,])\b(?!\s*(?:troy\s*)?(?:oz|ounces?|kg|kilograms?|g|gr|gm|grams?|different|designs|tiers)\b)", description, re.I)
    mintage_number = None
    if mintage:
        raw = re.sub(r"[^\d]", "", mintage[1])
        if raw and 0 < int(raw) < 100_000_000:
            mintage_number = int(raw)
    if re.search(r"\bblind\s*box\b", primary, re.I) or re.search(r"\bmintages\s+ranging\b|\bper\s+(?:applicable\s+)?(?:rarity\s+)?tier\b", description, re.I):
        # Rarity-tier mintages are not the edition size of the entire blind box.
        mintage_number = None
    return {
        "metal": detect_metal(primary, description),
        "category": detect_category(title, product_type),
        "weight": weight_text,
        "year": int(year[1]) if year else None,
        "mintage": mintage_number,
    }


def is_relevant(title, product_type, description, tags):
    primary = (title + " " + product_type).lower()
    if re.search(r"gift\s*(card|certificate)|display\s*(case|box)|empty\s*(box|capsule)", primary):
        return False
    return bool(re.search(r"\b(coin|coins|silver|gold|platinum|palladium|copper|bullion|bar|bars|round|rounds|foil|medal|medallion|sculpture|statue|blind box)\b", primary))


def clean_tags(values):
    if isinstance(values, str):
        values = values.split(",")
    return list(dict.fromkeys(text(v.get("name", "") if isinstance(v, dict) else v) for v in (values or [])))[:30]


def status_from_labels(labels):
    joined = " ".join(labels).lower().replace("_", "-")
    if re.search(r"coming[- ]soon|pre[- ]announce|not[- ]yet[- ]released", joined):
        return "upcoming"
    if re.search(r"pre[- ]?(?:order|sale)", joined):
        return "presale"
    if re.search(r"back[- ]?order", joined):
        return "backorder"
    return None


def shopify_status(item):
    labels = clean_tags(item.get("tags")) + [text(item.get("title"))]
    special = status_from_labels(labels)
    variants = item.get("variants") or []
    available = any(v.get("available") is True for v in variants)
    unavailable = bool(variants) and all(v.get("available") is False for v in variants)
    if special == "upcoming":
        return special, "Seller title/tag labels this listing coming soon or pre-announcement."
    if unavailable:
        return "out_of_stock", "All published Shopify variants report available=false."
    if available and special:
        return special, "A variant is orderable; the seller title/tag explicitly labels " + special + "."
    if available:
        return "available", "At least one Shopify variant reports available=true; this does not confirm immediate dispatch."
    return "unknown", "No usable variant availability was supplied."


def woo_status(item):
    labels = [text(item.get("name"))] + clean_tags(item.get("categories")) + clean_tags(item.get("tags"))
    special = status_from_labels(labels)
    if special == "upcoming":
        return special, "Seller category/title explicitly labels this listing coming soon or pre-announcement."
    if item.get("is_in_stock") is False:
        return "out_of_stock", "WooCommerce is_in_stock=false."
    if special == "presale":
        return special, "Seller title/category explicitly labels a pre-order or presale."
    if item.get("is_on_backorder") is True:
        return "backorder", "WooCommerce is_on_backorder=true."
    if item.get("is_in_stock") is True and item.get("is_purchasable") is True:
        return "available", "WooCommerce is_in_stock=true and is_purchasable=true."
    return "unknown", "No unambiguous purchasable stock status supplied."


def wordpress_status(item):
    classes = item.get("class_list") or []
    special = status_from_labels(classes + [text((item.get("title") or {}).get("rendered"))])
    if special == "upcoming":
        return special, "Public product category labels this listing coming-soon/pre-announce."
    if "outofstock" in classes:
        return "out_of_stock", "Public WordPress product class is outofstock."
    if special:
        return special, "Public product category/title explicitly labels " + special + "."
    if "onbackorder" in classes:
        return "backorder", "Public WordPress product class is onbackorder."
    if "instock" in classes and "purchasable" in classes:
        return "available", "Public WordPress product classes are instock and purchasable."
    return "unknown", "This catalogue feed does not expose an unambiguous stock status."


def normalize(source, item):
    kind = source["type"]
    tags = []
    product_type = ""
    image = None
    selected_variant = None
    selected_variant_id = None
    price_is_from = False
    price_basis = "Seller's listed product price"
    if kind == "shopify":
        title = text(item.get("title"))
        description = text(item.get("body_html"))
        product_type = text(item.get("product_type"))
        tags = clean_tags(item.get("tags"))
        variants = item.get("variants") or []
        available = [v for v in variants if v.get("available") is True]
        selected = available or variants
        priced = [(number(v.get("price")), v) for v in selected]
        priced = [(price, variant) for price, variant in priced if price is not None]
        chosen = min(priced, key=lambda row: row[0]) if priced else None
        price = chosen[0] if chosen else None
        if chosen:
            selected_variant_id = str(chosen[1]["id"]) if chosen[1].get("id") is not None else None
            selected_variant = text(chosen[1].get("title"))
            if selected_variant == "Default Title":
                selected_variant = None
        price_is_from = len({price for price, variant in priced}) > 1
        price_basis = ("Lowest-priced orderable variant" if available else "Lowest-priced listed variant; none currently orderable")
        if selected_variant:
            price_basis += ": " + selected_variant
        currency = source.get("currency")
        published = iso(item.get("published_at"))
        url = source["url"].rstrip("/") + "/products/" + item.get("handle", "")
        image = next((safe_url(i.get("src")) for i in item.get("images", []) if safe_url(i.get("src"))), None)
        status, evidence = shopify_status(item)
        variant_count = len(variants)
        vendor = text(item.get("vendor")) or None
    elif kind == "woocommerce":
        title = text(item.get("name"))
        description = text(item.get("description") or item.get("short_description"))
        tags = clean_tags(item.get("tags"))
        product_type = " ".join(clean_tags(item.get("categories")))
        prices = item.get("prices") or {}
        price = number(prices.get("price"))
        if price is not None:
            price = round(price / (10 ** int(prices.get("currency_minor_unit", 2))), 4)
        currency = prices.get("currency_code")
        published = iso(item.get("date_created_gmt"), utc=True)
        url = item.get("permalink")
        image = next((safe_url(i.get("thumbnail") or i.get("src")) for i in item.get("images", []) if safe_url(i.get("src"))), None)
        status, evidence = woo_status(item)
        variant_count = max(1, len(item.get("variations") or []))
        vendor = ", ".join(clean_tags(item.get("brands"))) or None
    else:
        title = text((item.get("title") or {}).get("rendered"))
        description = text((item.get("content") or {}).get("rendered"))
        tags = [v.removeprefix("product_tag-").replace("-", " ") for v in item.get("class_list", []) if v.startswith("product_tag-")]
        product_type = " ".join(v.removeprefix("product_cat-").replace("-", " ") for v in item.get("class_list", []) if v.startswith("product_cat-"))
        yoast = item.get("yoast_head_json") or {}
        price = number(yoast.get("product_price_amount"))
        currency = yoast.get("product_price_currency")
        published = iso(item.get("date_gmt"), utc=True)
        url = item.get("link")
        image = next((safe_url(i.get("url")) for i in yoast.get("og_image", []) if safe_url(i.get("url"))), None)
        status, evidence = wordpress_status(item)
        variant_count = 1
        vendor = None
    if not title or not safe_url(url) or not is_relevant(title, product_type, description, tags):
        return None
    # Zero-price placeholders should not appear as free precious metals.
    if price == 0:
        price = None
    if currency and not re.fullmatch(r"[A-Z]{3}", currency):
        currency = None
    return {
        "id": source["id"] + ":" + str(item["id"]),
        "sourceProductId": str(item["id"]),
        "sourceId": source["id"], "sourceName": source["name"],
        "title": title, "url": url, "image": image,
        **details(title, product_type, description, tags),
        "price": price, "currency": currency, "status": status,
        "publishedAt": published, "description": description[:277] + ("…" if len(description) > 277 else ""),
        "tags": tags[:20], "statusEvidence": evidence, "vendor": vendor,
        "variantCount": variant_count, "priceIsFrom": price_is_from,
        "selectedVariant": selected_variant, "selectedVariantId": selected_variant_id,
        "priceBasis": price_basis,
        "handle": item.get("handle"),
    }


class Fetcher:
    """One source/host worker; polite robots-aware GET requests, no credentials."""
    def __init__(self):
        self.robots = {}
        self.last_request = {}

    def raw(self, url):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"})
        # One retry for transient server errors only; never retry denials/challenges.
        for attempt in range(2):
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                    body = response.read(MAX_RESPONSE + 1)
                    if len(body) > MAX_RESPONSE:
                        raise ValueError("Response exceeded safe size bound")
                    return body.decode("utf-8")
            except urllib.error.HTTPError as exc:
                if exc.code not in {500, 502, 503, 504} or attempt:
                    raise
                time.sleep(1)

    def check_robots(self, url):
        split = urllib.parse.urlsplit(url)
        base = split.scheme + "://" + split.netloc
        if base not in self.robots:
            parser = urllib.robotparser.RobotFileParser()
            try:
                body = self.raw(base + "/robots.txt")
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    body = "User-agent: *\nAllow: /"
                else:
                    raise RuntimeError("Could not verify robots.txt (HTTP " + str(exc.code) + ")") from exc
            parser.parse(body.splitlines())
            self.robots[base] = parser
        parser = self.robots[base]
        if not parser.can_fetch(USER_AGENT, url):
            raise RuntimeError("robots.txt disallows this feed")
        delay = max(0.5, parser.crawl_delay(USER_AGENT) or parser.crawl_delay("*") or 0)
        wait = self.last_request.get(base, 0) + delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.last_request[base] = time.monotonic()

    def json(self, url):
        self.check_robots(url)
        return json.loads(self.raw(url))


def item_url(source, product):
    base = source.get("feedBase", source["url"]).rstrip("/")
    if source["type"] == "shopify":
        return base + "/products/" + urllib.parse.quote(product.get("handle") or product["url"].rstrip("/").split("/")[-1]) + ".json"
    if source["type"] == "woocommerce":
        return base + "/wp-json/wc/store/v1/products/" + product["sourceProductId"]
    return base + "/wp-json/wp/v2/product/" + product["sourceProductId"]


def fetch_source(source, old_products):
    fetcher = Fetcher()
    source = copy.deepcopy(source)
    payload = fetcher.json(source["endpoint"])
    items = payload.get("products") if isinstance(payload, dict) and source["type"] == "shopify" else payload
    if not isinstance(items, list) or not items:
        raise ValueError("Feed returned no product list; previous observations preserved")
    if source["type"] == "woocommerce":
        # Woo Store API omits creation dates. These two shops also expose their
        # WordPress product publication timestamps. Join by exact product ID;
        # never derive publication from IDs, image paths or the printed year.
        try:
            date_url = source["url"].rstrip("/") + "/wp-json/wp/v2/product?per_page=100&orderby=date&order=desc&_fields=id,date_gmt"
            dates = fetcher.json(date_url)
            if isinstance(dates, list):
                date_map = {str(v["id"]): v.get("date_gmt") for v in dates if isinstance(v, dict) and "id" in v}
                for item in items:
                    item["date_created_gmt"] = date_map.get(str(item.get("id")))
        except (OSError, ValueError, RuntimeError, TypeError):
            pass  # A missing date enrichment must not discard a valid offer.
    if source["type"] == "shopify":
        # Verified fallback is recorded in sources.json; meta.json checks currency
        # in the same storefront on every run rather than guessing from geography.
        try:
            meta = fetcher.json(source.get("feedBase", source["url"]).rstrip("/") + "/meta.json")
            if re.fullmatch(r"[A-Z]{3}", str(meta.get("currency", ""))):
                source["currency"] = meta["currency"]
        except (OSError, ValueError, RuntimeError):
            pass
    normalized = [normalize(source, item) for item in items]
    normalized = [p for p in normalized if p]
    if not normalized:
        raise ValueError("Feed parsed but contained no relevant releases; previous data preserved")
    seen = {p["id"] for p in normalized}
    missing = sorted([p for p in old_products if p["id"] not in seen], key=lambda p: p.get("lastCheckedAt", ""))[:RECHECK_LIMIT]
    warnings = []
    for prior in missing:
        try:
            item = fetcher.json(item_url(source, prior))
            if source["type"] == "shopify":
                item = item.get("product", item)
                if not any(isinstance(v.get("available"), bool) for v in item.get("variants", [])):
                    # Shopify's single-product .json often omits the public
                    # availability flags. Such a response is metadata only,
                    # not a fresh stock check; retain the previous timestamp.
                    raise ValueError("Individual product feed omitted stock flags")
            current = normalize(source, item)
            if current:
                normalized.append(current)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            # 404/unlisted is not evidence of a sale or zero stock.
            warnings.append(prior["id"] + ": " + type(exc).__name__)
    return normalized, len(items), warnings


def merge_products(previous, observations, at, baseline=False):
    result = {p["id"]: copy.deepcopy(p) for p in previous}
    for observed in observations:
        old = result.get(observed["id"])
        current = copy.deepcopy(observed)
        if old and not current.get("publishedAt"):
            current["publishedAt"] = old.get("publishedAt")
        current.update({"firstSeenAt": old["firstSeenAt"] if old else at, "lastSeenAt": at, "lastCheckedAt": at})
        current["sourceDateInFuture"] = bool(current.get("publishedAt") and current["publishedAt"] > at)
        sample = {"at": at, "price": current["price"], "currency": current["currency"], "status": current["status"], "selectedVariantId": current.get("selectedVariantId"), "selectedVariant": current.get("selectedVariant")}
        if old:
            history = copy.deepcopy(old.get("history") or [])
            changed = any(old.get(k) != current.get(k) for k in ["price", "currency", "status", "selectedVariantId"])
            if changed or not history:
                history.append(sample)
            current["history"] = history
            current["change"] = old.get("change")
            event = None
            if old.get("status") == "out_of_stock" and current["status"] == "available":
                event = "restock"
            elif old.get("status") in {"available", "presale", "backorder"} and current["status"] == "out_of_stock":
                event = "sold_out"
            elif old.get("selectedVariantId") == current.get("selectedVariantId") and old.get("currency") == current["currency"] and old.get("price") is not None and current["price"] is not None and old["price"] != current["price"]:
                event = "price_drop" if current["price"] < old["price"] else "price_up"
            elif old.get("status") in {"presale", "upcoming", "backorder"} and current["status"] == "available":
                event = "available"
            if event:
                current["change"] = {"type": event, "at": at, "previousPrice": old.get("price"), "previousStatus": old.get("status")}
        else:
            current["history"] = [sample]
            current["change"] = None if baseline else {"type": "new", "at": at}
        result[current["id"]] = current
    return list(result.values())


def collect(config, previous, at):
    products = previous.get("products") or []
    old_sources = {s["id"]: s for s in previous.get("sources", [])}
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch_source, s, [p for p in products if p["sourceId"] == s["id"]]): s for s in config}
        for future in concurrent.futures.as_completed(futures):
            source = futures[future]
            try:
                results[source["id"]] = (future.result(), None)
            except Exception as exc:
                results[source["id"]] = (None, str(exc)[:240])
    sources = []
    for source in config:
        old = old_sources.get(source["id"], {})
        result, error = results[source["id"]]
        health = {k: source[k] for k in ["id", "name", "url", "type", "coverage"]}
        health.update({"status": "error" if error else "ok", "lastAttempt": at, "lastSuccess": old.get("lastSuccess"), "count": old.get("count", 0), "error": error})
        if result:
            observations, fetched, warnings = result
            products = merge_products(products, observations, at, baseline=not old.get("lastSuccess"))
            health.update({"lastSuccess": at, "count": len(observations), "fetchedCount": fetched, "recheckErrors": len(warnings)})
            if warnings:
                health["note"] = "Some older listings could not be rechecked; their last known values and check dates are retained."
        sources.append(health)
    products.sort(key=lambda p: (p.get("publishedAt") or p["firstSeenAt"], p["id"]), reverse=True)
    return {"schemaVersion": 1, "generatedAt": at, "sources": sources, "products": products,
            "methodology": {"firstSeen": "First successful Raven Eye observation, not the original release date.", "publishedAt": "Seller publication timestamp when exposed; not a verified mint launch date.", "status": "Seller feed observation at lastCheckedAt; a source outage or absent listing never implies sold out.", "prices": "Original seller currency; taxes, shipping, quantity tiers and local storefront conversion may differ. No currency conversion.", "coverage": "Bounded catalogue windows. Existing listings beyond those windows are rechecked in rotation; missing listings retain historical observations.", "baseline": "Initial imports do not create new-release alerts. New means first detected after that source's baseline."}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=ROOT / "sources.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "releases.json")
    parser.add_argument("--only", help="Comma-separated source IDs, for diagnostics")
    args = parser.parse_args()
    config = json.loads(args.sources.read_text())
    if args.only:
        config = [s for s in config if s["id"] in args.only.split(",")]
    previous = json.loads(args.output.read_text()) if args.output.exists() else {}
    result = collect(config, previous, now_iso())
    if args.only:
        kept = [s for s in previous.get("sources", []) if s["id"] not in args.only.split(",")]
        result["sources"].extend(kept)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(args.output)
    for source in result["sources"]:
        print(f"{source['name']}: {source['status']}, {source['count']} observed" + (f" ({source['error']})" if source.get("error") else ""))
    print(f"Saved {len(result['products'])} tracked listings to {args.output}")
    # Healthy sources may still update when one provider is down.
    return 0 if any(s["status"] == "ok" for s in result["sources"]) else 1


if __name__ == "__main__":
    sys.exit(main())
