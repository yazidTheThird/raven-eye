"""Public PrestaShop catalogue JSON plus visible HTML availability.

The catalogue's public content negotiation returns listing data, but some
shops omit stock labels in that representation. Explicit HTML labels outrank
schema.org inventory because preorder allocations are often marked InStock.
"""
from html.parser import HTMLParser
from decimal import Decimal, InvalidOperation
import json
import re
import urllib.parse

from catalog_html import CataloguePage, VOID_TAGS, has_type, same_origin, structured_nodes


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


class ProductMarkup(HTMLParser):
    """Capture only product-specific labels; exclude FAQs and related items."""
    def __init__(self):
        super().__init__()
        self.stack = []
        self.article = None
        self.labels = {}
        self.detail_labels = []
        self.detail = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "article" and attrs.get("data-id-product"):
            self.article = str(attrs["data-id-product"])
            self.labels.setdefault(self.article, [])
        classes = set(attrs.get("class", "").split())
        label = bool(classes & {"flag", "availability-list", "tanto", "poco", "niente", "availability", "product-price-and-shipping"})
        detail_label = attrs.get("id") == "product-availability"
        if attrs.get("id") == "product-details" and attrs.get("data-product"):
            try:
                self.detail = json.loads(attrs["data-product"])
            except (ValueError, TypeError):
                pass
        if tag not in VOID_TAGS:
            self.stack.append((tag, label, detail_label))

    def handle_endtag(self, tag):
        if tag == "article":
            self.article = None
        if tag in VOID_TAGS:
            return
        for pos in range(len(self.stack) - 1, -1, -1):
            if self.stack[pos][0] == tag:
                del self.stack[pos:]
                break

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, value):
        if self.article and any(entry[1] for entry in self.stack):
            self.labels[self.article].append(value)
        if any(entry[2] for entry in self.stack):
            self.detail_labels.append(value)


def availability(labels):
    value = clean(" ".join(labels)).lower()
    # Explicit preorders and 'Soon' release flags override allocated inventory.
    if re.search(r"\bpre[ -]?(?:order|sale)\b", value):
        return "presale", clean(" ".join(labels))
    if re.search(r"\bsoon\b", value):
        return ("presale" if re.search(r"\bin stock\b", value) else "upcoming"), clean(" ".join(labels))
    if re.search(r"\bback[ -]?order\b", value):
        return "backorder", clean(" ".join(labels))
    if re.search(r"\bout of stock\b|\bunavailable\b|\bsold out\b", value):
        return "out_of_stock", clean(" ".join(labels))
    if re.search(r"\bin stock\b|\bavailable\b|\bready to ship\b|\blast items in stock\b", value):
        return "available", clean(" ".join(labels))
    return "unknown", "No explicit public availability label was exposed."


def decimal_price(value):
    try:
        result = Decimal(str(value))
        return float(result) if result.is_finite() and result > 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def listing_item(product, source, labels=()):
    if not isinstance(product, dict):
        raise ValueError("Malformed public product record")
    identity = str(product.get("id_product") or "")
    url = product.get("canonical_url") or product.get("url") or product.get("link")
    if not identity.isdigit() or not url or not same_origin(url, source["url"]):
        raise ValueError("Invalid public product identity or origin")
    status, evidence = availability(labels)
    cover = product.get("cover") or {}
    image = (cover.get("large") or {}).get("url")
    currency = source.get("currency")
    item = {
        "id": identity, "name": product.get("name"), "permalink": url,
        "description": product.get("description_short") or "",
        "images": [{"src": image}] if image else [], "attributes": [],
        "categories": [{"name": product.get("category_name") or source.get("productTypeHint", "Collectible")}],
        "tags": [], "prices": {}, "_price": decimal_price(product.get("price_amount")),
        "_currency": currency, "_status": status, "_metadataPartial": True,
        "_statusEvidence": "Public catalogue availability: " + evidence,
    }
    maker = product.get("manufacturer_name") or source.get("catalogueMaker")
    if maker:
        item["brands"] = [{"name": maker}]
    return item


def enrich_item(item, body, source):
    markup = ProductMarkup()
    markup.feed(body)
    page = CataloguePage()
    page.feed(body)
    nodes = [node for block in page.blocks for node in structured_nodes(block)]
    schema = next((node for node in nodes if has_type(node, "Product")
                   and (str(node.get("productID", "")) == item["id"]
                        or str(node.get("url", "")).rstrip("/") == item["permalink"].rstrip("/"))), {})
    detail = markup.detail
    detail_id = str(detail.get("id_product") or detail.get("id") or "")
    if detail_id != item["id"]:
        detail = {}
    if not schema and not detail:
        raise ValueError("Detail page did not identify the requested product")
    properties = detail.get("features") or schema.get("additionalProperty") or []
    attributes = []
    aliases = {"mintage (pcs)": "mintage", "date of issue": "issue date"}
    for prop in properties:
        if not isinstance(prop, dict) or not prop.get("name") or prop.get("value") is None:
            continue
        name = clean(prop["name"])
        name = aliases.get(name.lower(), name)
        value = clean(prop["value"])
        attributes.append({"name": name, "terms": [{"name": value}]})
    item["attributes"] = attributes
    description = detail.get("description") or schema.get("description") or item["description"]
    item["description"] = description + " " + " ".join(a["name"] + ": " + a["terms"][0]["name"] + "." for a in attributes)
    # An issue month is not an exact publication timestamp; do not manufacture one.
    labels = markup.detail_labels + [a["terms"][0]["name"] for a in attributes if a["name"].lower() == "availability"]
    status, evidence = availability(labels)
    if status != "unknown":
        # Preserve a catalogue Soon flag even where the generic detail stock
        # control reports allocated inventory as immediately available.
        if item["_status"] != "presale" or status != "available":
            item["_status"] = status
            item["_statusEvidence"] = "Public product availability: " + evidence
    offers = schema.get("offers") or {}
    if isinstance(offers, dict):
        price = decimal_price(offers.get("price"))
        currency = offers.get("priceCurrency")
        if price is not None and re.fullmatch(r"[A-Z]{3}", str(currency or "")):
            item["_price"], item["_currency"] = price, currency
    # schema.org availability is intentionally ignored: verified Power Coin
    # pages label presales InStock in JSON-LD while the purchase UI says preorder.
    item["_metadataPartial"] = False
    return item


def fetch_prestashop(fetcher, source, max_pages):
    maximum = max(1, min(4, int(max_pages)))
    detail_limit = max(0, min(24, int(source.get("maxDetails", 24))))
    report = {"pagesFetched": 0, "paginationErrors": [], "latestWindowCapped": False,
              "discoveryFeeds": [], "backfillPagesFetched": 0, "skippedRechecks": 0,
              "detailPagesFetched": 0, "detailLimit": detail_limit}
    items, visited = {}, set()
    next_url = source["endpoint"]
    endpoint_path = urllib.parse.urlsplit(next_url).path
    for _ in range(maximum):
        if not next_url:
            break
        if next_url in visited:
            report["paginationErrors"].append("Catalogue repeated its pagination URL")
            break
        visited.add(next_url)
        try:
            payload = fetcher.json(next_url)
            if not isinstance(payload, dict):
                raise ValueError("Catalogue did not return a product object")
            products = payload.get("products")
            if not isinstance(products, list) or not products:
                raise ValueError("Catalogue exposed no public product records")
            markup = ProductMarkup()
            markup.feed(payload.get("rendered_products") or "")
            if source.get("availabilityHtml"):
                fetcher.check_robots(next_url)
                markup.feed(fetcher.raw(next_url, accept="text/html"))
            new_count = 0
            for product in products:
                item = listing_item(product, source, markup.labels.get(str(product.get("id_product")), []))
                if item["id"] not in items:
                    items[item["id"]] = item
                    new_count += 1
            report["pagesFetched"] += 1
            if not new_count:
                raise ValueError("Catalogue repeated previously observed products")
            pages = (payload.get("pagination") or {}).get("pages") or []
            if isinstance(pages, dict):
                pages = pages.values()
            following = next((p.get("url") for p in pages if p.get("type") == "next" and p.get("clickable")), None)
            if following:
                following = urllib.parse.urljoin(next_url, following)
                if not same_origin(following, source["url"]) or urllib.parse.urlsplit(following).path != endpoint_path:
                    raise ValueError("Pagination left the configured public catalogue")
            next_url = following
        except (OSError, ValueError, RuntimeError, TypeError) as exc:
            if not items:
                raise
            report["paginationErrors"].append("Catalogue: " + str(exc)[:160])
            break
    report["latestWindowCapped"] = bool(next_url)
    report["latestFetchedCount"] = len(items)
    report["detailWindowCapped"] = len(items) > detail_limit
    for item in list(items.values())[:detail_limit]:
        try:
            fetcher.check_robots(item["permalink"])
            enrich_item(item, fetcher.raw(item["permalink"], accept="text/html"), source)
            report["detailPagesFetched"] += 1
        except (OSError, ValueError, RuntimeError, TypeError) as exc:
            report["paginationErrors"].append("Product " + item["permalink"] + ": " + str(exc)[:120])
            if getattr(exc, "code", None) in {401, 403, 429}:
                break
    return list(items.values()), report

# Shared collector adapter contract.
fetch_catalogue = fetch_prestashop
