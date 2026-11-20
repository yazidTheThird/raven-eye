"""Bounded public retail catalogues; only explicit Product/Offer evidence.

No cart requests, private APIs or browser state. Missing/conflicting availability
is unknown; sitemap modification timestamps are never release dates.
"""
from decimal import Decimal, InvalidOperation
from html import unescape
from html.parser import HTMLParser
import re
import urllib.parse
import xml.etree.ElementTree as ET

from catalog_html import CataloguePage, VOID_TAGS, has_type, same_origin, structured_nodes


def schema_name(value):
    return str(value or "").rstrip("/").rsplit("/", 1)[-1]


def clean_text(value):
    return " ".join(unescape(str(value or "")).split())


class RetailPage(HTMLParser):
    """Observed card links and scoped microdata, plus configured product copy."""
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.source = source
        self.stack = []
        self.links = []
        self.products = []
        self.copy = []
        self.subtitle = []
        self.signals = []
        self.specs = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = set(a.get("class", "").split())
        ancestor_classes = set().union(*(e["classes"] for e in self.stack)) if self.stack else set()
        mode = self.source.get("retailLinkMode", "bigcommerce")
        container = self.source.get("retailContainerId")
        inside_container = not container or any(e.get("id") == container for e in self.stack)
        if tag == "a" and a.get("href"):
            if inside_container and ((mode == "bigcommerce" and "card-title" in ancestor_classes)
                or (mode == "magento" and "product-item-link" in classes and "products-grid" in ancestor_classes)
                or mode == "prefix"):
                self.links.append(a["href"])
        scope = next((e["scope"] for e in reversed(self.stack) if e.get("scope") is not None), None)
        root_class = self.source.get("microdataRootClass")
        explicit_product = schema_name(a.get("itemtype")) == "Product"
        synthetic_product = bool(root_class and root_class in classes)
        node = None
        prop = a.get("itemprop")
        if explicit_product or synthetic_product:
            node = {"@type": "Product"}
            self.products.append(node)
        elif "itemscope" in a and scope is not None:
            node = {"@type": schema_name(a.get("itemtype"))}
            if prop:
                scope.setdefault(prop, []).append(node)
        target = scope if node is None else node
        value = next((a[k] for k in ("content", "href", "src") if a.get(k)), None)
        if prop and scope is not None and node is None and value is not None:
            scope.setdefault(prop, []).append(value)
        copy_ids = set(self.source.get("descriptionIds", []))
        copy_classes = set(self.source.get("descriptionClasses", []))
        capture = (a.get("id") in copy_ids or bool(copy_classes & classes))
        signal_classes = set(self.source.get("stockSignalClasses", []))
        in_specs = any(e.get("id") in self.source.get("specContainerIds", []) for e in self.stack)
        row = next((e for e in reversed(self.stack) if e.get("cells") is not None), None)
        e = {"tag": tag, "id": a.get("id"), "classes": classes, "scope": node, "target": target,
             "prop": prop if value is None and node is None else None, "text": [],
             "capture": capture, "subtitle": bool(classes & set(self.source.get("subtitleClasses", []))),
             "signal": bool(classes & signal_classes), "spec": a.get("data-th"),
             "cells": [] if tag == "tr" and in_specs else None,
             "cell_row": row if tag in {"td", "th"} else None}
        if tag == "img" and self.source.get("productImagePath") and scope is not None:
            src = a.get("src", "")
            if self.source["productImagePath"] in src:
                scope.setdefault("image", []).append(src)
        if tag not in VOID_TAGS:
            self.stack.append(e)

    def handle_endtag(self, tag):
        match = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i]["tag"] == tag), None)
        if match is None:
            return
        for e in self.stack[match:]:
            value = clean_text(" ".join(e["text"]))
            if e["prop"] and e["target"] is not None and value:
                e["target"].setdefault(e["prop"], []).append(value)
            if e["capture"] and value:
                self.copy.append(value)
            if e["subtitle"] and value:
                self.subtitle.append(value)
            if e["signal"] and value:
                self.signals.append(value)
            if e["spec"] and value:
                self.specs.append((e["spec"], value))
            if e["cell_row"] is not None:
                e["cell_row"]["cells"].append(value)
            if e["cells"] is not None and len(e["cells"]) == 2:
                self.specs.append((e["cells"][0].rstrip(":"), e["cells"][1]))
        del self.stack[match:]

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if any(e["tag"] in {"script", "style"} for e in self.stack):
            return
        for e in self.stack:
            if e["prop"] or e["capture"] or e["subtitle"] or e["signal"] or e["spec"] or e["cell_row"] is not None:
                e["text"].append(data)


def first(value, default=None):
    if isinstance(value, list):
        return value[0] if value else default
    return value if value is not None else default


def offer_fields(product):
    offers = product.get("offers") or []
    if not isinstance(offers, list):
        offers = [offers]
    # Aggregate or multiple offers cannot establish the specific displayed offer.
    if len(offers) != 1 or not isinstance(offers[0], dict) or schema_name(offers[0].get("@type")) != "Offer":
        return "unknown", None, None, "No single explicit retail Offer was supplied."
    offer = offers[0]
    statuses = {schema_name(v) for v in (offer.get("availability") if isinstance(offer.get("availability"), list) else [offer.get("availability")])}
    mapping = {"InStock": "available", "LimitedAvailability": "available", "OutOfStock": "out_of_stock",
               "SoldOut": "out_of_stock", "Discontinued": "out_of_stock", "PreOrder": "presale",
               "PreSale": "presale", "BackOrder": "backorder"}
    status = mapping.get(next(iter(statuses)), "unknown") if len(statuses) == 1 else "unknown"
    specs = offer.get("priceSpecification") or []
    if not isinstance(specs, list):
        specs = [specs]
    def values(key):
        result = []
        for node in [offer, *specs]:
            if isinstance(node, dict) and node.get(key) is not None:
                v = node[key]
                result.extend(v if isinstance(v, list) else [v])
        return result
    try:
        amounts = {Decimal(str(v)) for v in values("price")}
        amount = next(iter(amounts)) if len(amounts) == 1 else Decimal(0)
        price = float(amount) if amount.is_finite() and amount > 0 else None
    except (InvalidOperation, ValueError, TypeError):
        price = None
    currencies = {str(v) for v in values("priceCurrency")}
    currency = next(iter(currencies)) if len(currencies) == 1 else None
    currency = currency if isinstance(currency, str) and re.fullmatch(r"[A-Z]{3}", currency) else None
    if price is None or currency is None:
        price, currency = None, None
    return status, price, currency, "Public Product/Offer availability: " + ", ".join(sorted(statuses)) + "."


def product_item(body, url, source):
    page = CataloguePage()
    page.feed(body)
    retail = RetailPage(source)
    retail.feed(body)
    nodes = [n for b in page.blocks for n in structured_nodes(b)]
    products = [n for n in nodes if has_type(n, "Product")]
    if not products:
        products = retail.products
    if len(products) != 1:
        raise ValueError("Product page did not expose one unambiguous Product")
    product = products[0]
    canonical = first(product.get("url")) or page.meta.get("og:url") or url
    canonical = urllib.parse.urljoin(url, canonical)
    if not same_origin(canonical, source["url"]):
        raise ValueError("Product canonical URL left the official source")
    name = clean_text(first(product.get("name")))
    if not name:
        raise ValueError("Product has no name")
    if retail.subtitle:
        name += " — " + " ".join(dict.fromkeys(retail.subtitle))
    description = first(product.get("description"), "")
    # BigCommerce themes may URL-encode their schema descriptions.
    if isinstance(description, str) and re.search(r"%(?:20|0A|3A)", description, re.I):
        description = urllib.parse.unquote(description)
    description = str(description) + " " + " ".join(dict.fromkeys(retail.copy))
    images = product.get("image") or []
    if not isinstance(images, list):
        images = [images]
    image_urls = []
    for img in images:
        img = img.get("contentUrl") or img.get("url") or img.get("@id") if isinstance(img, dict) else img
        if isinstance(img, str):
            img = urllib.parse.urljoin(url, img)
            if urllib.parse.urlsplit(img).scheme in {"https", "http"} and img not in image_urls:
                image_urls.append(img)
    if not image_urls and page.meta.get("og:image"):
        image_urls.append(urllib.parse.urljoin(url, page.meta["og:image"]))
    status, price, currency, evidence = offer_fields(product)
    signals = " ".join(retail.signals).casefold()
    observed = set()
    for pattern, state in [(r"\bcoming soon\b", "upcoming"), (r"\b(?:sold out|out of stock|not available)\b", "out_of_stock"),
                           (r"\bpre[- ]?order\b", "presale")]:
        if re.search(pattern, signals):
            observed.add(state)
    if observed and (len(observed) != 1 or status not in observed):
        status, evidence = "unknown", "Structured offer conflicts with the product page availability label."
    attrs = []
    for key, value in retail.specs:
        label = {"millésime": "year", "alloy": "metal", "fine weight": "weight"}.get(key.casefold(), key)
        if label.casefold().startswith("mintage ("):
            label = "mintage"
        attrs.append({"name": label, "terms": [{"name": value}]})
        if key.casefold() == "date of issue":
            match = re.search(r"\b(20\d{2})\b", value)
            if match:
                attrs.append({"name": "year", "terms": [{"name": match[1]}]})
    for prop in product.get("additionalProperty") or []:
        if isinstance(prop, dict) and prop.get("name") and prop.get("value") is not None:
            attrs.append({"name": prop["name"], "terms": [{"name": str(prop["value"])}]})
    brand = first(product.get("brand"))
    brand = brand.get("name") if isinstance(brand, dict) else brand
    if isinstance(brand, list):
        brand = first(brand)
    brand = brand or source.get("catalogueMaker")
    item = {"id": str(first(product.get("sku")) or canonical), "name": name, "permalink": canonical,
            "description": description, "images": [{"src": i} for i in image_urls], "attributes": attrs,
            "categories": [{"name": source.get("productTypeHint", "Collectible")}], "prices": {}, "tags": [],
            "_status": status, "_statusEvidence": evidence, "_price": price, "_currency": currency}
    if brand:
        item["brands"] = [{"name": brand}]
    if product.get("datePublished"):
        item["date_created_gmt"] = first(product["datePublished"])
    return item


def catalogue_links(body, url, source):
    if source.get("retailLinkMode") == "sitemap":
        root = ET.fromstring(body)
        if root.tag.rsplit("}", 1)[-1] != "urlset":
            raise ValueError("Expected a public product sitemap URL set")
        links = [n.text for n in root.iter() if n.tag.rsplit("}", 1)[-1] == "loc" and n.text]
        next_url = None
    else:
        retail, page = RetailPage(source), CataloguePage()
        retail.feed(body)
        page.feed(body)
        links, next_url = retail.links, page.next_url
    urls = []
    prefix = source.get("productPathPrefix", "/")
    for link in links:
        target = urllib.parse.urljoin(url, link)
        parts = urllib.parse.urlsplit(target)
        target = urllib.parse.urlunsplit(parts._replace(query="", fragment=""))
        if same_origin(target, source["url"]) and parts.path.startswith(prefix) and target not in urls:
            urls.append(target)
    next_url = urllib.parse.urljoin(url, next_url) if next_url else None
    if next_url and (not same_origin(next_url, source["url"]) or
                     not urllib.parse.urlsplit(next_url).path.startswith(urllib.parse.urlsplit(source["endpoint"]).path.rstrip("/"))):
        raise ValueError("Catalogue pagination left the configured public path")
    return urls, next_url


def fetch_catalogue(fetcher, source, max_pages):
    max_products = max(1, min(80, int(source.get("maxProducts", 36))))
    report = {"pagesFetched": 0, "paginationErrors": [], "latestWindowCapped": False,
              "discoveryFeeds": [], "backfillPagesFetched": 0, "skippedRechecks": 0}
    urls, visited = [], set()
    next_url = source["endpoint"]
    for _ in range(min(8, max_pages)):
        if not next_url:
            break
        if next_url in visited:
            report["paginationErrors"].append("Catalogue repeated a visited page")
            break
        visited.add(next_url)
        try:
            fetcher.check_robots(next_url)
            found, following = catalogue_links(fetcher.raw(next_url, accept="text/html,application/xml;q=0.9,*/*;q=0.1"), next_url, source)
            if not found:
                raise ValueError("Catalogue exposed no configured product links")
        except (OSError, ValueError, RuntimeError, TypeError, ET.ParseError) as exc:
            if not urls:
                raise
            report["paginationErrors"].append("Catalogue: " + str(exc)[:160])
            break
        report["pagesFetched"] += 1
        if all(x in urls for x in found):
            report["paginationErrors"].append("Catalogue repeated earlier products")
            break
        urls.extend(x for x in found if x not in urls)
        next_url = following
        if len(urls) >= max_products:
            break
    report["latestWindowCapped"] = bool(next_url) or len(urls) > max_products
    report["latestFetchedCount"] = len(urls)
    items = {}
    for url in urls[:max_products]:
        try:
            fetcher.check_robots(url)
            item = product_item(fetcher.raw(url, accept="text/html,*/*;q=0.1"), url, source)
            items.setdefault(item["id"], item)
        except (OSError, ValueError, RuntimeError, TypeError) as exc:
            report["paginationErrors"].append("Product " + url + ": " + str(exc)[:120])
            if getattr(exc, "code", None) in {401, 403, 429}:
                break
    if not items:
        raise ValueError("No public product details could be read; previous observations preserved")
    return list(items.values()), report
