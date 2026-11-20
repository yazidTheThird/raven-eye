from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import collect as c


class CatalogueIntegrationTests(unittest.TestCase):
    def test_german_product_finish_is_distinct_from_packaging_and_negation(self):
        for title, expected in [("Panda Silber mit Farbapplikation", True), ("Panda Silber farbveredelt", True),
                                ("Panda Silber ohne Farbapplikation", False), ("Panda Silber — Farbapplikation Etui", False)]:
            result = c.classify_product(title, "Coin", "", [], metal="Silver", category="Coin")
            self.assertEqual(result["isColorizedSilverCoin"], expected)

    def test_thin_catalogue_updates_price_without_erasing_enriched_metadata(self):
        source = {"id": "mint", "name": "Mint", "url": "https://example.org", "type": "cbr_catalog", "maxProducts": 80}
        raw = {"id": "42", "name": "Silver coin", "permalink": "https://example.org/42", "_metadataPartial": True,
               "_status": "out_of_stock", "_price": 50, "_currency": "EUR"}
        prior = {"id": "mint:42", "description": "Colorized silver coin", "metal": "Silver", "category": "Coin",
                 "mintage": 199, "weight": "2 oz", "isColorized": True, "isColorizedSilverCoin": True}
        adapter = SimpleNamespace(fetch_catalogue=lambda *args: ([raw], {"paginationErrors": [], "detailLimit": 12}))
        with patch.object(c.importlib, "import_module", return_value=adapter):
            products, _, _, report = c.fetch_source(source, [prior])
        self.assertEqual(report["detailLimit"], 12)
        self.assertEqual(products[0]["weight"], "2 oz")
        self.assertTrue(products[0]["isColorizedSilverCoin"])
        self.assertEqual(products[0]["status"], "out_of_stock")
        self.assertEqual(products[0]["price"], 50)

    def test_explicit_catalogue_evidence_and_issuer_date_survive_normalization(self):
        source = {"id": "issuer", "name": "Issuer", "url": "https://example.org", "endpoint": "https://example.org/coins", "type": "cbr_catalog"}
        item = {"id": "42", "name": "Silver coin", "permalink": "https://example.org/coin/42", "_status": "presale",
                "_statusEvidence": "Product card says preorder", "_price": 42.5, "_currency": "EUR", "_releaseDate": "2026-10-01"}
        adapter = SimpleNamespace(fetch_catalogue=lambda *args: ([item], {"paginationErrors": []}))
        with patch.object(c.importlib, "import_module", return_value=adapter):
            products, _, _, _ = c.fetch_source(source, [])
            catalogue, _, _, _ = c.fetch_source({**source, "catalogOnly": True}, [])
        self.assertEqual((products[0]["status"], products[0]["price"], products[0]["currency"]), ("presale", 42.5, "EUR"))
        self.assertEqual(products[0]["statusEvidence"], "Product card says preorder")
        self.assertEqual(products[0]["sourceReleaseDate"], "2026-10-01")
        self.assertIsNone(products[0]["publishedAt"])
        self.assertEqual(catalogue[0]["status"], "unknown")
        self.assertIsNone(catalogue[0]["price"])

    def test_tower_fineness_symbols_and_total_mintage(self):
        for material, expected in [("999.9 Au", "Gold"), ("999 Ag", "Silver"), ("Ag999.9", "Silver"), ("Pt 999.5", "Platinum")]:
            item = {"id": 1, "name": "Liberty Coin", "permalink": "https://example.org/coin", "attributes": [
                {"name": "Metal", "terms": [{"name": material}]}, {"name": "Total Mintage", "terms": [{"name": "199"}]},
                {"name": "Issue Limit", "terms": [{"name": "10"}]}]}
            product = c.normalize({"type": "woocommerce", "id": "tower", "name": "Tower"}, item)
            self.assertEqual(product["metal"], expected)
            self.assertEqual(product["mintage"], 199)
        self.assertEqual(c.detect_metal("Agatha coin"), "Other")

    def test_dealer_collection_membership_does_not_replace_seller_or_baseline(self):
        source = {"id": "dealer", "name": "Dealer", "url": "https://example.org", "type": "shopify", "endpoint": "https://example.org/products.json?limit=2", "maxPages": 1,
                  "discoveryFeeds": [{"id": "mint", "label": "Mint collection", "maker": "Mint of MK", "endpoint": "https://example.org/collections/mint/products.json?limit=2", "maxPages": 1}]}
        def fetch(url):
            if url.endswith('/meta.json'):
                return {"currency": "EUR"}
            ids = [1, 2] if '/collections/' in url else [1]
            return {"products": [{"id": i, "title": "Silver coin", "handle": str(i), "variants": []} for i in ids]}
        with patch.object(c.Fetcher, "json", side_effect=fetch):
            products, _, _, _ = c.fetch_source(source, [])
        self.assertEqual(products[0]["dealerCollections"][0]["maker"], "Mint of MK")
        self.assertEqual(products[0]["sourceName"], "Dealer")
        self.assertIsNone(products[0]["maker"])
        merged = c.merge_products([], products, "2026-11-20T00:00:00Z", baseline=True)
        self.assertTrue(all(p["change"] is None for p in merged))
        later = {k: v for k, v in products[0].items() if k != "dealerCollections"}
        updated = c.merge_products(merged, [later], "2026-11-21T00:00:00Z")
        self.assertEqual(updated[0]["dealerCollections"], merged[0]["dealerCollections"])
        self.assertEqual(updated[0]["firstSeenAt"], merged[0]["firstSeenAt"])


if __name__ == "__main__":
    unittest.main()
