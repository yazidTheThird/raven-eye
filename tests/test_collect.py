import importlib.util
import io
import json
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
    def test_target_collection_deduplicates_and_baselines_only_extra_products(self):
        endpoint = "https://example.org/collections/coloured/products.json?limit=2"
        config = source(discoveryFeeds=[{"id": "colour", "label": "Coloured coins", "endpoint": endpoint, "maxPages": 2}])
        def get(url):
            if url.endswith("/meta.json"):
                return {"currency": "USD"}
            if "collections" in url:
                ids = [3] if "page=2" in url else [2, 3]
            else:
                ids = [1, 2]
            return {"products": [feed_item("shopify", i) for i in ids]}
        with patch.object(c.Fetcher, "json", side_effect=get):
            items, fetched, _, report = c.fetch_source(config, [])
        self.assertEqual([p["id"] for p in items], ["test:1", "test:2", "test:3"])
        self.assertEqual(fetched, 3)
        self.assertFalse(items[1]["baselineDiscovery"])
        self.assertTrue(items[2]["baselineDiscovery"])
        self.assertEqual(items[2]["discoveryFeedId"], "colour")
        self.assertTrue(report["latestWindowCapped"])
        # A repeated page is not misrepresented as an exhausted catalogue.
        self.assertEqual(report["discoveryFeeds"][0]["status"], "partial")

    def test_completed_discovery_baseline_allows_later_new_events(self):
        endpoint = "https://example.org/collections/coloured/products.json?limit=2"
        config = source(discoveryFeeds=[{"id": "colour", "endpoint": endpoint}])
        previous_health = {"discoveryFeeds": [{"id": "colour", "endpoint": endpoint, "baselineComplete": True, "maxPages": 1}]}
        def get(url):
            if url.endswith("/meta.json"):
                return {"currency": "USD"}
            return {"products": [feed_item("shopify", 2 if "collections" in url else 1)]}
        with patch.object(c.Fetcher, "json", side_effect=get):
            items, _, _, _ = c.fetch_source(config, [], previous_health)
        self.assertFalse(items[1]["baselineDiscovery"])
        self.assertEqual(c.merge_products([], items, "now")[1]["change"]["type"], "new")

    def test_rotating_backfill_resumes_failed_page_and_preserves_old_import_history(self):
        config = source(catalogueBackfill={"pagesPerRun": 3, "maxPage": 100})
        calls = []
        def get(url):
            calls.append(url)
            if url.endswith("/meta.json"):
                return {"currency": "USD"}
            page = int(dict(c.urllib.parse.parse_qsl(c.urllib.parse.urlsplit(url).query)).get("page", 1))
            if page == 4:
                raise TimeoutError("try this page next time")
            return {"products": [feed_item("shopify", page * 2 - 1), feed_item("shopify", page * 2)]}
        with patch.object(c.Fetcher, "json", side_effect=get):
            items, _, _, report = c.fetch_source(config, [], {"nextBackfillPage": 3})
        self.assertEqual(report["nextBackfillPage"], 4)
        self.assertEqual(report["backfillPagesFetched"], 1)
        self.assertFalse(any("page=2" in url or "page=5" in url for url in calls))
        self.assertTrue(all(p["baselineDiscovery"] for p in items if p["id"] in {"test:5", "test:6"}))
        merged = c.merge_products([], items, "now")
        self.assertIsNone(next(p for p in merged if p["id"] == "test:5")["change"])
        self.assertEqual(next(p for p in merged if p["id"] == "test:5")["firstSeenAt"], "now")
        self.assertEqual(next(p for p in merged if p["id"] == "test:1")["change"]["type"], "new")

    def test_backfill_end_wraps_but_page_cap_is_not_exhaustion(self):
        for short, maximum in [(True, 100), (False, 2)]:
            with self.subTest(short=short):
                def get(url):
                    if url.endswith("/meta.json"):
                        return {"currency": "USD"}
                    ids = ([3] if short else [3, 4]) if "page=2" in url else [1, 2]
                    return {"products": [feed_item("shopify", i) for i in ids]}
                with patch.object(c.Fetcher, "json", side_effect=get):
                    _, _, _, report = c.fetch_source(source(catalogueBackfill={"pagesPerRun": 4, "maxPage": maximum}), [])
                self.assertEqual(report["nextBackfillPage"], 2)
                self.assertEqual(report["backfillReachedEnd"], short)
                self.assertEqual(report["backfillReachedLimit"], not short)

    def test_only_explicit_wordpress_invalid_page_error_is_exhaustion(self):
        for code in ["rest_post_invalid_page_number", "rest_invalid_param"]:
            with self.subTest(code=code):
                def get(url):
                    if dict(c.urllib.parse.parse_qsl(c.urllib.parse.urlsplit(url).query)).get("page") == "2":
                        raise c.urllib.error.HTTPError(url, 400, "Bad Request", {}, io.BytesIO(json.dumps({"code": code}).encode()))
                    return [feed_item("wordpress", 1), feed_item("wordpress", 2)]
                with patch.object(c.Fetcher, "json", side_effect=get):
                    _, _, _, report = c.fetch_source(source("wordpress", catalogueBackfill={"pagesPerRun": 2}), [])
                self.assertEqual(report["backfillReachedEnd"], code == "rest_post_invalid_page_number")
                self.assertEqual(bool(report["paginationErrors"]), code != "rest_post_invalid_page_number")
                self.assertEqual(report["nextBackfillPage"], 2)

    def test_supplemental_failure_keeps_primary_and_cursor_on_source_failure(self):
        config = source(discoveryFeeds=[{"id": "colour", "endpoint": "https://example.org/collections/colour/products.json"}], catalogueBackfill={"pagesPerRun": 2})
        old = {"products": [], "sources": [{"id": "test", "lastSuccess": "before", "nextBackfillPage": 12}]}
        def get(url):
            if "collections" in url:
                raise TimeoutError("collection unavailable")
            if url.endswith("/meta.json"):
                return {"currency": "USD"}
            return {"products": [feed_item("shopify", 1)]}
        with patch.object(c.Fetcher, "json", side_effect=get):
            result = c.collect([config], old, "now")
        self.assertEqual(result["sources"][0]["status"], "partial")
        self.assertEqual(result["products"][0]["id"], "test:1")
        with patch.object(c, "fetch_source", side_effect=TimeoutError("source unavailable")):
            result = c.collect([config], old, "later")
        self.assertEqual(result["sources"][0]["nextBackfillPage"], 12)
        self.assertIsNone(result["sources"][0]["latestWindowCapped"])

    def test_supplemental_observation_preserves_existing_discovery_and_events(self):
        first = c.merge_products([], [product()], "before")[0]
        first["discoveryReason"] = "latest_window"
        first["baselineDiscovery"] = False
        result = c.merge_products([first], [{**product(), "baselineDiscovery": True, "discoveryReason": "catalogue_backfill"}], "later")[0]
        self.assertEqual(result["firstSeenAt"], "before")
        self.assertEqual(result["change"], first["change"])
        self.assertEqual(result["history"], first["history"])
        self.assertEqual(result["discoveryReason"], "latest_window")
        self.assertFalse(result["baselineDiscovery"])

    def test_short_titles_use_full_product_metadata_without_generic_footer_matches(self):
        item = feed_item("shopify", 1)
        item.update({"title": "KITSUNE", "product_type": "", "body_html": "A legendary fox. " * 30 + "This 2 oz silver coin has a colorized finish."})
        current = c.normalize(source(), item)
        self.assertIsNotNone(current)
        self.assertEqual(current["metal"], "Silver")
        self.assertTrue(current["isColorized"])
        self.assertEqual(current["category"], "Coin")
        self.assertNotIn("colorized", current["description"])
        item.update({"body_html": "A legendary fox.", "tags": ["unrelated"] * 32 + ["Silver Coins", "Colorized"]})
        current = c.normalize(source(), item)
        self.assertEqual(current["metal"], "Silver")
        self.assertTrue(current["isColorized"])
        self.assertFalse(c.is_relevant("Greeting Card", "Stationery", "We sell silver coins, gold coins and collectibles. Subscribe today.", []))
        self.assertFalse(c.is_relevant("Coin Capsules", "", "2 oz silver coin with colorized finish", ["Silver"]))
        item.update({"title": "Silver-plated Brass Coin", "tags": ["Silver Coins", "Colorized"]})
        self.assertEqual(c.normalize(source(), item)["metal"], "Other")

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

    def test_explicit_copper_composition_outranks_names_and_mixed_taxonomy(self):
        titles = [
            "Scottsdale Cowboy with 250th Privy 1 oz Copper Round",
            "1870-S $3 GOLD TRIBUTE – 1 AVDP OUNCE – .999 FINE COPPER – CU",
            "Silver Breaks the $100 Barrier – 5 oz .999 Copper High Relief Medal",
            "Hotel & Casino $50 Poker Chip 1 oz Fine Copper Colorized Round",
            "2025 China ANA Colorized Panda with Silver Plating 1 oz Copper BU",
            "Premium Bull & Bear 1 oz Copper High Relief Silver Enameled Round",
        ]
        for title in titles:
            with self.subTest(title=title):
                item = feed_item("woocommerce", 1)
                item.update({"name": title, "categories": [{"name": "Silver Coins and Gold Collectibles"}]})
                normalized = c.normalize(source("woocommerce"), item)
                self.assertEqual(normalized["metal"], "Copper")
                self.assertFalse(normalized["colorizedSilver"])
                self.assertFalse(normalized["isColorizedSilverCoin"])

    def test_copper_surfaces_names_alloys_and_mixed_objects_are_not_pure_copper(self):
        for title, expected in [
            ("1 oz Copper Plated Silver Coin", "Silver"),
            ("Panda 30g .999 Silver BU Antique Copper Plated", "Silver"),
            ("The Goonies Copper Bones Skeleton Key Silver Coin", "Silver"),
            ("1 oz Copper-Nickel Commemorative Coin", "Other"),
            ("1 oz Copper Coloured Medal", "Other"),
            ("Copper Horizon", "Other"),
            ("1 oz Silver Coin with 14 oz Copper Core", "Other"),
            ("Saint Michael 1 Kilogram Copper with 1 oz Silver Libertad Coin", "Other"),
            ("Saint Michael 1 Kilogram Copper with 2024 1 oz Mexican Silver Libertad Coin", "Other"),
        ]:
            with self.subTest(title=title):
                self.assertEqual(c.detect_metal(title), expected)

    def test_woo_metal_type_attribute_and_title_taxonomy_priority(self):
        self.assertEqual(c.details("KITSUNE", "Copper", "", [])["metal"], "Copper")
        self.assertEqual(c.details("1 oz Silver Coin", "Copper Coins and Silver Coins", "", [])["metal"], "Silver")
        item = feed_item("woocommerce", 1)
        item.update({"name": "Cowboy Round", "attributes": [{"name": "Metal Type", "terms": [{"name": "Copper"}]}]})
        normalized = c.normalize(source("woocommerce"), item)
        self.assertEqual(normalized["metal"], "Copper")
        self.assertEqual(normalized["specificationEvidence"]["metal"], "Source-reported metal type: Copper")
        item = feed_item("wordpress", 2)
        item["title"] = {"rendered": "1 oz .999 Fine Copper Round"}
        item["class_list"].append("product_types-silver")
        self.assertEqual(c.normalize(source("wordpress"), item)["metal"], "Copper")

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

    def test_retained_title_form_correction_preserves_observation_history_and_color_evidence(self):
        prior_product = c.merge_products([], [{**product(), "title": "Spectrum Parrot 1 oz Silver Colorized Proof Coin",
                                               "category": "Bar", "metal": "Silver", "isColorized": True,
                                               "isColorizedSilverCoin": False, "colorizedEvidence": "Title: Colorized"}], "before")[0]
        prior = {"products": [prior_product], "sources": [{"id": "test", "lastSuccess": "before"}]}
        with patch.object(c, "fetch_source", side_effect=TimeoutError("offline")):
            result = c.collect([source()], prior, "later")
        corrected = result["products"][0]
        self.assertEqual(corrected["category"], "Coin")
        self.assertTrue(corrected["isColorizedSilverCoin"])
        for key in ["firstSeenAt", "lastSeenAt", "lastCheckedAt", "publishedAt", "history", "change", "colorizedEvidence"]:
            self.assertEqual(corrected[key], prior_product[key])
        self.assertEqual(prior_product["category"], "Bar")
        self.assertFalse(prior_product["isColorizedSilverCoin"])

    def test_retained_generic_title_does_not_infer_form_from_truncated_description(self):
        prior_product = c.merge_products([], [{**product(), "title": "KITSUNE", "category": "Collectible",
                                               "metal": "Silver", "description": "A silver coin appears here.",
                                               "isColorized": True, "isColorizedSilverCoin": False}], "before")[0]
        with patch.object(c, "fetch_source", side_effect=TimeoutError("offline")):
            result = c.collect([source()], {"products": [prior_product]}, "later")
        self.assertEqual(result["products"][0], prior_product)

    def test_retained_copper_correction_preserves_observations_and_source_composition(self):
        title = "Silver Breaks the $100 Barrier – 5 oz .999 Copper High Relief Medal"
        original = c.merge_products([], [{**product(), "title": title, "metal": "Silver",
                                         "isColorized": True, "colorizedSilver": True,
                                         "isColorizedSilverCoin": True}], "before")[0]
        protected = {**original, "id": "test:2", "specificationEvidence": {"metal": "Source-reported composition: Silver"}}
        generic = {**original, "id": "test:3", "title": "KITSUNE", "description": "Made from .999 fine copper"}
        with patch.object(c, "fetch_source", side_effect=TimeoutError("offline")):
            result = c.collect([source()], {"products": [original, protected, generic]}, "later")
        indexed = {p["id"]: p for p in result["products"]}
        corrected, unchanged, generic_result = [indexed[key] for key in ["test:1", "test:2", "test:3"]]
        self.assertEqual(corrected["metal"], "Copper")
        self.assertFalse(corrected["colorizedSilver"])
        self.assertFalse(corrected["isColorizedSilverCoin"])
        self.assertTrue(corrected["isColorized"])
        for key in ["firstSeenAt", "lastSeenAt", "lastCheckedAt", "publishedAt", "history", "change"]:
            self.assertEqual(corrected[key], original[key])
        self.assertEqual(original["metal"], "Silver")
        self.assertEqual(unchanged, protected)
        self.assertEqual(generic_result, generic)

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

    def test_explicit_coin_and_round_titles_outrank_mixed_shop_categories(self):
        taxonomy = "1 oz Silver Rounds and Bars Silver (Rounds and Bars) Colorized Coins"
        for title in ["2026 Cook Islands Spectrum Parrot 1 oz Silver Colorized Proof Coin",
                      "Rainbow Toucan 1 oz Silver Colorized Round",
                      "Christmas 1 oz Silver Colorized Round"]:
            with self.subTest(title=title):
                item = feed_item("woocommerce", 982389)
                item.update({"name": title, "categories": [{"name": taxonomy}]})
                normalized = c.normalize(source("woocommerce"), item)
                self.assertEqual(normalized["category"], "Coin")
                self.assertTrue(normalized["isColorizedSilverCoin"])
        self.assertEqual(c.detect_category("Colorized Silver Coin-in-Bar", "Silver Coins"), "Bar")
        self.assertEqual(c.detect_category("Silver Coin Sculpture", "Silver Coins"), "Collectible")

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
