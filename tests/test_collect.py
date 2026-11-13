import importlib.util
import pathlib
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("collect", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "collect.py")
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def product(status="available", price=100, currency="USD"):
    return {"id": "test:1", "sourceId": "test", "title": "Test silver coin", "price": price, "currency": currency, "status": status, "publishedAt": "2020-01-01T00:00:00Z"}


class CollectorTests(unittest.TestCase):
    def test_first_seen_is_observation_not_publication(self):
        first = c.merge_products([], [product()], "2026-11-13T10:00:00Z", baseline=True)[0]
        self.assertEqual(first["firstSeenAt"], "2026-11-13T10:00:00Z")
        self.assertIsNone(first["change"])
        second = c.merge_products([first], [product()], "2026-11-13T16:00:00Z")[0]
        self.assertEqual(second["firstSeenAt"], first["firstSeenAt"])
        self.assertEqual(len(second["history"]), 1)

    def test_stock_transitions_and_price_history(self):
        items = c.merge_products([], [product()], "a", baseline=True)
        items = c.merge_products(items, [product("out_of_stock")], "b")
        self.assertEqual(items[0]["change"]["type"], "sold_out")
        items = c.merge_products(items, [product("available", 90)], "c")
        self.assertEqual(items[0]["change"]["type"], "restock")
        items = c.merge_products(items, [product("available", 80)], "d")
        self.assertEqual(items[0]["change"]["type"], "price_drop")
        self.assertEqual([h["price"] for h in items[0]["history"]], [100, 100, 90, 80])

    def test_absent_product_is_not_sold_out(self):
        first = c.merge_products([], [product()], "a", baseline=True)
        self.assertEqual(c.merge_products(first, [], "b"), first)

    def test_failed_source_preserves_product_check_time(self):
        first = c.merge_products([], [product()], "a", baseline=True)
        source = {"id": "test", "name": "Test", "url": "https://example.org", "type": "shopify", "coverage": "limited"}
        with patch.object(c, "fetch_source", side_effect=TimeoutError("timed out")):
            result = c.collect([source], {"products": first, "sources": [{"id": "test", "lastSuccess": "a", "count": 1}]}, "b")
        self.assertEqual(result["products"], first)
        self.assertEqual(result["sources"][0]["status"], "error")
        self.assertEqual(result["sources"][0]["lastSuccess"], "a")

    def test_currency_change_is_not_price_drop(self):
        items = c.merge_products([], [product(price=200, currency="CAD")], "a", baseline=True)
        items = c.merge_products(items, [product(price=150, currency="USD")], "b")
        self.assertIsNone(items[0]["change"])
        self.assertEqual(len(items[0]["history"]), 2)

    def test_plating_does_not_change_substrate(self):
        self.assertEqual(c.detect_metal("1 oz Gold Plated Silver Coin"), "Silver")
        self.assertEqual(c.detect_metal("1 oz Silver Plated .999 Fine Copper"), "Copper")
        self.assertEqual(c.detect_metal("20g Gold Bar", "Gold and silver products"), "Gold")
        self.assertEqual(c.detect_metal("Silver & Gold Foil Blind Box"), "Other")

    def test_blind_box_rarity_is_not_whole_edition_mintage(self):
        data = c.details("Scooby-Doo Blind Box", "Collectible Coins", "Rare gold chase with a mintage of just 30 pieces per applicable tier; mintages ranging from 1,800 to 30 pieces", [])
        self.assertIsNone(data["mintage"])

    def test_mintage_brand_followed_by_weight_is_not_edition_size(self):
        data = c.details("Hogwarts Express", "1 oz Silver Coin", "A Legendary Mintage 1 oz 99.9% pure silver Hogwarts Express", [])
        self.assertIsNone(data["mintage"])
        self.assertIsNone(c.details("Coin", "", "Mintage 5 g pure silver", [])["mintage"])
        self.assertEqual(c.details("Coin", "", "Mintage: 5,000", [])["mintage"], 5000)

    def test_special_status_overrides_orderable_boolean(self):
        self.assertEqual(c.shopify_status({"tags": ["coming-soon"], "variants": [{"available": True}]})[0], "upcoming")
        self.assertEqual(c.shopify_status({"tags": ["Label - On Backorder"], "variants": [{"available": True}]})[0], "backorder")
        self.assertEqual(c.shopify_status({"tags": [], "title": "Pre-order silver coin", "variants": [{"available": False}]})[0], "out_of_stock")

    def test_woo_generic_backorder_text_does_not_override_booleans(self):
        item = {"name": "Gold coin", "is_in_stock": True, "is_purchasable": True, "is_on_backorder": False, "stock_availability": {"text": "In Stock / On Backorder"}}
        self.assertEqual(c.woo_status(item)[0], "available")

    def test_wordpress_announcement_not_sold_out(self):
        self.assertEqual(c.wordpress_status({"class_list": ["product_cat-coming-soon", "outofstock"]})[0], "upcoming")
        self.assertEqual(c.wordpress_status({"class_list": []})[0], "unknown")

    def test_shipping_weight_is_never_coin_weight(self):
        source = {"id": "test", "name": "Test", "type": "shopify", "url": "https://example.org", "currency": "USD"}
        item = {"id": 1, "title": "Silver Coin", "handle": "silver-coin", "variants": [{"available": True, "price": "20", "grams": 250}], "images": []}
        self.assertIsNone(c.normalize(source, item)["weight"])

    def test_title_metal_and_category_take_priority(self):
        self.assertEqual(c.details("20g Gold Bar", "Bullion", "Silver coins also available", [])["metal"], "Gold")
        self.assertEqual(c.details("3oz Silver Leopard", "Barre de Luxe", "", [])["category"], "Bar")

    def test_set_weight_and_fractional_weight(self):
        self.assertEqual(c.details("Thor Set 2 x 1 Oz Silver Coin", "", "", [])["weight"], "2 × 1 oz")
        self.assertEqual(c.details("Gold coin 1/1000 Ounce", "", "", [])["weight"], "1/1000 oz")

    def test_publication_date_survives_offer_only_recheck(self):
        first = c.merge_products([], [product()], "2026-11-13T10:00:00Z", baseline=True)
        item = product()
        item["publishedAt"] = None
        second = c.merge_products(first, [item], "2026-11-14T10:00:00Z")
        self.assertEqual(second[0]["publishedAt"], first[0]["publishedAt"])

    def test_price_uses_available_variant(self):
        source = {"id": "test", "name": "Test", "type": "shopify", "url": "https://example.org", "currency": "USD"}
        item = {"id": 1, "title": "Silver Coin", "handle": "silver", "variants": [{"id": 11, "title": "Single", "available": False, "price": "20"}, {"id": 12, "title": "10 Pack", "available": True, "price": "30"}]}
        p = c.normalize(source, item)
        self.assertEqual(p["price"], 30)
        self.assertEqual(p["selectedVariant"], "10 Pack")
        self.assertFalse(p["priceIsFrom"])

    def test_variant_switch_is_not_price_increase(self):
        single = {**product(price=149), "selectedVariantId": "single", "selectedVariant": "Single"}
        pack = {**product(price=1490), "selectedVariantId": "pack", "selectedVariant": "10 Pack"}
        items = c.merge_products([], [single], "a", baseline=True)
        items = c.merge_products(items, [pack], "b")
        self.assertIsNone(items[0]["change"])
        self.assertEqual(len(items[0]["history"]), 2)
        self.assertEqual(items[0]["history"][-1]["selectedVariant"], "10 Pack")


if __name__ == "__main__":
    unittest.main()
