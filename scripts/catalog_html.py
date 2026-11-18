"""Bounded public HTML catalogues with structured product metadata.

Only links actually exposed by the configured catalogue are followed. Stock
and price placeholders on manufacturer catalogue pages are not retail offers.
"""
from html.parser import HTMLParser
import json
import re
import urllib.parse

VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class CataloguePage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.next_url = None
        self.meta = {}
        self.blocks = []
        self.script = None
        self.description = []
        self.description_depth = 0
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag not in VOID_TAGS:
            self.depth += 1
        if tag == "a" and values.get("href"):
            self.links.append(values["href"])
        if tag == "link" and "next" in values.get("rel", "").split():
            self.next_url = values.get("href")
        if tag == "meta":
            self.meta[values.get("property") or values.get("name")] = values.get("content", "")
        if tag == "script" and values.get("type", "").lower() == "application/ld+json":
            self.script = []
        if not self.description_depth and "product-description" in values.get("class", "").split():
            self.description_depth = self.depth
        elif self.description_depth:
            self.description.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if tag in VOID_TAGS:
            return
        if tag == "script" and self.script is not None:
            try:
                self.blocks.append(json.loads("".join(self.script)))
            except (TypeError, ValueError):
                pass
            self.script = None
        if self.description_depth:
            if self.depth == self.description_depth:
                self.description_depth = 0
            else:
                self.description.append("</" + tag + ">")
        self.depth = max(0, self.depth - 1)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)
        if self.description_depth and self.script is None:
            self.description.append(data)


def structured_nodes(value):
    if isinstance(value, list):
        for child in value:
            yield from structured_nodes(child)
    elif isinstance(value, dict):
        yield value
        if "@graph" in value:
            yield from structured_nodes(value["@graph"])


def has_type(node, name):
    types = node.get("@type", [])
    return name in (types if isinstance(types, list) else [types])


def same_origin(url, base):
    target, origin = urllib.parse.urlsplit(url), urllib.parse.urlsplit(base)
    return target.scheme in {"https", "http"} and (target.scheme, target.netloc) == (origin.scheme, origin.netloc)


def catalogue_links(body, page_url, source):
    page = CataloguePage()
    page.feed(body)
    prefix = source.get("productPathPrefix", "/product/")
    urls = []
    for link in page.links:
        url = urllib.parse.urljoin(page_url, link)
        parts = urllib.parse.urlsplit(url)
        if same_origin(url, source["url"]) and parts.path.startswith(prefix):
            clean = urllib.parse.urlunsplit(parts._replace(query="", fragment=""))
            if clean not in urls:
                urls.append(clean)
    next_url = urllib.parse.urljoin(page_url, page.next_url) if page.next_url else None
    if next_url and (not same_origin(next_url, source["url"])
                     or not urllib.parse.urlsplit(next_url).path.startswith(urllib.parse.urlsplit(source["endpoint"]).path.rstrip("/"))):
        raise ValueError("Catalogue next link left its configured public path")
    return urls, next_url


def product_item(body, url, source):
    """Convert an explicit Product schema into the existing Woo normalization."""
    page = CataloguePage()
    page.feed(body)
    nodes = [node for block in page.blocks for node in structured_nodes(block)]
    product = next((node for node in nodes if has_type(node, "Product")), None)
    if not product:
        raise ValueError("Product page has no usable Product metadata")
    title = product.get("name") or page.meta.get("og:title", "")
    suffix = " - " + source["name"]
    if title.endswith(suffix):
        title = title[:-len(suffix)]
    canonical = page.meta.get("og:url") or url
    if not same_origin(canonical, source["url"]):
        raise ValueError("Product metadata points outside its official source")
    # WordPress IDs survive URL/slug changes; otherwise retain canonical URL.
    identity = re.search(r"\bpostid-(\d+)\b", body)
    identity = identity[1] if identity else canonical
    aliases = {"year of issue": "year", "metal of coin": "metal"}
    attributes = []
    for prop in product.get("additionalProperty") or []:
        if not isinstance(prop, dict):
            continue
        label = str(prop.get("name", "")).removeprefix("pa_").replace("-", " ")
        label = aliases.get(label.lower(), label)
        value = prop.get("value")
        if label and value is not None:
            attributes.append({"name": label, "terms": [{"name": str(value)}]})
    images = product.get("image") or []
    if not isinstance(images, list):
        images = [images]
    images = [{"src": item.get("url") or item.get("@id")} if isinstance(item, dict) else {"src": item} for item in images]
    if not images and page.meta.get("og:image"):
        images = [{"src": page.meta["og:image"]}]
    item_page = next((node for node in nodes if (has_type(node, "ItemPage") or has_type(node, "WebPage"))
                      and str(node.get("url", "")).rstrip("/") == canonical.rstrip("/")), {})
    description = product.get("description") or "".join(page.description)
    # Including full, explicitly labelled specs also lets finish classification
    # read formats such as UV Printing without guessing from promotional copy.
    description += " " + " ".join(a["name"] + ": " + a["terms"][0]["name"] + "." for a in attributes)
    item = {"id": identity, "name": title, "permalink": canonical,
            "description": description, "images": images, "attributes": attributes,
            "categories": [{"name": product.get("category") or source.get("productTypeHint", "Collectible")}],
            "date_created_gmt": item_page.get("datePublished"), "prices": {}, "tags": []}
    if source.get("catalogueMaker"):
        item["brands"] = [{"name": source["catalogueMaker"]}]
    return item


def fetch_catalogue(fetcher, source, max_pages):
    """Return successful product details and explicit partial-coverage health."""
    max_products = max(1, min(80, int(source.get("maxProducts", 80))))
    urls, pages_seen = [], set()
    report = {"pagesFetched": 0, "paginationErrors": [], "latestWindowCapped": False,
              "discoveryFeeds": [], "backfillPagesFetched": 0, "skippedRechecks": 0}
    next_url = source["endpoint"]
    for _ in range(max_pages):
        if not next_url:
            break
        if next_url in pages_seen:
            report["paginationErrors"].append("Catalogue repeated a previously visited page")
            break
        pages_seen.add(next_url)
        try:
            fetcher.check_robots(next_url)
            page_urls, new_next = catalogue_links(fetcher.raw(next_url), next_url, source)
            if not page_urls:
                raise ValueError("Catalogue page exposed no product links")
        except (OSError, ValueError, RuntimeError, TypeError) as exc:
            if not urls:
                raise
            report["paginationErrors"].append("Catalogue page: " + str(exc)[:160])
            break
        report["pagesFetched"] += 1
        if all(url in urls for url in page_urls):
            report["paginationErrors"].append("Catalogue repeated earlier products; pagination stopped")
            break
        urls.extend(url for url in page_urls if url not in urls)
        next_url = new_next
    report["latestWindowCapped"] = bool(next_url) or len(urls) > max_products
    report["latestFetchedCount"] = len(urls)
    items = {}
    for url in urls[:max_products]:
        try:
            fetcher.check_robots(url)
            item = product_item(fetcher.raw(url), url, source)
            items.setdefault(str(item["id"]), item)
        except (OSError, ValueError, RuntimeError, TypeError) as exc:
            report["paginationErrors"].append("Product " + url + ": " + str(exc)[:120])
            # Stop on access denial/rate limits rather than probing other pages.
            if getattr(exc, "code", None) in {401, 403, 429}:
                break
    if not items:
        raise ValueError("No public product details could be read; previous observations preserved")
    return list(items.values()), report
