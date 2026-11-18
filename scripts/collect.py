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

# Support both direct CLI execution and importlib-based collector tests.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from classify import classify_product
from catalog_html import fetch_catalogue

ROOT = Path(__file__).resolve().parents[1]
USER_AGENT = "RavenEye/1.0 (+https://github.com/yazidTheThird/raven-eye; public-release-monitor)"
TIMEOUT = 25
MAX_RESPONSE = 24 * 1024 * 1024
RECHECK_LIMIT = 12
MAX_PAGES = 4
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


def explicit_copper_metal(title):
    """Conservative title-only substrate evidence, also safe for old records."""
    s = re.sub(r"[‐‑–—]", "-", title.lower())
    if re.search(r"\b(?:cupronickel|copper[ -]nickel|copper\s+alloy)\b", s):
        return "Other"
    # A copper-coloured or copper-plated surface is not a copper substrate.
    s = re.sub(r"\bcopper[ -]+(?:plated|plating|colou?red|colou?r|toned?|finish)\b", "", s)
    if not re.search(r"\bcopper\b", s):
        return None
    # Remove named precious-metal finishes before checking for mixed substrates.
    s = re.sub(r"\b(?:silver|gold)[ -]+(?:plated|plating|gilded|enameled|enamelled)\b", "", s)
    quantity = r"\d+(?:[.,/]\d+)?\s*(?:(?:troy|avdp)\s*)?(?:oz|ounces?|kilograms?|kg|grams?|g)\b"
    purity = r"(?:\.?999\d*|99[.,]9\d*\s*%)"
    modifiers = r"[\s,.-]*(?:(?:fine|pure)\s+)?"
    forms = r"(?:coins?|rounds?|bars?|medals?|medallions?|bullion|sculptures?|statues?|ingots?)\b"
    def substrate(metal):
        return bool(re.search(r"(?:" + quantity + r"[\s,.-]*(?:" + purity + r")?|" + purity + r")" + modifiers + metal + r"\b", s)
                    or re.search(r"\b(?:fine|pure)\s+" + metal + r"\b", s)
                    or re.search(r"\b" + metal + r"\s+" + forms, s))
    copper = substrate("copper")
    if copper and (substrate("silver") or substrate("gold")
                   or re.search(r"\b(?:with|and|insert)\b.{0,80}\b(?:silver|gold)\b", s)
                   or re.search(r"\bcopper\s+core\b.*\b(?:silver|gold)\b|\b(?:silver|gold)\b.*\bcopper\s+core\b", s)):
        return "Other"  # A copper core plus a silver coin is a mixed object.
    if re.search(r"\b(?:copper\s*(?:&|and)\s*silver|silver\s*(?:&|and)\s*copper)\b", s):
        return "Other"
    return "Copper" if copper else None


def detect_metal(title, detail=""):
    # Examine the product title/type before the prose, which may mention other
    # products or describe a gold-coloured surface on a silver substrate.
    for candidate in [title, detail]:
        s = candidate.lower()
        # Explicit material terms in public German/Portuguese product metadata.
        s = re.sub(r"\bsilber(?:münze|muenze|barren)?\b|\bprata\b", "silver", s)
        s = re.sub(r"\bouro\b", "gold", s)
        # Element symbols paired with fineness establish material; a named
        # pot of gold is a design theme, not a statement of composition.
        s = re.sub(r"\bag\s*\.?999(?:[.,]\d+)?\b", "silver", s)
        s = re.sub(r"\bpot of gold\b", "", s)
        explicit_copper = explicit_copper_metal(s)
        if explicit_copper is not None:
            return explicit_copper
        # Do not classify a decorative copper colour or alloy as pure copper.
        s = re.sub(r"\bcopper[ -]+(?:plated|plating|colou?red|colou?r|toned?|finish)\b", "", s)
        coating = r"(?:silver|gold)[ -](?:plated|gilded)"
        if re.search(coating + r".{0,40}\bcopper\b", s) or re.search(r"\bcopper\b.{0,40}" + coating, s):
            return "Copper"
        if re.search(coating + r".{0,40}\b(?:brass|base[ -]metal)\b", s) or re.search(r"\b(?:brass|base[ -]metal)\b.{0,40}" + coating, s):
            return "Other"
        if re.search(r"\b(?:silver\s*(?:&|and|or)\s*(?:pure\s+)?gold|gold\s*(?:&|and|or)\s*(?:pure\s+)?silver)\b", s):
            return "Other"  # Mixed-metal sets or chance-based variants.
        if re.search(r"\bsilver\b", s):
            return "Silver"
        for metal in ["Platinum", "Palladium", "Gold"]:
            if re.search(r"\b" + metal.lower() + r"\b", s):
                return metal if metal != "Palladium" else "Other"
        if s.strip() == "copper":  # A structured metal field is unambiguous.
            return "Copper"
    return "Other"


def detect_category(title, product_type="", default="Collectible"):
    # Sellers group unrelated forms in taxonomy labels such as "Silver Rounds
    # and Bars". An explicit title describes this release more precisely.
    # Within the same title, bars and sculptures retain priority over "coin"
    # (for example a coin-in-bar or a sculpture made from a coin).
    for s in [title.lower(), product_type.lower()]:
        if re.search(r"\b(bar|bars|barre|ingot|ingots|barren|anlagebarren|lingote)\b", s):
            return "Bar"
        if re.search(r"\b(foil|blind box|sculpture|statue|figurine|coin.?card|medallion|note|pendant)\b", s):
            return "Collectible"
        if re.search(r"\b(coin|coins|round|rounds|moeda|moedas|münze|münzen|silbermünze|silbermünzen|goldmünze|goldmünzen|anlagemünze)\b", s):
            return "Coin"
    return default


def details(title, product_type, description, tags):
    primary = title + " " + product_type
    title_composition = explicit_copper_metal(title)
    metal = title_composition or detect_metal(title)
    if metal == "Other" and title_composition is None:
        metal = detect_metal(product_type, description)
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
        "metal": metal,
        "category": detect_category(title, product_type),
        "weight": weight_text,
        "year": int(year[1]) if year else None,
        "mintage": mintage_number,
    }


def excluded_title(title):
    """Narrow, explicit non-release titles; safe for historical-data cleanup."""
    # Accessories can mention coins and precious metals without containing any.
    # Match the accessory itself, preserving e.g. "Silver Coin in Capsule".
    if re.search(r"\b(?:hoodie|hoodies|coin[ -]+capsules?)\b", title, re.I):
        return True
    if re.search(r"\bguide\s*book\b", title, re.I):
        # A book-themed bullion bar is a physical release, not a reference book.
        physical_release = re.search(r"\d+(?:[.,]\d+)?\s*(?:oz|ounces?|g|grams?)\b", title, re.I) and re.search(r"\b(?:silver|gold)\s+(?:art\s+)?(?:bar|coin|round)\b", title, re.I)
        if not physical_release:
            return True
    return bool(re.search(r"gift\s*(card|certificate)|display\s*(case|box)|empty\s*(box|capsule)|pool\s+allocated|wall\s+calendar", title, re.I))


def is_relevant(title, product_type, description, tags):
    primary = (title + " " + product_type).lower()
    if excluded_title(title) or re.fullmatch(r"(?:calendar|book|catalogue|catalog|magazine)s?", product_type.strip(), re.I):
        return False
    release_terms = r"\b(coin|coins|silver|gold|platinum|palladium|copper|bullion|bar|bars|round|rounds|foil|medal|medallion|sculpture|statue|blind box|silber|silbermünze|silbermünzen|goldmünze|goldmünzen|barren|anlagebarren|prata|ouro|moeda|moedas)\b"
    if re.search(release_terms, primary) or any(re.search(release_terms, tag, re.I) for tag in tags):
        return True
    # Short artistic titles often put the actual product type only in the body.
    # Require physical material specifications alongside the object, rather
    # than accepting a generic footer saying the shop sells silver and gold.
    body = description.lower()
    metal = r"(?:silver|gold|platinum|palladium|copper|silber|prata|ouro)"
    physical = r"(?:coin|round|bar|medal|medallion|foil|sculpture|statue|moeda|münze)"
    quantity = r"\d+(?:[.,/]\d+)?\s*(?:troy\s*)?(?:oz|ounces?|kg|grams?|gr|gm|g)\b"
    purity = r"(?:\.?999\d*|925|999/1000|99[.,]9\d*\s*%)"
    return bool(re.search(r"(?:" + quantity + "|" + purity + r").{0,70}\b" + metal + r"\b.{0,45}\b" + physical + r"s?\b", body)
                or re.search(r"\b" + metal + r"\b.{0,45}\b" + physical + r"s?\b.{0,70}(?:" + quantity + "|" + purity + ")", body))


def clean_tags(values, limit=30):
    if isinstance(values, str):
        values = values.split(",")
    return list(dict.fromkeys(text(v.get("name", "") if isinstance(v, dict) else v) for v in (values or [])))[:limit]


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
    maker = None
    maker_evidence = None
    specification_values = {}
    specification_evidence = {}
    if kind == "shopify":
        title = text(item.get("title"))
        description = text(item.get("body_html"))
        product_type = text(item.get("product_type"))
        tags = clean_tags(item.get("tags"), limit=None)
        # Only explicitly labelled material tags affect material classification.
        product_type += " " + " ".join(tag for tag in tags if re.match(r"^METAL\s", tag, re.I))
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
        maker = vendor
        if maker:
            maker_evidence = "Shopify vendor / source-reported brand; not independently verified as the manufacturer."
    elif kind == "woocommerce":
        title = text(item.get("name"))
        short_description = text(item.get("short_description"))
        description = " ".join(dict.fromkeys(v for v in [short_description, text(item.get("description"))] if v))
        tags = clean_tags(item.get("tags"), limit=None)
        product_type = " ".join(clean_tags(item.get("categories"))) + " " + short_description
        for attribute in item.get("attributes") or []:
            label = text(attribute.get("name")).lower()
            values = ", ".join(clean_tags(attribute.get("terms") or attribute.get("options")))
            if not values:
                continue
            if label in {"metal", "metal type", "composition"}:
                specification_values["metal"] = detect_metal(values)
                product_type += " " + values
                specification_evidence["metal"] = "Source-reported " + label + ": " + values
            elif re.fullmatch(r"weight(?:\s*\((?:oz|g|kg)\))?", label):
                weight = details(values, "", "", [])["weight"]
                if weight:
                    specification_values["weight"] = weight
                    specification_evidence["weight"] = "Source-reported " + label + ": " + values
            elif label in {"year", "years", "year of issue"} and re.fullmatch(r"20\d{2}", values):
                specification_values["year"] = int(values)
                specification_evidence["year"] = "Source-reported " + label + ": " + values
            elif label == "mintage" and re.fullmatch(r"[\d][\d, .]*(?:\s*(?:pcs|pieces|coins|units))?", values, re.I):
                count = int(re.sub(r"[^\d]", "", values))
                if 0 < count < 100_000_000:
                    specification_values["mintage"] = count
                    specification_evidence["mintage"] = "Source-reported mintage: " + values
        prices = item.get("prices") or {}
        price = number(prices.get("price"))
        if price is not None:
            price = round(price / (10 ** int(prices.get("currency_minor_unit", 2))), 4)
        currency = prices.get("currency_code")
        published = iso(item.get("date_created_gmt"), utc=True)
        url = item.get("permalink")
        images = item.get("images") or []
        # PHP arrays with missing numeric indexes serialize as objects. Some
        # public Woo feeds retain image keys 0, 2, ... after removing an image.
        if isinstance(images, dict):
            images = images.values()
        image = next((safe_url(i.get("src")) or safe_url(i.get("thumbnail")) for i in images
                      if isinstance(i, dict) and (safe_url(i.get("src")) or safe_url(i.get("thumbnail")))), None)
        status, evidence = woo_status(item)
        variant_count = max(1, len(item.get("variations") or []))
        vendor = ", ".join(clean_tags(item.get("brands"))) or None
        maker = vendor
        if maker:
            maker_evidence = "Source-reported WooCommerce brand; not independently verified as the manufacturer."
        else:
            for attribute in item.get("attributes") or []:
                label = text(attribute.get("name")).lower()
                taxonomy = text(attribute.get("taxonomy")).lower().removeprefix("pa_")
                if label in {"brand", "manufacturer", "mint", "producer", "maker"} or taxonomy in {"brand", "manufacturer", "mint", "producer", "maker"}:
                    maker = ", ".join(clean_tags(attribute.get("terms") or attribute.get("options"))) or None
                    if maker:
                        maker_evidence = "Source-reported WooCommerce " + (label or taxonomy) + " attribute."
                        break
    else:
        title = text((item.get("title") or {}).get("rendered"))
        description = text((item.get("content") or {}).get("rendered"))
        tags = [v.removeprefix("product_tag-").replace("-", " ") for v in item.get("class_list", []) if v.startswith("product_tag-")]
        product_type = " ".join(v.removeprefix("product_cat-").replace("-", " ") for v in item.get("class_list", []) if v.startswith("product_cat-"))
        for value in item.get("class_list", []):
            if value.startswith("product_types-"):
                material = value.removeprefix("product_types-").replace("-", " ")
                product_type += " " + material
                if detect_metal(material) != "Other":
                    specification_values["metal"] = detect_metal(material)
                    specification_evidence["metal"] = "Public product taxonomy: " + value
            if value in {"type-bars", "type-coins", "type-rounds"}:
                product_type += " " + value.removeprefix("type-")
            match = re.fullmatch(r"product_weight-(\d+(?:[.,]\d+)?)-(oz|g|kg)", value)
            if match:
                specification_values["weight"] = match[1].replace(",", ".") + " " + match[2]
                specification_evidence["weight"] = "Public product taxonomy: " + value
        yoast = item.get("yoast_head_json") or {}
        price = number(yoast.get("product_price_amount"))
        currency = yoast.get("product_price_currency")
        published = iso(item.get("date_gmt"), utc=True)
        url = item.get("link")
        image = next((safe_url(i.get("url")) for i in yoast.get("og_image", []) if safe_url(i.get("url"))), None)
        media = (item.get("_embedded") or {}).get("wp:featuredmedia") or []
        image = next((safe_url(i.get("source_url")) for i in media if isinstance(i, dict) and safe_url(i.get("source_url"))), image)
        status, evidence = wordpress_status(item)
        variant_count = 1
        vendor = None
    product_type += " " + source.get("productTypeHint", "")
    if kind == "wordpress" and {str(value) for value in item.get("categories", [])} & {str(value) for value in source.get("excludeCategoryIds", [])}:
        return None
    if any(urllib.parse.urlsplit(url or "").path.startswith(prefix) for prefix in source.get("excludePathPrefixes", [])):
        return None
    if any(value.casefold() in title.casefold() for value in source.get("excludeTitleContains", [])):
        return None
    if not title or not safe_url(url) or not is_relevant(title, product_type, description, tags):
        return None
    if image in source.get("excludeImageUrls", []):
        image = None
    # Overrides must be explicit source-specific tag mappings backed by evidence.
    for tag, mapped_status in source.get("statusTags", {}).items():
        if mapped_status in VALID_STATUSES and any(value.casefold() == tag.casefold() for value in tags):
            status, evidence = mapped_status, "Public source tag explicitly indicates " + tag + "."
            break
    if source.get("catalogOnly"):
        status, evidence = "unknown", "Catalogue/announcement source; this feed does not verify current retail availability."
    if source.get("retailPathSuffix") and not urllib.parse.urlsplit(url).path.rstrip("/").endswith(str(source["retailPathSuffix"]).rstrip("/")):
        status, evidence = "unknown", "Manufacturer catalogue entry; only this source's explicitly separated retail product paths verify purchase availability."
    # Zero-price placeholders should not appear as free precious metals.
    if price == 0:
        price = None
    if currency and not re.fullmatch(r"[A-Z]{3}", currency):
        currency = None
    specifications = {**details(title, product_type, description, tags), **specification_values}
    title_metal = explicit_copper_metal(title)
    if title_metal is not None and specification_evidence.get("metal", "").startswith("Public product taxonomy:"):
        specifications["metal"] = title_metal
        specification_evidence["metal"] = "Explicit title composition: " + title
    if specifications["metal"] == "Other" and not re.search(r"\b(?:silver|gold|platinum|palladium|copper|brass|silber|prata|ouro)\b", title + " " + product_type + " " + description, re.I):
        material_tags = [tag for tag in tags if re.fullmatch(r"(?:metal\s*[:\-]?\s*)?(?:silver|gold|platinum|palladium|copper|silber|prata|ouro)(?:\s+(?:coins?|rounds?|bars?|bullion))?", tag, re.I)]
        material_values = {detect_metal(tag) for tag in material_tags} - {"Other"}
        if len(material_values) == 1:
            specifications["metal"] = material_values.pop()
            specification_evidence["metal"] = "Source-reported product tag: " + ", ".join(material_tags)
    classification = classify_product(title, product_type, description, tags,
                                      attributes=item.get("attributes"),
                                      metal=specifications["metal"], category=specifications["category"])
    if specifications["category"] == "Collectible" and classification.get("isColorizedSilverCoin"):
        specifications["category"] = "Coin"
    return {
        "id": source["id"] + ":" + str(item["id"]),
        "sourceProductId": str(item["id"]),
        "sourceId": source["id"], "sourceName": source["name"],
        "title": title, "url": url, "image": image,
        **specifications, **classification,
        "specificationEvidence": specification_evidence,
        "price": price, "currency": currency, "status": status,
        "publishedAt": published, "description": description[:277] + ("…" if len(description) > 277 else ""),
        "tags": tags[:20], "statusEvidence": evidence, "vendor": vendor,
        "maker": maker, "makerEvidence": maker_evidence,
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
    return base + "/wp-json/wp/v2/" + source.get("wpRestBase", "product") + "/" + product["sourceProductId"]


def bounded_pages(source):
    try:
        maximum = 8 if source.get("type") == "html_catalog" else MAX_PAGES
        return max(1, min(maximum, int(source.get("maxPages", 1))))
    except (TypeError, ValueError):
        return 1


def page_url(url, page):
    parts = urllib.parse.urlsplit(url)
    if page == 1 and "page" not in dict(urllib.parse.parse_qsl(parts.query)):
        return url
    query = [(key, value) for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if key != "page"]
    query.append(("page", str(page)))
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def page_size(source):
    if source.get("type") == "html_catalog":
        return max(1, min(80, int(source.get("limit", 12))))
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(source.get("endpoint", "")).query))
    key = "limit" if source["type"] == "shopify" else "per_page"
    default = 250 if source["type"] == "shopify" else 100
    try:
        return max(1, min(default, int(query.get(key, source.get("limit", default)))))
    except (TypeError, ValueError):
        return default


def invalid_page_error(exc, kind):
    """Only a structured WordPress invalid-page response proves exhaustion."""
    if kind not in {"wordpress", "woocommerce"} or not isinstance(exc, urllib.error.HTTPError) or exc.code != 400:
        return False
    try:
        payload = json.loads(exc.read(4096).decode("utf-8"))
        return isinstance(payload, dict) and payload.get("code") == "rest_post_invalid_page_number"
    except (OSError, ValueError, UnicodeError, AttributeError):
        return False


def fetch_window(fetcher, source, start_page, count, require_first=False, seen_before=None):
    """Read a bounded window; failed pages are never skipped by the cursor."""
    items_by_id = {}
    prior_ids = set(seen_before or [])
    report = {"pagesFetched": 0, "paginationErrors": [], "windowCapped": False,
              "exhausted": False, "nextPage": start_page}
    for page in range(start_page, start_page + count):
        try:
            payload = fetcher.json(page_url(source["endpoint"], page))
            items = payload.get("products") if isinstance(payload, dict) and source["type"] == "shopify" else payload
            if not isinstance(items, list) or (require_first and page == 1 and not items):
                raise ValueError("Feed returned no product list; previous observations preserved")
            if any(not isinstance(item, dict) or item.get("id") is None for item in items):
                raise ValueError("Feed contained a product without a stable ID")
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            if page > 1 and invalid_page_error(exc, source["type"]):
                report["exhausted"] = True
                break
            if require_first and page == 1:
                raise
            report["paginationErrors"].append("Page " + str(page) + ": " + str(exc)[:160])
            break
        report["pagesFetched"] += 1
        new_ids = {str(item["id"]) for item in items} - items_by_id.keys() - prior_ids
        # Detect ignored page arguments without calling a repeated page a full
        # catalogue traversal. Partial overlap remains valid during live edits.
        if items and not new_ids:
            report["paginationErrors"].append("Page " + str(page) + ": the feed repeated an earlier page; pagination stopped without duplicate listings.")
            break
        for item in items:
            items_by_id.setdefault(str(item["id"]), item)
        report["nextPage"] = page + 1
        if len(items) < page_size(source):
            report["exhausted"] = True
            break
    report["windowCapped"] = not report["exhausted"] and not report["paginationErrors"] and report["pagesFetched"] == count
    report["fetchedCount"] = len(items_by_id)
    return items_by_id, report


def backfill_limits(source):
    config = source.get("catalogueBackfill")
    if not isinstance(config, dict):
        return None
    start = bounded_pages(source) + 1
    try:
        pages = max(1, min(MAX_PAGES, int(config.get("pagesPerRun", 2))))
        maximum = max(start, min(100, int(config.get("maxPage", 100))))
    except (TypeError, ValueError):
        return None
    return start, pages, maximum


def fetch_source(source, old_products, previous_health=None):
    # A directory is a useful, explicit coverage gap, never a pretend live feed.
    if source["type"] == "directory":
        return [], 0, [], {"pagesFetched": 0, "paginationErrors": [], "skippedRechecks": 0}
    if source["type"] == "html_catalog":
        items, report = fetch_catalogue(Fetcher(), source, bounded_pages(source))
        normalized = []
        for item in items:
            current = normalize({**source, "type": "woocommerce", "catalogOnly": True}, item)
            if current:
                current.update({"discoveryReason": "latest_window", "baselineDiscovery": False})
                if source.get("catalogueMaker"):
                    current["makerEvidence"] = "Official maker's own product catalogue."
                normalized.append(current)
        if not normalized:
            raise ValueError("Public HTML catalogue had no relevant releases; previous observations preserved")
        report.update({"acceptedCount": len(normalized), "rejectedCount": len(items) - len(normalized),
                       "windowLimit": min(bounded_pages(source) * page_size(source), max(1, min(80, int(source.get("maxProducts", 80))))),
                       "detailLimit": max(1, min(80, int(source.get("maxProducts", 80))))})
        return normalized, len(items), [], report
    if source["type"] not in {"shopify", "woocommerce", "wordpress"}:
        raise ValueError("Unsupported public feed type")
    fetcher = Fetcher()
    source = copy.deepcopy(source)
    previous_health = previous_health or {}
    items_by_id, latest = fetch_window(fetcher, source, 1, bounded_pages(source), require_first=True)
    origins = {item_id: {"discoveryReason": "latest_window", "baselineDiscovery": False} for item_id in items_by_id}
    report = {"pagesFetched": latest["pagesFetched"], "paginationErrors": list(latest["paginationErrors"]),
              "latestWindowCapped": latest["windowCapped"], "latestFetchedCount": len(items_by_id),
              "discoveryFeeds": [], "backfillPagesFetched": 0}
    old_feeds = {feed["id"]: feed for feed in previous_health.get("discoveryFeeds", [])}
    for index, feed in enumerate(source.get("discoveryFeeds") or []):
        if not isinstance(feed, dict) or not safe_url(feed.get("endpoint")):
            report["paginationErrors"].append("Discovery feed " + str(index + 1) + ": invalid public endpoint")
            continue
        feed_id = str(feed.get("id") or index + 1)
        previous_feed = old_feeds.get(feed_id, {})
        # Changing a feed endpoint is a new baseline even if its label is reused.
        feed_source = {**source, "endpoint": feed["endpoint"], "maxPages": feed.get("maxPages", 1)}
        baseline = (not previous_feed.get("baselineComplete") or previous_feed.get("endpoint") != feed["endpoint"]
                    or bounded_pages(feed_source) > previous_feed.get("maxPages", 0))
        extra, feed_report = fetch_window(fetcher, feed_source, 1, bounded_pages(feed_source))
        feed_report.update({"id": feed_id, "label": feed.get("label", feed_id), "endpoint": feed["endpoint"],
                            "maxPages": bounded_pages(feed_source),
                            "status": "partial" if feed_report["paginationErrors"] and feed_report["pagesFetched"] else "error" if feed_report["paginationErrors"] else "ok",
                            "lastSuccess": previous_feed.get("lastSuccess") if previous_feed.get("endpoint") == feed["endpoint"] else None,
                            "baselineComplete": not baseline or not feed_report["paginationErrors"]})
        report["discoveryFeeds"].append(feed_report)
        report["paginationErrors"].extend("Discovery " + feed_id + " " + error for error in feed_report["paginationErrors"])
        for item_id, item in extra.items():
            if item_id not in items_by_id:
                items_by_id[item_id] = item
                origins[item_id] = {"discoveryReason": "discovery_feed", "discoveryFeedId": feed_id, "baselineDiscovery": baseline}
    limits = backfill_limits(source)
    if limits:
        start, budget, maximum = limits
        try:
            cursor = max(start, min(maximum, int(previous_health.get("nextBackfillPage", start))))
        except (ValueError, TypeError):
            cursor = start
        # A short current window already proves there are no deeper pages.
        if latest["exhausted"]:
            backfill = {"pagesFetched": 0, "paginationErrors": [], "exhausted": True,
                        "nextPage": start, "fetchedCount": 0, "windowCapped": False}
            extra = {}
        elif latest["paginationErrors"]:
            backfill = {"pagesFetched": 0, "paginationErrors": [], "exhausted": False,
                        "nextPage": cursor, "fetchedCount": 0, "windowCapped": False}
            extra = {}
        else:
            # Compare only against the primary window: collections can overlap
            # valid catalogue pages without indicating ignored pagination.
            primary_ids = {key for key, value in origins.items() if value["discoveryReason"] == "latest_window"}
            extra, backfill = fetch_window(fetcher, source, cursor, min(budget, maximum - cursor + 1), seen_before=primary_ids)
        reached_limit = backfill["nextPage"] > maximum
        report.update({"backfillStartPage": cursor, "backfillPagesFetched": backfill["pagesFetched"],
                       "backfillFetchedCount": backfill["fetchedCount"], "backfillReachedEnd": backfill["exhausted"],
                       "backfillReachedLimit": reached_limit, "backfillMaxPage": maximum,
                       "nextBackfillPage": start if backfill["exhausted"] or reached_limit else backfill["nextPage"]})
        report["paginationErrors"].extend("Backfill " + error for error in backfill["paginationErrors"])
        for item_id, item in extra.items():
            if item_id not in items_by_id:
                items_by_id[item_id] = item
                origins[item_id] = {"discoveryReason": "catalogue_backfill", "baselineDiscovery": True}
    items = list(items_by_id.values())
    if source["type"] == "woocommerce":
        # Store API dates are optional. Join the public WordPress dates by exact
        # ID, in bounded batches; a missing enrichment never discards an offer.
        missing_dates = [str(item["id"]) for item in items if not item.get("date_created_gmt")]
        for start in range(0, len(missing_dates), 100):
            try:
                query = urllib.parse.urlencode({"per_page": 100, "include": ",".join(missing_dates[start:start + 100]), "_fields": "id,date_gmt"})
                date_url = source.get("feedBase", source["url"]).rstrip("/") + "/wp-json/wp/v2/product?" + query
                dates = fetcher.json(date_url)
                if isinstance(dates, list):
                    date_map = {str(v["id"]): v.get("date_gmt") for v in dates if isinstance(v, dict) and "id" in v}
                    for item in items:
                        if not item.get("date_created_gmt") and date_map.get(str(item["id"])):
                            item["date_created_gmt"] = date_map[str(item["id"])]
            except (OSError, ValueError, RuntimeError, TypeError):
                break  # Do not repeat an unsupported optional endpoint.
    if source["type"] == "shopify":
        # A configured currency is verified by the public storefront metadata
        # when available; no currency is inferred from the source's geography.
        try:
            meta = fetcher.json(source.get("feedBase", source["url"]).rstrip("/") + "/meta.json")
            if isinstance(meta, dict) and re.fullmatch(r"[A-Z]{3}", str(meta.get("currency", ""))):
                source["currency"] = meta["currency"]
        except (OSError, ValueError, RuntimeError):
            pass
    normalized = []
    for item in items:
        current = normalize(source, item)
        if current:
            current.update(origins[str(item["id"])])
            normalized.append(current)
    report["acceptedCount"] = len(normalized)
    report["rejectedCount"] = len(items) - len(normalized)
    if not normalized:
        raise ValueError("Feed parsed but contained no relevant releases; previous data preserved")
    seen = {p["id"] for p in normalized}
    missing = sorted([p for p in old_products if p["id"] not in seen], key=lambda p: p.get("lastCheckedAt", ""))
    warnings = []
    # Shopify's single-product .json is metadata-only on these public shops.
    # Do not repeatedly request it or advance a stock-check timestamp from it.
    skipped_rechecks = len(missing) if source["type"] == "shopify" else 0
    for prior in ([] if source["type"] == "shopify" else missing[:RECHECK_LIMIT]):
        try:
            item = fetcher.json(item_url(source, prior))
            current = normalize(source, item)
            if current:
                normalized.append(current)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            # 404/unlisted is not evidence of a sale or zero stock.
            warnings.append(prior["id"] + ": " + type(exc).__name__)
    report["skippedRechecks"] = skipped_rechecks
    return normalized, len(items), warnings, report


def merge_products(previous, observations, at, baseline=False):
    result = {p["id"]: copy.deepcopy(p) for p in previous}
    for observed in observations:
        old = result.get(observed["id"])
        current = copy.deepcopy(observed)
        if old and not current.get("publishedAt"):
            current["publishedAt"] = old.get("publishedAt")
        if old and not current.get("maker") and old.get("maker"):
            current["maker"] = old["maker"]
            current["makerEvidence"] = old.get("makerEvidence")
        # Discovery describes the first import, not whichever feed refreshed
        # this offer today. Preserve historical provenance and alert decisions.
        if old:
            for key in ["discoveryReason", "discoveryFeedId", "baselineDiscovery"]:
                if key in old:
                    current[key] = old[key]
                else:
                    current.pop(key, None)
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
            current["change"] = None if baseline or current.get("baselineDiscovery") else {"type": "new", "at": at}
        result[current["id"]] = current
    return list(result.values())


def collect(config, previous, at):
    # Remove only explicit accessory/merchandise mistakes from older imports.
    # Absence from a current feed never removes a legitimate historical release.
    products = [copy.deepcopy(p) for p in previous.get("products", []) if not excluded_title(p.get("title", ""))]
    # Correct earlier mixed-taxonomy form mistakes even when those offers are
    # outside today's windows. The title is retained verbatim; this is a local
    # metadata correction, not a new observation or a stock/price check.
    for product in products:
        metal = explicit_copper_metal(product.get("title", ""))
        evidence = (product.get("specificationEvidence") or {}).get("metal", "")
        structured = re.match(r"Source-reported (?:metal|metal type|composition):\s*(.+)", evidence)
        if metal is not None and (not structured or detect_metal(structured[1]) == metal):
            product["metal"] = metal
            if "colorizedSilver" in product:
                product["colorizedSilver"] = False
            if "isColorizedSilverCoin" in product:
                product["isColorizedSilverCoin"] = False
        category = detect_category(product.get("title", ""), default=None)
        if category and "category" in product:
            product["category"] = category
            if "isColorized" in product or "isColorizedSilverCoin" in product:
                product["isColorizedSilverCoin"] = (category == "Coin" and product.get("metal") == "Silver"
                                                      and product.get("isColorized") is True)
    old_sources = {s["id"]: s for s in previous.get("sources", [])}
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(fetch_source, s, [p for p in products if p["sourceId"] == s["id"]], old_sources.get(s["id"], {})): s for s in config if s["type"] != "directory"}
        for future in concurrent.futures.as_completed(futures):
            source = futures[future]
            try:
                results[source["id"]] = (future.result(), None)
            except Exception as exc:
                results[source["id"]] = (None, str(exc)[:240])
    sources = []
    for source in config:
        old = old_sources.get(source["id"], {})
        prior_products = [p for p in products if p["sourceId"] == source["id"]]
        health = {k: source[k] for k in ["id", "name", "url", "type", "coverage", "kind", "region", "evidenceUrl", "trackingReason"] if k in source}
        health.update({"tracking": "directory" if source["type"] == "directory" else "automatic", "lastSuccess": old.get("lastSuccess"), "firstSuccess": old.get("firstSuccess") or min((p["firstSeenAt"] for p in prior_products), default=old.get("lastSuccess")), "count": len(prior_products), "observedCount": 0})
        if source["type"] == "directory":
            reason = source.get("trackingReason") or "Listed in the source directory; no supported automatic public feed is connected."
            health.update({"status": "directory", "reason": reason, "trackingReason": reason, "lastAttempt": old.get("lastAttempt"), "error": None})
            sources.append(health)
            continue
        result, error = results[source["id"]]
        health.update({"status": "error" if error else "ok", "lastAttempt": at, "error": error, "maxPages": bounded_pages(source), "windowLimit": bounded_pages(source) * page_size(source), "pagesFetched": 0})
        # A failed fresh window must not reset the rotation or a collection's
        # completed baseline. It also must not report stale coverage as fresh.
        for key in ["nextBackfillPage", "lastBackfillCompletedAt", "discoveryFeeds"]:
            if key in old:
                health[key] = copy.deepcopy(old[key])
        health["latestWindowCapped"] = None
        if result:
            observations, fetched, warnings, report = result
            products = merge_products(products, observations, at, baseline=not old.get("lastSuccess"))
            health.update({"firstSuccess": health["firstSuccess"] or at, "lastSuccess": at, "count": sum(p["sourceId"] == source["id"] for p in products), "observedCount": len(observations), "fetchedCount": fetched, "recheckErrors": len(warnings), **report})
            for feed in health.get("discoveryFeeds", []):
                feed["lastAttempt"] = at
                if feed.get("status") == "ok":
                    feed["lastSuccess"] = at
            if report.get("backfillReachedEnd"):
                health["lastBackfillCompletedAt"] = at
            notes = []
            if report["paginationErrors"]:
                health["status"] = "partial"
                health["error"] = " ".join(report["paginationErrors"])
                notes.append("Only part of the configured catalogue window could be refreshed.")
            if warnings:
                notes.append("Some older listings could not be rechecked; their last known values and check dates are retained.")
            if report["skippedRechecks"]:
                notes.append("Older Shopify listings outside the fetched window retain their previous stock-check dates; the individual metadata endpoint is not a stock check.")
            if report.get("latestWindowCapped"):
                notes.append("The current catalogue window filled its page budget; it does not establish complete coverage.")
            if report.get("backfillReachedLimit"):
                notes.append("The rotating scan reached its configured page cap and will restart; this is not proof that the full catalogue was covered.")
            if notes:
                health["note"] = " ".join(notes)
        health["colorizedSilverCoinCount"] = sum(p.get("isColorizedSilverCoin") is True for p in products if p["sourceId"] == source["id"])
        sources.append(health)
    products.sort(key=lambda p: (p.get("publishedAt") or p["firstSeenAt"], p["id"]), reverse=True)
    return {"schemaVersion": 1, "generatedAt": at, "sources": sources, "products": products,
            "methodology": {"firstSeen": "First successful Raven Eye observation, not the original release date.", "publishedAt": "Seller publication timestamp when exposed; not a verified mint launch date.", "status": "Seller feed observation at lastCheckedAt; a source outage or absent listing never implies sold out.", "prices": "Original seller currency; taxes, shipping, quantity tiers and local storefront conversion may differ. No currency conversion.", "maker": "Reported brand/vendor or an explicit maker attribute from the seller's feed; not independent verification of the physical manufacturer.", "coverage": "Automatic JSON feeds refresh bounded primary catalogue windows (at most four pages). Public HTML maker catalogues follow up to eight linked pages and 80 product details; their entries do not establish retail availability. Selected sources also scan focused collections and rotating deeper pages, each with a separate fixed budget. A capped window or scan is not complete catalogue coverage. Older WooCommerce and WordPress listings are rechecked in rotation; Shopify stock checks advance only when a listing reappears in a public collection or catalogue feed. Directory entries have no automatic product monitoring. Missing listings retain historical observations; this is not an exhaustive catalogue of every maker or release.", "baseline": "Initial source and focused-collection imports, plus discoveries only in rotating older catalogue pages, do not create new-release alerts. New means first detected outside those baseline imports; it does not verify a mint release date."}}


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
    tmp.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
    tmp.replace(args.output)
    for source in result["sources"]:
        print(f"{source['name']}: {source['status']}, {source['count']} observed" + (f" ({source['error']})" if source.get("error") else ""))
    print(f"Saved {len(result['products'])} tracked listings to {args.output}")
    # Healthy sources may still update when one provider is down.
    automatic = [s for s in result["sources"] if s.get("tracking") != "directory" and s["type"] != "directory"]
    return 0 if not automatic or any(s["status"] in {"ok", "partial"} for s in automatic) else 1


if __name__ == "__main__":
    sys.exit(main())
