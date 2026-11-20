import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from catalog_prestashop import ProductMarkup, availability, enrich_item, fetch_prestashop, listing_item

SOURCE = {"id": "example", "name": "Example", "url": "https://example.test", "endpoint": "https://example.test/en/new-products", "currency": "EUR", "maxDetails": 0}
PRODUCT = {"id_product": "15096", "name": "BLACK LION 3 Oz Silver Coin", "url": "https://example.test/en/15096-lion.html", "description_short": "3 Oz Silver coin", "price_amount": 599.95, "cover": {"large": {"url": "https://example.test/lion.jpg"}}}


class PrestaShopTests(unittest.TestCase):
    def test_presale_allocation_beats_stock_count_and_schema(self):
        item = listing_item(PRODUCT, SOURCE, ["Soon", "Availability: 109 In Stock"])
        body = '<div id="product-availability">In Stock</div><script type="application/ld+json">' + json.dumps({"@type": "Product", "productID": 15096, "description": "Colour printed silver coin", "offers": {"availability": "https://schema.org/InStock", "price": "599.95", "priceCurrency": "EUR"}}) + '</script>'
        enrich_item(item, body, SOURCE)
        self.assertEqual(item["_status"], "presale")

    def test_visible_preorder_overrides_instock_json_ld(self):
        item = listing_item(PRODUCT, SOURCE)
        body = '<div id="product-availability"><span>In pre-order</span></div><div>FAQ: Out of stock and available orders</div><script type="application/ld+json">' + json.dumps({"@type": "Product", "productID": 15096, "offers": {"availability": "https://schema.org/InStock"}, "additionalProperty": [{"name": "Metal", "value": "Silver"}, {"name": "Mintage (pcs)", "value": 499}]}) + '</script>'
        enrich_item(item, body, SOURCE)
        self.assertEqual(item["_status"], "presale")
        self.assertEqual(item["attributes"][1]["name"], "mintage")
        self.assertFalse(item["_metadataPartial"])
        self.assertIn("Metal: Silver", item["description"])

    def test_absent_status_never_implies_available(self):
        item = listing_item({**PRODUCT, "add_to_cart_url": "https://example.test/cart?add=1", "active": 1}, SOURCE)
        self.assertEqual(item["_status"], "unknown")
        self.assertTrue(item["_metadataPartial"])
        self.assertEqual(availability(["To order"])[0], "unknown")
        self.assertEqual(availability(["Soon"])[0], "upcoming")
        self.assertEqual(availability(["Out of stock"])[0], "out_of_stock")

    def test_labels_are_scoped_to_matching_product(self):
        p = ProductMarkup()
        p.feed('<p>In stock</p><article data-id-product="1"><div class="flag-soon flag">Soon</div><div class="availability-list">23 In Stock</div></article><article data-id-product="2"><span class="niente">Out of Stock</span></article><article data-id-product="3"><p>Story about pre-order coin</p></article>')
        self.assertEqual(availability(p.labels["1"])[0], "presale")
        self.assertEqual(availability(p.labels["2"])[0], "out_of_stock")
        self.assertEqual(availability(p.labels["3"])[0], "unknown")

    def test_untagged_out_of_stock_in_price_block(self):
        p = ProductMarkup()
        p.feed('<article data-id-product="15050"><div class="product-price-and-shipping"><span class="price">€59.95</span><!-- E-out -->Out of Stock<div>Coming on 30-Nov-26</div></div><div class="product-desc">This coin is a beautiful available collector edition.</div></article>')
        self.assertEqual(availability(p.labels["15050"])[0], "out_of_stock")

    def test_foreign_product_and_mismatched_detail_rejected(self):
        with self.assertRaises(ValueError):
            listing_item({**PRODUCT, "url": "https://foreign.test/coin"}, SOURCE)
        with self.assertRaises(ValueError):
            enrich_item(listing_item(PRODUCT, SOURCE), '<script type="application/ld+json">{"@type":"Product","productID":999}</script>', SOURCE)

    def test_repeated_or_external_pagination_does_not_lose_products(self):
        class Fake:
            def json(self, url):
                return {"products": [PRODUCT], "pagination": {"pages": [{"type": "next", "clickable": True, "url": "https://foreign.test/products"}]}}
        items, report = fetch_prestashop(Fake(), SOURCE, 4)
        self.assertEqual(len(items), 1)
        self.assertTrue(report["paginationErrors"])
        self.assertIn("left", report["paginationErrors"][0])

    def test_normal_pagination_is_bounded_and_explicit(self):
        class Fake:
            def __init__(self): self.urls = []
            def json(self, url):
                self.urls.append(url)
                page = len(self.urls)
                return {"products": [{**PRODUCT, "id_product": str(page)}], "pagination": {"pages": {"next": {"type": "next", "clickable": True, "url": SOURCE["endpoint"] + '?page=' + str(page + 1)}}}}
        f = Fake()
        items, report = fetch_prestashop(f, SOURCE, 999)
        self.assertEqual(len(items), 4)
        self.assertEqual(len(f.urls), 4)
        self.assertTrue(report["latestWindowCapped"])

    def test_price_does_not_accept_nonfinite_or_zero(self):
        for bad in ["NaN", "Infinity", 0, -1, "not a price"]:
            self.assertIsNone(listing_item({**PRODUCT, "price_amount": bad}, SOURCE)["_price"])


if __name__ == "__main__": unittest.main()
