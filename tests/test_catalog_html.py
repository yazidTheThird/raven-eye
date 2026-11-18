import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import collect as c
from catalog_html import catalogue_links, product_item


SOURCE = {"id": "maker", "name": "Example Mint", "type": "html_catalog",
          "url": "https://mint.example", "endpoint": "https://mint.example/coins/",
          "maxPages": 8, "limit": 12, "maxProducts": 80, "catalogOnly": True,
          "catalogueMaker": "Example Mint", "productTypeHint": "Coin"}


def detail(product_id=1861, slug="t-rex", finish="Antiqued"):
    url = "https://mint.example/product/" + slug + "/"
    schema = {"@graph": [
        {"@type": "ItemPage", "url": url, "datePublished": "2026-10-20T13:45:28+01:00"},
        {"@type": "Product", "name": "Tyrannosaurus Rex - Example Mint", "category": "Coins",
         "image": [{"url": "https://mint.example/trex.jpg"}],
         "additionalProperty": [{"name": name, "value": value} for name, value in [
             ("pa_year-of-issue", "2026"), ("pa_metal-of-coin", "Silver"),
             ("pa_weight", "2 oz"), ("pa_mintage", "333 pieces"), ("pa_finish", finish)]],
         "offers": {"price": 0, "availability": "https://schema.org/InStock"}}]}
    return ('<html><head><meta property="og:url" content="' + url + '">'
            '<meta property="product:availability" content="instock">'
            '<script type="application/ld+json">' + json.dumps(schema) + '</script></head>'
            '<body class="single-product postid-' + str(product_id) + '">'
            '<div class="product-description"><h4>Description</h4><div><p>A fine silver collectible.<br/>Complete description after the break.</p></div></div>'
            '<footer>Unrelated gold box</footer></body></html>')


class FakeFetcher:
    pages = {}
    calls = []

    def check_robots(self, url):
        self.calls.append(("robots", url))

    def raw(self, url):
        self.calls.append(("read", url))
        result = self.pages[url]
        if isinstance(result, Exception):
            raise result
        return result


class HTMLCatalogueTests(unittest.TestCase):
    def test_product_details_preserve_stable_identity_explicit_specs_and_publication(self):
        raw = product_item(detail(), "https://mint.example/product/t-rex/", SOURCE)
        p = c.normalize({**SOURCE, "type": "woocommerce"}, raw)
        self.assertEqual(p["id"], "maker:1861")
        self.assertEqual(p["title"], "Tyrannosaurus Rex")
        self.assertEqual((p["metal"], p["weight"], p["year"], p["mintage"]), ("Silver", "2 oz", 2026, 333))
        self.assertEqual(p["publishedAt"], "2026-10-20T12:45:28Z")
        self.assertEqual(p["image"], "https://mint.example/trex.jpg")
        self.assertEqual(p["status"], "unknown")
        self.assertIsNone(p["price"])
        self.assertNotIn("Unrelated gold", p["description"])
        self.assertIn("Complete description after the break", p["description"])
        # A slug change must not recreate the product or reset first seen.
        moved = product_item(detail(slug="renamed"), "https://mint.example/product/renamed/", SOURCE)
        self.assertEqual(moved["id"], raw["id"])

    def test_explicit_uv_finish_is_kept_but_plating_is_not_called_colorization(self):
        for finish, expected in [("UV Printing", True), ("Gold, ruthenium, palladium", False)]:
            raw = product_item(detail(finish=finish), "https://mint.example/product/t-rex/", SOURCE)
            p = c.normalize({**SOURCE, "type": "woocommerce"}, raw)
            self.assertEqual(p["isColorizedSilverCoin"], expected)

    def test_only_linked_same_origin_products_and_pagination_are_followed(self):
        html = ('<link rel="next" href="/coins/page/2/">'
                '<a href="/product/t-rex/">Coin</a><a href="/product/t-rex/#gallery">Image</a>'
                '<a href="https://other.example/product/fake/">Other shop</a>'
                '<a href="/company/">About</a>')
        urls, next_url = catalogue_links(html, SOURCE["endpoint"], SOURCE)
        self.assertEqual(urls, ["https://mint.example/product/t-rex/"])
        self.assertEqual(next_url, "https://mint.example/coins/page/2/")
        with self.assertRaises(ValueError):
            catalogue_links('<link rel="next" href="https://other.example/coins/">', SOURCE["endpoint"], SOURCE)
        self.assertEqual(c.bounded_pages(SOURCE), 8)
        self.assertEqual(c.bounded_pages({**SOURCE, "type": "woocommerce"}), 4)

    def test_partial_catalogue_keeps_history_and_does_not_claim_a_complete_scan(self):
        FakeFetcher.calls = []
        FakeFetcher.pages = {
            SOURCE["endpoint"]: '<link rel="next" href="/coins/page/2/"><a href="/product/t-rex/">Coin</a>',
            "https://mint.example/coins/page/2/": OSError("temporary failure"),
            "https://mint.example/product/t-rex/": detail(),
        }
        prior_item = c.normalize({**SOURCE, "type": "woocommerce"}, product_item(detail(), "https://mint.example/product/t-rex/", SOURCE))
        prior = c.merge_products([], [prior_item], "2026-01-01T00:00:00Z", baseline=True)
        with patch.object(c, "Fetcher", FakeFetcher):
            result = c.collect([SOURCE], {"products": prior}, "2026-11-18T12:00:00Z")
        self.assertEqual(result["sources"][0]["status"], "partial")
        self.assertEqual(result["sources"][0]["pagesFetched"], 1)
        self.assertEqual(result["products"][0]["firstSeenAt"], "2026-01-01T00:00:00Z")
        self.assertIsNone(result["products"][0]["change"])
        for index, (action, url) in enumerate(FakeFetcher.calls):
            if action == "read":
                self.assertEqual(FakeFetcher.calls[index - 1], ("robots", url))

    def test_catalogue_page_cap_and_detail_cap_are_reported(self):
        FakeFetcher.calls = []
        FakeFetcher.pages = {
            SOURCE["endpoint"]: '<link rel="next" href="/coins/page/2/"><a href="/product/t-rex/">One</a><a href="/product/two/">Two</a>',
            "https://mint.example/product/t-rex/": detail(),
        }
        with patch.object(c, "Fetcher", FakeFetcher):
            observations, _, _, report = c.fetch_source({**SOURCE, "maxPages": 1, "maxProducts": 1}, [])
        self.assertEqual(len(observations), 1)
        self.assertTrue(report["latestWindowCapped"])
        self.assertEqual(report["windowLimit"], 1)
        self.assertFalse(any(url.endswith("/two/") for _, url in FakeFetcher.calls))


if __name__ == "__main__":
    unittest.main()
