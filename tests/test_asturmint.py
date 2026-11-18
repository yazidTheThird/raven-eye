"""Regressions from Asturmint's public catalogue and retail Store API."""
import importlib.util
import pathlib
import unittest


spec = importlib.util.spec_from_file_location(
    "collect", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "collect.py"
)
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


SOURCE = {
    "id": "asturmint", "name": "Asturmint", "url": "https://asturmint.com",
    "type": "woocommerce", "productTypeHint": "Coin",
    "retailPathSuffix": "-store/",
}


def item(product_id, name, slug, *, price="0", in_stock=True, year="2026"):
    # Real public fields: catalogue originals misleadingly have both Woo stock
    # flags set true, even though their pages link onward to retail listings.
    return {
        "id": product_id, "name": name,
        "permalink": "https://asturmint.com/product/" + slug + "/",
        "is_in_stock": in_stock, "is_purchasable": True,
        "prices": {"price": price, "currency_code": "EUR", "currency_minor_unit": 2},
        "attributes": [
            {"name": "Year of issue", "terms": [{"name": year}]},
            {"name": "Metal", "terms": [{"name": "Silver"}]},
            {"name": "Weight", "terms": [{"name": "1 oz"}]},
            {"name": "Mintage", "terms": [{"name": "100 pieces"}]},
            {"name": "Finish", "terms": [{"name": "Colouring"}]},
        ],
    }


class AsturmintTests(unittest.TestCase):
    def test_catalogue_original_keeps_specs_without_false_purchase_availability(self):
        p = c.normalize(SOURCE, item(4674, "Mechanical Frog", "mechanical-frog"))
        self.assertEqual(p["id"], "asturmint:4674")
        self.assertEqual(p["status"], "unknown")
        self.assertIsNone(p["price"])
        self.assertIsNone(p["publishedAt"])
        self.assertEqual((p["year"], p["metal"], p["weight"], p["mintage"]), (2026, "Silver", "1 oz", 100))
        self.assertTrue(p["isColorizedSilverCoin"])

    def test_real_retail_listing_retains_price_and_stock(self):
        p = c.normalize(SOURCE, item(4975, "Lucifer", "lucifer-store", price="59990", year="2021"))
        self.assertEqual((p["status"], p["price"], p["currency"], p["year"]), ("available", 599.9, "EUR", 2021))
        self.assertIsNone(p["publishedAt"])

    def test_sold_out_retail_placeholder_is_still_sold_out(self):
        p = c.normalize(SOURCE, item(4951, "Grim Reaper Samurai 2024", "grim-reaper-samurai-2024-store", in_stock=False, year="2024"))
        self.assertEqual(p["status"], "out_of_stock")
        self.assertIsNone(p["price"])

    def test_catalogue_rule_is_opt_in_and_uses_canonical_path(self):
        original = item(4674, "Mechanical Frog", "mechanical-frog")
        generic = {key: value for key, value in SOURCE.items() if key != "retailPathSuffix"}
        self.assertEqual(c.normalize(generic, original)["status"], "available")
        original["permalink"] += "?related=/product/coin-store/"
        self.assertEqual(c.normalize(SOURCE, original)["status"], "unknown")

    def test_additional_uv_printing_is_color_evidence_but_packaging_is_not(self):
        memory = item(4972, "Memory", "memory-store", price="29990", year="2022")
        memory["attributes"] = [a for a in memory["attributes"] if a["name"] != "Finish"]
        memory["attributes"].append({"name": "Additional", "terms": [{"name": "UV printing"}]})
        p = c.normalize(SOURCE, memory)
        self.assertTrue(p["isColorizedSilverCoin"])
        self.assertIn("Additional attribute", p["colorizedEvidence"])
        memory["attributes"][-1]["terms"] = [{"name": "UV printed presentation box"}]
        self.assertFalse(c.normalize(SOURCE, memory)["isColorizedSilverCoin"])
        memory["attributes"][-1]["terms"] = [{"name": "Gold plated"}]
        self.assertFalse(c.normalize(SOURCE, memory)["isColorizedSilverCoin"])


if __name__ == "__main__":
    unittest.main()
