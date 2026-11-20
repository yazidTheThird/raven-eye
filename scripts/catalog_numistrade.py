"""Numistrade's public, non-retail release catalogue.

Each observed release page contains several separately specified coin variants.
Only linked detail pages are read; manufacturer availability is not retail stock.
"""
from hashlib import sha256
from html.parser import HTMLParser
import re
import urllib.parse

from catalog_html import VOID_TAGS, same_origin


class Node:
    def __init__(self, tag="", attrs=None):
        self.tag, self.attrs, self.children = tag, dict(attrs or []), []

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def text(self):
        return re.sub(r"\s+", " ", " ".join(c.text() if isinstance(c, Node) else c for c in self.children)).strip()

    def has_class(self, name):
        return name in self.attrs.get("class", "").split()


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.root = Node()
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse(body):
    page = Page()
    page.feed(body)
    return page.root


def base_url(root, url, source):
    base = next((n.attrs.get("href") for n in root.walk() if n.tag == "base"), None)
    resolved = urllib.parse.urljoin(url, base) if base else url
    if not same_origin(resolved, source["url"]):
        raise ValueError("Catalogue base left its official source")
    return resolved


def catalogue_links(body, url, source):
    root = parse(body)
    base = base_url(root, url, source)
    prefix = "/de/numistrade-muenzen-im-detail/"
    links = []
    for n in root.walk():
        if n.tag != "a" or not n.attrs.get("href"):
            continue
        target = urllib.parse.urljoin(base, n.attrs["href"])
        parts = urllib.parse.urlsplit(target)
        if same_origin(target, source["url"]) and parts.path.startswith(prefix) and parts.path.endswith(".html"):
            target = urllib.parse.urlunsplit(parts._replace(query="", fragment=""))
            if target not in links:
                links.append(target)
    return links


def translated(value):
    for german, english in [(r"\bSilber\b", "Silver"), (r"\bKupfer\b", "Copper"),
                            (r"\bGramm\b", "g"), (r"(?<![A-Za-z])Unze[n]?\b", "oz"),
                            (r"\b(?:farbig|koloriert|coloriert|Farbdruck)\b", "Colorized"),
                            (r"\bStück\b", "pieces")]:
        value = re.sub(german, english, value, flags=re.I)
    return value


def product_items(body, url, source):
    root = parse(body)
    base = base_url(root, url, source)
    content = next((n for n in root.walk() if n.has_class("c-newsFull__body")), None)
    if not content:
        raise ValueError("Release page has no public specification body")
    title = next((n.text() for n in content.walk() if n.tag == "h4"), "")
    series = next((n.text() for n in content.walk() if n.has_class("o-subline")), "")
    image = next((urllib.parse.urljoin(base, n.attrs["src"]) for n in content.walk()
                  if n.tag == "img" and n.attrs.get("src")), None)
    if image and urllib.parse.urlsplit(image).scheme not in {"http", "https"}:
        image = None
    # Contao's pageId identifies the shared reader template, not the release.
    page_id = urllib.parse.urlunsplit(urllib.parse.urlsplit(url)._replace(query="", fragment=""))
    aliases = {"ausgabeland": "Country", "motiv": "Theme", "ausgabejahr": "Year",
               "nennwert": "Denomination", "metall": "Metal", "gewicht": "Weight",
               "durchmesser": "Diameter", "qualität": "Finish", "auflage": "Mintage",
               "prägezeitraum": "Minting period", "status": "Manufacturer availability"}
    variants = {}
    heading = ""
    for node in content.walk():
        if node.tag == "h4":
            heading = node.text()
        if node.tag != "table":
            continue
        specs = {}
        for row in node.walk():
            if row.tag != "tr":
                continue
            cells = [n for n in row.children if isinstance(n, Node) and n.tag in {"td", "th"}]
            if len(cells) == 2:
                specs[cells[0].text().rstrip(":").casefold()] = cells[1].text()
        if not all(specs.get(k) for k in ["metall", "gewicht", "ausgabejahr"]) or not title:
            continue
        # Identical page/table copies collapse; order and changing mintage/stock
        # do not create a new listing. Denomination/finish distinguish variants.
        identity = [specs.get(k, "").casefold() for k in ["metall", "gewicht", "qualität", "nennwert"]]
        signature = sha256("|".join(identity).encode()).hexdigest()[:16]
        attrs = [{"name": aliases.get(k, k), "terms": [{"name": translated(v)}]} for k, v in specs.items()]
        label = " — ".join(v for v in [series, title, heading, translated(specs["gewicht"])] if v)
        description = "; ".join(a["name"] + ": " + a["terms"][0]["name"] for a in attrs)
        variants[signature] = {
            "id": page_id + ":" + signature, "name": translated(label), "permalink": url,
            "description": description, "attributes": attrs,
            "images": [{"src": image}] if image else [],
            "categories": [{"name": "Coin"}], "brands": [{"name": "Numistrade"}],
            "tags": [], "prices": {}, "date_created_gmt": None,
        }
    if not variants:
        raise ValueError("Release page has no complete coin specification tables")
    return list(variants.values())


def fetch_catalogue(fetcher, source, max_pages):
    """Read the current releases page and up to 40 linked release details."""
    limit = max(1, min(40, int(source.get("maxProducts", 40))))
    endpoint = source["endpoint"]
    fetcher.check_robots(endpoint)
    urls = catalogue_links(fetcher.raw(endpoint), endpoint, source)
    if not urls:
        raise ValueError("Numistrade latest releases page exposed no detail links")
    report = {"pagesFetched": 1, "paginationErrors": [], "latestWindowCapped": len(urls) > limit,
              "discoveryFeeds": [], "backfillPagesFetched": 0, "skippedRechecks": 0,
              "productPagesFetched": 0, "detailLimit": limit}
    items = {}
    for url in urls[:limit]:
        try:
            fetcher.check_robots(url)
            variants = product_items(fetcher.raw(url), url, source)
            for item in variants:
                items.setdefault(item["id"], item)
            report["productPagesFetched"] += 1
        except (OSError, ValueError, RuntimeError, TypeError) as exc:
            report["paginationErrors"].append("Product " + url + ": " + str(exc)[:120])
            if getattr(exc, "code", None) in {401, 403, 429}:
                break
    if not items:
        raise ValueError("No Numistrade specification pages could be read; previous observations preserved")
    report["latestFetchedCount"] = len(items)
    return list(items.values()), report
