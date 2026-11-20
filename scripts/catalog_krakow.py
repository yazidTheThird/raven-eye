"""Royal Krakow Mint's public catalogue, using its labelled specifications.

The mint sends retail orders to a partner. Availability here is explicitly the
mint's catalogue statement; no partner inventory or price is inferred.
"""
from html.parser import HTMLParser
import re
import urllib.parse

from catalog_html import VOID_TAGS, catalogue_links, same_origin


def clean(value):
    return re.sub(r"\s+", " ", value).strip()


class KrakowProduct(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.identity = None
        self.title = []
        self.short_description = []
        self.attributes = []
        self.images = []
        self.label = []
        self.value = []
        self.saw_description = False
        self.capture_description = False

    def inside(self, predicate):
        return any(predicate(tag, attrs) for tag, attrs in self.stack)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "body" and values.get("id") == "product":
            match = re.search(r"(?:^|\s)product-(\d+)(?:\s|$)", values.get("class", ""))
            if match:
                self.identity = match[1]
        right = self.inside(lambda t, a: "pb-right-column" in a.get("class", "").split())
        if tag == "li" and right:
            self.label, self.value = [], []
        if tag == "div" and right and "desc" in values.get("class", "").split() and not self.saw_description:
            self.capture_description = True
            self.saw_description = True
        if tag == "a" and "thumbnail" in values.get("class", "").split():
            image = values.get("href", "")
            if urllib.parse.urlsplit(image).scheme in {"https", "http"} and image not in self.images:
                self.images.append(image)
        if tag not in VOID_TAGS:
            self.stack.append((tag, values))

    def handle_data(self, value):
        if self.inside(lambda t, a: t in {"script", "style"}):
            return
        if self.inside(lambda t, a: t == "h1"):
            self.title.append(value)
        if self.capture_description:
            self.short_description.append(value)
        if self.inside(lambda t, a: "pb-right-column" in a.get("class", "").split()):
            if self.inside(lambda t, a: t == "strong"):
                self.label.append(value)
            elif self.inside(lambda t, a: t == "span"):
                self.value.append(value)

    def handle_endtag(self, tag):
        if tag in VOID_TAGS:
            return
        if tag == "li" and self.label and self.value:
            self.attributes.append((clean("".join(self.label)), clean("".join(self.value))))
            self.label, self.value = [], []
        if tag == "div" and self.stack and "desc" in self.stack[-1][1].get("class", "").split():
            self.capture_description = False
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)


def product_item(body, url, source):
    if not same_origin(url, source["url"]):
        raise ValueError("Krakow product URL left the official source")
    page = KrakowProduct()
    page.feed(body)
    title = clean(" ".join(page.title))
    specs = dict(page.attributes)
    if not page.identity or not title or not specs.get("Metal"):
        raise ValueError("No labelled Royal Krakow Mint product metadata")
    statement = specs.get("Availability", "")
    lowered = statement.lower()
    status = "unknown"
    if lowered in {"sold out", "out of stock"}:
        status = "out_of_stock"
    elif lowered in {"ready to order", "last pieces", "in stock", "available"}:
        status = "available"
    elif lowered.startswith("coming "):
        status = "upcoming"
    evidence = ("Mint catalogue availability: " + statement + ". Retail orders go to its partner; partner stock is not checked."
                if statement else "No availability statement in the mint's product specifications.")
    description = clean(" ".join(page.short_description))
    description += " " + " ".join(name + ": " + value + "." for name, value in page.attributes)
    return {"id": page.identity, "name": title, "permalink": url,
            "short_description": clean(" ".join(page.short_description)), "description": description,
            "attributes": [{"name": name, "terms": [{"name": value}]} for name, value in page.attributes],
            "images": [{"src": image} for image in page.images], "categories": [], "tags": [],
            "brands": [{"name": "Royal Krakow Mint"}], "prices": {},
            "_status": status, "_statusEvidence": evidence}


def fetch_catalogue(fetcher, source, max_pages):
    maximum = max(1, min(80, int(source.get("maxProducts", 40))))
    source = {**source, "productPathPrefix": "/en/our-coins/"}
    report = {"pagesFetched": 0, "paginationErrors": [], "latestWindowCapped": False,
              "discoveryFeeds": [], "backfillPagesFetched": 0, "skippedRechecks": 0}
    urls, pages = [], set()
    next_url = source["endpoint"]
    for _ in range(max_pages):
        if not next_url:
            break
        if next_url in pages:
            report["paginationErrors"].append("Krakow catalogue repeated a page")
            break
        pages.add(next_url)
        try:
            fetcher.check_robots(next_url)
            links, following = catalogue_links(fetcher.raw(next_url), next_url, source)
            if not links:
                raise ValueError("Krakow catalogue exposed no product links")
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            if not urls:
                raise
            report["paginationErrors"].append("Catalogue: " + str(exc)[:160])
            break
        report["pagesFetched"] += 1
        if all(url in urls for url in links):
            report["paginationErrors"].append("Krakow catalogue repeated earlier products")
            break
        urls.extend(url for url in links if url not in urls)
        next_url = following
    report["latestWindowCapped"] = bool(next_url) or len(urls) > maximum
    report["latestFetchedCount"] = len(urls)
    items = {}
    for url in urls[:maximum]:
        try:
            fetcher.check_robots(url)
            item = product_item(fetcher.raw(url), url, source)
            items.setdefault(item["id"], item)
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            report["paginationErrors"].append("Product " + url + ": " + str(exc)[:120])
            if getattr(exc, "code", None) in {401, 403, 429}:
                break
    if not items:
        raise ValueError("No public Krakow product details could be read")
    return list(items.values()), report
