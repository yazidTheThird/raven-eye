"""Evidence-based finish classification, using metadata before display truncation.

False means no supported evidence was found, not that a product is uncolored.
This module deliberately does not infer finishes from artwork names or images.
"""

import html
import re
from html.parser import HTMLParser


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"p", "div", "li", "br", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif tag in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _text(value):
    parser = _Text()
    parser.feed(str(value or ""))
    return html.unescape("".join(parser.parts)).strip()


def _values(value):
    if isinstance(value, dict):
        return [_text(value.get("name", value.get("value", "")))]
    if isinstance(value, (list, tuple)):
        return [part for item in value for part in _values(item)]
    return [_text(value)]


def _attributes(attributes):
    if isinstance(attributes, dict):
        return [(str(key), " ".join(_values(value))) for key, value in attributes.items()]
    if isinstance(attributes, (list, tuple)):
        return [(_text(item.get("name", "")), " ".join(_values(
            item.get("terms", item.get("options", item.get("value", ""))))))
            for item in attributes if isinstance(item, dict)]
    return []


_COLOR = re.compile(
    r"\b(?:colou?ri[sz](?:ed|ation|ations|ing)|colou?ration|colou?red|colou?ring|"
    r"colou?r[ -](?:applied|application|printing|printed|print|finish|enamel)|"
    r"(?:full|multi|selective)[ -]colou?r|"
    r"uv[ -]colou?r|printed[ -](?:coins?|rounds?)|"
    r"(?:uv|hd|high[ -]definition|digital|pad|gicl[ée]e)[ -](?:print(?:ed|ing)?)|"
    r"enamel(?:led|ed)?|koloriert|coloriert|kolorowan[ay]e?|koloryzowan[ay]e?|"
    r"color[ée]e?s?|colorid[oa]s?|colorat[oa])\b", re.I)
_PACKAGING = re.compile(
    r"\b(?:box|boxes|packaging|package|case|capsule|certificate|coa|booklet|"
    r"brochure|cardboard|display stand|presentation folder)\b", re.I)
_PRODUCT = re.compile(
    r"\b(?:coin|round|bar|reverse|obverse|surface|finish|design|edition|issue|piece|"
    r"medal|medallion|figurine|statue|sculpture|foil|poster)\b", re.I)
_NEGATION = re.compile(
    r"\b(?:not|never|without|non|unlike|no)\b[^.;:!?]{0,35}$", re.I)
_GENERIC = re.compile(
    r"\b(?:historically|traditionally|some coins|many coins|other coins|"
    r"we (?:sell|offer|stock)|our (?:range|selection|collection) of|"
    r"explore (?:our|the)|browse (?:our|the))\b", re.I)
_BASE_PLATED = re.compile(
    r"\b(?:silver|gold)[ -](?:plated|coated)\s+(?:\w+\s+){0,2}"
    r"(?:copper|brass|bronze|steel|base[ -]metal)\b|"
    r"\b(?:copper|brass|bronze|steel|base[ -]metal)\b[^.;\n]{0,40}"
    r"\b(?:silver|gold)[ -](?:plated|plating|coated)\b", re.I)
_COIN = re.compile(r"\b(?:coin|round|münze|munze|moneta|monnaie)\b", re.I)
_NON_COIN = re.compile(
    r"\b(?:bars?|ingots?|foils?|posters?|statues?|sculptures?|figurines?|"
    r"medals?|medallions?|pendants?|banknotes?|coincards?)\b", re.I)
_DEDICATED_TYPE = re.compile(
    r"(?:(?:silver|gold|platinum|palladium|copper)\s+)?"
    r"(?:colou?ri[sz]ed|colou?red|printed|uv\s+colou?r|enamel(?:led|ed)?)\s+"
    r"(?:(?:silver|gold|platinum|palladium|copper)\s+)?"
    r"(?:coins?|rounds?|bars?|foils?|collectibles?)", re.I)


def _evidence(label, value, structured=False):
    """Find one local positive assertion, excluding packaging and generic copy."""
    for sentence in re.split(r"[\n.!?;]+", value):
        if not sentence.strip() or _GENERIC.search(sentence):
            continue
        matches = list(_COLOR.finditer(sentence))
        if structured and re.fullmatch(r"\s*(?:colou?r|yes|true)\s*", sentence, re.I):
            matches = [re.search(r"\S+", sentence)]
        for match in matches:
            before, after = sentence[:match.start()], sentence[match.end():]
            if _NEGATION.search(before) or re.match(r"\s*[:=–-]?\s*(?:no|false|none)\b", after, re.I):
                continue
            # Color usually modifies the next noun: a colored box is not a colored coin.
            package = _PACKAGING.search(after)
            product = _PRODUCT.search(after)
            if (package and package.start() < 45
                    and not re.search(r"\b(?:and|with|comes|supplied|delivered|housed|presented)\b", after[:package.start()], re.I)
                    and (not product or package.start() < product.start())):
                continue
            # Also handle "box is colored", without excluding "coin ... with a box".
            preceding_package = list(_PACKAGING.finditer(before))
            preceding_product = list(_PRODUCT.finditer(before))
            if preceding_package and len(before) - preceding_package[-1].end() < 30:
                if not preceding_product or preceding_package[-1].end() > preceding_product[-1].end():
                    continue
            excerpt = re.sub(r"\s+", " ", sentence.strip())
            if len(excerpt) > 170:
                start = max(0, match.start() - 55)
                excerpt = ("…" if start else "") + re.sub(r"\s+", " ", sentence[start:match.end() + 85]).strip() + "…"
            return f"{label}: {excerpt}"
    return None


def classify_product(title, product_type, description, tags, attributes=None, metal=None, category=None):
    """Return conservative color/metal/coin flags and the supporting source text.

    Call with full descriptions and tags, after resolving metal/category specs.
    Explicit physical bars/foils/statues stay outside the strict coin focus.
    """
    title, product_type, description = map(_text, (title, product_type, description))
    tag_values = _values(tags.split(",") if isinstance(tags, str) else tags)
    attrs = _attributes(attributes)
    candidates = [("Title", title, False)]
    # Joined retailer taxonomies can include Colorized Coins for plain antiqued
    # or gilded items. Accept a dedicated type, but require direct evidence when
    # the input combines multiple departments, years, themes, and finishes.
    if _DEDICATED_TYPE.fullmatch(product_type.replace("-", " ").strip()) or _COLOR.fullmatch(product_type.strip()):
        candidates.append(("Product type", product_type, False))
    for name, value in attrs:
        if re.search(r"finish|finishing|technique|feature|color|colour|colouring|colorization|veredelung", name, re.I):
            # A colorization yes/no field is useful; arbitrary "Finish: yes" is not.
            structured = bool(re.search(r"color|colour", name, re.I)) or bool(re.fullmatch(r"colou?r", value, re.I))
            candidates.append((f"{name} attribute", value, structured))
    candidates.extend(("Tag", tag, bool(re.fullmatch(r"colou?r", tag, re.I))) for tag in tag_values)
    candidates.append(("Description", description, False))
    evidence = next((found for label, value, structured in candidates
                     if (found := _evidence(label, value, structured))), None)
    all_text = "\n".join([title, product_type, description, *tag_values,
                          *(f"{name}: {value}" for name, value in attrs)])
    silver = str(metal).lower() == "silver" if metal else bool(re.search(
        r"\b(?:silver|silber|argent|argento|prata)\b", all_text, re.I))
    silver = silver and not bool(_BASE_PLATED.search(all_text))
    primary = " ".join([title, product_type, *tag_values])
    title_coin = bool(_COIN.search(title))
    title_other_form = bool(_NON_COIN.search(title))
    # Retailers place coins under broad "Silver rounds and bars" taxonomies.
    # An explicit title form is more precise, including a title's coin-in-bar.
    coin = title_coin or str(category).lower() == "coin"
    if not coin and (not category or str(category).lower() == "collectible"):
        body_coin = re.search(
            r"\b(?:this|the|a|an|silver|silber|argent|argento|prata)\s+"
            r"(?:[\w.-]+\s+){0,3}(?:coin|round|münze)\b", description, re.I)
        body_other_form = re.search(
            r"\b(?:silver|silber|argent|argento|prata)\s+(?:coin\s+)?"
            r"(?:bars?|ingots?|foils?|posters?|statues?|sculptures?|figurines?|medallions?)\b", description, re.I)
        coin = bool(_COIN.search(primary) or (body_coin and not body_other_form))
    if title_other_form or (not title_coin and
                            (_NON_COIN.search(primary) or str(category).lower() == "bar")):
        coin = False
    colorized = evidence is not None
    return {
        "isColorized": colorized,
        "colorizedEvidence": evidence,
        "finish": "Colorized" if colorized else None,
        "colorizedSilver": colorized and silver,
        "isColorizedSilverCoin": colorized and silver and coin,
    }
