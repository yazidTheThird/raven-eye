import importlib.util
import pathlib
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("collect", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "collect.py")
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def product(status="available", price=100, currency="USD"):
    return {"id": "test:1", "sourceId": "test", "title": "Test silver coin", "price": price, "currency": currency, "status": status, "publishedAt": "2020-01-01T00:00:00Z"}


def source(feed_type="shopify", **overrides):
    return {"id": "test", "name": "Test", "url": "https://example.org", "type": feed_type, "endpoint": "https://example.org/products.json?limit=2" if feed_type == "shopify" else "https://example.org/wp-json/products?per_page=2", "coverage": "Bounded public feed", "currency": "USD", **overrides}


def feed_item(kind, item_id):
    if kind == "shopify":
        return {"id": item_id, "title": "Silver coin", "handle": "coin-" + str(item_id), "variants": [{"id": item_id, "available": True, "price": "20"}]}
    if kind == "woocommerce":
        return {"id": item_id, "name": "Silver coin", "permalink": "https://example.org/coin-" + str(item_id), "date_created_gmt": "2026-01-01T00:00:00", "is_in_stock": True, "is_purchasable": True}
    return {"id": item_id, "title": {"rendered": "Silver coin"}, "link": "https://example.org/coin-" + str(item_id), "class_list": ["instock", "purchasable"]}


class CollectorTests(unittest.TestCase):
    def test_directory_never_fetches_or_changes_prior_observations(self):
        first = c.merge_products([], [product()], "a", baseline=True)
        config = source("directory", kind="Colorizer", region="Europe", evidenceUrl="https://example.org/about", trackingReason="No supported public feed")
        with patch.object(c, "Fetcher") as fetcher, patch.object(c, "fetch_source") as fetch:
            result = c.collect([config], {"products": first, "sources": [{"id": "test", "lastSuccess": "a", "lastAttempt": "a"}]}, "b")
        fetcher.assert_not_called()
        fetch.assert_not_called()
        self.assertEqual(result["products"], first)
        health = result["sources"][0]
        self.assertEqual((health["tracking"], health["status"], health["count"], health["observedCount"]), ("directory", "directory", 1, 0))
        self.assertEqual(health["reason"], "No supported public feed")
        self.assertEqual((health["lastAttempt"], health["lastSuccess"]), ("a", "a"))
        self.assertEqual((health["kind"], health["region"]), ("Colorizer", "Europe"))

    def test_bounded_pagination_deduplicates_all_supported_feeds(self):
        for kind in ["shopify", "woocommerce", "wordpress"]:
            with self.subTest(kind=kind):
                config = source(kind, maxPages=2)
                calls = []
                def get(url):
                    calls.append(url)
                    if url.endswith("/meta.json"):
                        return {"currency": "USD"}
                    ids = [2, 3] if dict(c.urllib.parse.parse_qsl(c.urllib.parse.urlsplit(url).query)).get("page") == "2" else [1, 2]
                    items = [feed_item(kind, item_id) for item_id in ids]
                    return {"products": items} if kind == "shopify" else items
                with patch.object(c.Fetcher, "json", side_effect=get):
                    items, fetched, warnings, report = c.fetch_source(config, [])
                self.assertEqual([item["id"] for item in items], ["test:1", "test:2", "test:3"])
                self.assertEqual((fetched, report["pagesFetched"]), (3, 2))
                self.assertEqual(report["paginationErrors"], [])
                self.assertFalse(any("page=3" in url for url in calls))
        self.assertEqual(c.bounded_pages(source(maxPages=500)), 4)
        self.assertEqual(c.bounded_pages(source(maxPages="invalid")), 1)

    def test_failed_later_page_keeps_successful_page_and_prior_history(self):
        first = c.merge_products([], [{**product(), "id": "test:old"}], "a", baseline=True)
        def get(url):
            if "page=2" in url:
                raise TimeoutError("page timed out")
            if url.endswith("/meta.json"):
                return {"currency": "USD"}
            return {"products": [feed_item("shopify", 1), feed_item("shopify", 2)]}
        with patch.object(c.Fetcher, "json", side_effect=get):
            result = c.collect([source(maxPages=2)], {"products": first, "sources": [{"id": "test", "lastSuccess": "a"}]}, "b")
        health = result["sources"][0]
        self.assertEqual((health["status"], health["count"], health["observedCount"]), ("partial", 3, 2))
        self.assertEqual(health["pagesFetched"], 1)
        self.assertEqual(next(p for p in result["products"] if p["id"] == "test:old"), first[0])
        self.assertIn("Page 2", health["error"])

    def test_shopify_old_metadata_endpoints_are_not_retried(self):
        old = c.merge_products([], [{**product(), "id": "test:old"}], "a", baseline=True)
        with patch.object(c.Fetcher, "json", side_effect=[{"products": [feed_item("shopify", 1)]}, {"currency": "USD"}]) as get:
            _, _, _, report = c.fetch_source(source(), old)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(report["skippedRechecks"], 1)

    def test_reported_brand_requires_explicit_source_data(self):
        item = feed_item("shopify", 1)
        self.assertIsNone(c.normalize(source(), item)["maker"])
        item["vendor"] = "Retailer's house brand"
        current = c.normalize(source(), item)
        self.assertEqual(current["maker"], "Retailer's house brand")
        self.assertIn("not independently verified", current["makerEvidence"])
        woo = feed_item("woocommerce", 1)
        woo["attributes"] = [{"name": "Manufacturer", "terms": [{"name": "Example Mint"}]}]
        current = c.normalize(source("woocommerce"), woo)
        self.assertEqual(current["maker"], "Example Mint")
        self.assertIn("manufacturer attribute", current["makerEvidence"])

    def test_woo_short_description_and_explicit_specs_recover_generic_titles(self):
        item = feed_item("woocommerce", 1)
        item.update({"name": "KITSUNE", "description": "A fox from Japanese folklore.", "short_description": "2 oz Silver Coin<br>5 Dollars Niue 2026", "attributes": [{"name": "Metal", "terms": [{"name": "Silver"}]}, {"name": "Weight", "terms": [{"name": "62.2 g (2 oz)"}]}, {"name": "Mintage", "terms": [{"name": "500 pcs"}]}, {"name": "Years", "terms": [{"name": "2026"}]}]})
        current = c.normalize(source("woocommerce"), item)
        self.assertEqual((current["metal"], current["weight"], current["mintage"], current["year"]), ("Silver", "62.2 g", 500, 2026))
        self.assertEqual(current["category"], "Coin")
        self.assertIn("Source-reported", current["specificationEvidence"]["metal"])

    def test_wordpress_taxonomy_embedded_image_and_catalogue_semantics(self):
        item = {"id": 1, "title": {"rendered": "Sylphid"}, "link": "https://example.org/products/sylphid", "class_list": ["product_types-silver", "product_weight-2-oz", "product_year-160", "instock", "purchasable"], "_embedded": {"wp:featuredmedia": [{"source_url": "https://example.org/coin.png"}]}}
        config = source("wordpress", wpRestBase="products", catalogOnly=True)
        current = c.normalize(config, item)
        self.assertEqual((current["metal"], current["weight"], current["status"]), ("Silver", "2 oz", "unknown"))
        self.assertIsNone(current["year"])
        self.assertEqual(current["image"], "https://example.org/coin.png")
        self.assertEqual(c.item_url(config, current), "https://example.org/wp-json/wp/v2/products/1")
        item["link"] = "https://example.org/pl/products/sylphid"
        self.assertIsNone(c.normalize({**config, "excludePathPrefixes": ["/pl/"]}, item))

    def test_explicit_source_tag_overrides_and_localized_materials(self):
        item = feed_item("shopify", 1)
        item.update({"title": "Albarnaz — Faróis de Portugal", "tags": ["METAL Prata 925/1000", "INDISPONIVEL", "ABANDONADO"]})
        current = c.normalize(source(productTypeHint="Coin", statusTags={"INDISPONIVEL": "out_of_stock"}), item)
        self.assertEqual((current["status"], current["metal"]), ("out_of_stock", "Silver"))
        self.assertEqual(c.detect_metal("20g Ouro Proof"), "Gold")
        self.assertEqual(c.detect_metal("1 Unze Silber-Anlagemünze"), "Silver")
        self.assertFalse(c.is_relevant("Ancient Coins 13-Month Wall Calendar", "Calendar", "", []))
        self.assertFalse(c.is_relevant("Pool Allocated Silver Gram", "", "", []))

    def test_wordpress_category_exclusion_filters_only_matching_posts(self):
        config = source("wordpress", wpRestBase="posts", productTypeHint="Coin", catalogOnly=True, excludeCategoryIds=[9])
        news = {**feed_item("wordpress", 1), "categories": [2, 9]}
        release = {**feed_item("wordpress", 2), "categories": [2, 5]}
        self.assertIsNone(c.normalize(config, news))
        self.assertEqual(c.normalize(config, release)["id"], "test:2")

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
        self.assertEqual(c.detect_metal("Four Horsemen 24k Gold Gilded 10 oz Copper Medal"), "Copper")
        self.assertEqual(c.detect_metal("Silver-plated Brass Coin"), "Other")
        self.assertEqual(c.detect_metal("Base Metal Coin with Gold Plated Finish"), "Other")

    def test_merchandise_and_accessories_do_not_hide_physical_releases(self):
        accessories = [
            "Silver Tag Original Hoodie",
            "Lighthouse Round Coin Capsule 21mm (Pack of 10)",
            "Ultra Coin Capsules - Inside Diameter 24mm",
            "Infinity Double Coin Capsule 2 x 1oz with Lens – Set of 10",
            "BULK PRICING ZANICA 5oz Stackable Coin Capsule – Premium PMMA Acrylic",
            "BRAND NEW COMPACT Archie Kidd’s 5th Edition Guide Book of Silver Art Bars & Art Rounds",
        ]
        for title in accessories:
            with self.subTest(title=title):
                self.assertFalse(c.is_relevant(title, "", "", []))
        releases = [
            ("1oz Silver Coin in Capsule", ""),
            ("Blood of Elves Witcher Book 15$ 200g", "Silver Coin"),
            ("Archie Kidd Guide Book Commemorative 1oz, .999 Silver Art Bar", ""),
        ]
        for title, kind in releases:
            with self.subTest(title=title):
                self.assertTrue(c.is_relevant(title, kind, "", []))

    def test_historical_accessory_cleanup_preserves_missing_valid_release(self):
        valid = c.merge_products([], [product()], "a", baseline=True)[0]
        accessory = c.merge_products([], [{**product(), "id": "test:hoodie", "title": "Silver Tag Original Hoodie"}], "a", baseline=True)[0]
        prior = {"products": [valid, accessory], "sources": [{"id": "test", "lastSuccess": "a"}]}
        with patch.object(c, "fetch_source", side_effect=TimeoutError("offline")):
            current = c.collect([source()], prior, "b")
        self.assertEqual(current["products"], [valid])
        self.assertEqual(current["products"][0]["lastCheckedAt"], "a")
        self.assertEqual(len(prior["products"]), 2)

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
