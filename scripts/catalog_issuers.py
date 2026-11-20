"""Public issuer catalogues and Coins Today's published own-brand collection.

The bank's issue dates are not storefront publication dates. Catalogue-only
records never imply stock. Coins Today stock is taken only from visible badges
or order controls on the same product card, never from an absent price.
"""
from datetime import datetime
from html.parser import HTMLParser
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit


VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class Node:
    def __init__(self, tag="root", attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def all(self, tag=None, cls=None):
        for node in self.children:
            if isinstance(node, Node):
                if (tag is None or node.tag == tag) and (cls is None or cls in node.attrs.get("class", "").split()):
                    yield node
                yield from node.all(tag, cls)

    def text(self):
        return re.sub(r"\s+", " ", " ".join(c.text() if isinstance(c, Node) else c for c in self.children)).strip()


class Document(HTMLParser):
    def __init__(self, body):
        super().__init__()
        self.root = Node()
        self.stack = [self.root]
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                self.stack = self.stack[:i]
                break

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if self.stack[-1].tag not in {"script", "style"}:
            self.stack[-1].children.append(data)


def first(node, cls=None, tag=None):
    return next(node.all(tag=tag, cls=cls), Node())


def absolute(url, base):
    value = urljoin(base, url)
    return value if urlsplit(value).scheme in {"http", "https"} else None


def same_origin(url, base):
    a, b = urlsplit(url), urlsplit(base)
    return a.scheme in {"http", "https"} and (a.scheme, a.netloc) == (b.scheme, b.netloc)


def attr(name, value):
    return {"name": name, "terms": [{"name": str(value)}]}


def issue_date(value):
    try:
        return datetime.strptime(value, "%d.%m.%Y").strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def cbr_rows(body, url):
    """Current-year database rows include exact issue dates and unique variants."""
    root = Document(body).root
    rows = []
    for row in root.all(tag="tr"):
        target = row.attrs.get("data-tr-link", "")
        target = absolute(target, url)
        if not target or not same_origin(target, url) or not urlsplit(target).path.endswith("/ShowCoins/"):
            continue
        identity = parse_qs(urlsplit(target).query).get("cat_num", [""])[0]
        name = first(row, cls="coins-list_item_name").text()
        metal = first(row, cls="coins-list_item_metall").text()
        face = first(row, cls="coins-list_item_face-value").text()
        released = issue_date(first(row, cls="coins-list_item_date").text())
        src = first(row, tag="img").attrs.get("src")
        image = absolute(src, url) if src else None
        if not identity or not name or not metal or not released or not image:
            raise ValueError("Bank coin row is missing its identity, material, image, or issue date")
        rows.append({"id": identity, "name": name, "permalink": target,
                     "description": f"{metal} coin. Denomination: {face}. Official date of issue: {released}.",
                     "images": [{"src": image}], "categories": [{"name": "Coins"}],
                     "attributes": [attr("Metal", metal), attr("Year", released[:4]), attr("Denomination", face)],
                     "prices": {}, "tags": [], "_releaseDate": released, "_metadataPartial": True,
                     "_releaseDateEvidence": "Bank of Russia official Date of Issue field."})
    if not rows:
        raise ValueError("Bank of Russia catalogue exposed no coin rows")
    return sorted(rows, key=lambda item: (item["_releaseDate"], item["id"]), reverse=True)


def cbr_detail(body, item):
    root = Document(body).root
    content = first(root, cls="commemor-coin_content")
    if not content.children:
        raise ValueError("Bank coin detail has no commemorative coin content")
    identity = next((node.attrs.get("value") for node in root.all(tag="input")
                     if node.attrs.get("name") == "UniDbQuery.cat_num"), None)
    if identity != item["id"]:
        raise ValueError("Bank detail identity does not match the catalogue coin")
    specs = {}
    for row in content.all(cls="commemor-coin_info_characteristic"):
        label = first(row, cls="characteristic_denomenation").text()
        value = first(row, cls="characteristic_value").text()
        if label and value:
            specs[label] = value
    if not any(name in specs for name in ("Metal, fineness", "Alloy", "Material")):
        raise ValueError("Bank detail has no explicit material specification")
    aliases = {"Metal, fineness": "Metal", "Alloy": "Metal", "Material": "Metal",
               "Mintage, pcs": "Mintage", "Quality": "Finish",
               "Denomination": "Denomination"}
    extra = [attr(aliases[k], v) for k, v in specs.items() if k in aliases]
    # Preserve total and fine metal weights separately; the displayed weight is
    # fine metal content, as explicitly labelled by the issuer.
    fine = specs.get("Fine metal content not less than, g")
    if fine:
        extra.append(attr("Weight", fine + " g"))
    paragraphs = [node.text() for node in content.all(tag="p")]
    item = {**item, "attributes": item["attributes"] + extra, "_metadataPartial": False,
            "description": item["description"] + " " + " ".join(f"{k}: {v}." for k, v in specs.items()) + " " + " ".join(paragraphs)}
    return item


def coins_today_items(body, source):
    root = Document(body).root
    items = []
    for card in root.all(cls="prd_box"):
        link = next((node for node in card.all(tag="a") if "gp_id=" in node.attrs.get("href", "")), None)
        if not link:
            continue
        # AJAX fragments resolve relative links in the embedding index page.
        target = absolute(link.attrs["href"], source["url"].rstrip("/") + "/index.php")
        if not target or not same_origin(target, source["url"]):
            continue
        query = parse_qs(urlsplit(target).query)
        identity = query.get("gp_id", [""])[0]
        if query.get("menu") != ["goods_detail"] or not identity:
            continue
        name = first(card, cls="prd_box2_name").text()
        image_node = first(card, cls="prd_box_img", tag="img")
        src = image_node.attrs.get("src")
        image = absolute(src, source["url"]) if src else None
        if not name or not image:
            raise ValueError("Coins Today product card has no usable title or image")
        specs = first(card, cls="prd_overlay_info").text()
        state, evidence = "unknown", "Public own-brand card has no explicit current stock label."
        if first(card, cls="soldout_badge").text().upper() == "SOLD OUT":
            state, evidence = "out_of_stock", "Coins Today public product card explicitly displays SOLD OUT."
        price_text = first(card, cls="prd_box2_price").text()
        price = re.fullmatch(r"\$\s*([\d,]+(?:\.\d+)?)", price_text)
        items.append({"id": identity, "name": name, "permalink": target,
                      "description": specs, "images": [{"src": image}], "attributes": [],
                      "categories": [{"name": "Precious metal collectibles"}], "prices": {}, "tags": [],
                      "brands": [{"name": "Coins Today"}], "_status": state, "_statusEvidence": evidence,
                      "_price": float(price[1].replace(",", "")) if price else None,
                      "_currency": "USD" if price else None})
    if not items:
        raise ValueError("Coins Today collection exposed no product cards")
    # These are visible pagination controls emitted by the public collection.
    pages = [int(match[1]) for node in root.all(tag="a")
             if (match := re.fullmatch(r"loadProductList\((\d+),'ALL','','new'\)", node.attrs.get("onclick", "")))]
    return items, max(pages, default=1)


def read(fetcher, url):
    fetcher.check_robots(url)
    return fetcher.raw(url)


def fetch_catalogue(fetcher, source, max_pages):
    report = {"pagesFetched": 0, "paginationErrors": [], "latestWindowCapped": False,
              "discoveryFeeds": [], "backfillPagesFetched": 0, "skippedRechecks": 0,
              "detailPagesFetched": 0, "detailWindowCapped": False}
    if source["type"] == "cbr_catalog":
        rows = cbr_rows(read(fetcher, source["endpoint"]), source["endpoint"])
        report["pagesFetched"] = 1
        maximum = max(1, min(80, int(source.get("maxProducts", 80))))
        detail_limit = max(0, min(40, int(source.get("detailLimit", 12))))
        report["latestWindowCapped"] = len(rows) > maximum
        items = rows[:maximum]
        report["detailWindowCapped"] = len(items) > detail_limit
        for index, item in enumerate(items[:detail_limit]):
            try:
                body = read(fetcher, item["permalink"])
                report["detailPagesFetched"] += 1
                items[index] = cbr_detail(body, item)
            except (OSError, ValueError, RuntimeError, TypeError) as exc:
                report["paginationErrors"].append("Coin detail " + item["id"] + ": " + str(exc)[:120])
                if getattr(exc, "code", None) in {401, 403, 429}:
                    report["detailWindowCapped"] = True
                    break
        report["latestFetchedCount"] = len(items)
        return items, report
    if source["type"] != "coins_today":
        raise ValueError("Unsupported issuer catalogue type")
    items, seen, more = [], set(), 1
    for page in range(1, min(4, max_pages) + 1):
        if page > more:
            break
        parts = urlsplit(source["endpoint"])
        query = parse_qs(parts.query)
        query["page"] = [str(page)]
        url = urlunsplit(parts._replace(query=urlencode(query, doseq=True)))
        try:
            batch, more = coins_today_items(read(fetcher, url), source)
            if all(item["id"] in seen for item in batch):
                raise ValueError("Coins Today repeated a catalogue page")
        except (OSError, ValueError, RuntimeError, TypeError) as exc:
            if not items:
                raise
            report["paginationErrors"].append("Collection page " + str(page) + ": " + str(exc)[:120])
            break
        for item in batch:
            if item["id"] not in seen:
                seen.add(item["id"])
                items.append(item)
        report["pagesFetched"] += 1
    report["latestWindowCapped"] = more > report["pagesFetched"]
    report["latestFetchedCount"] = len(items)
    return items, report
