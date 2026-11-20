import sys
from pathlib import Path
import unittest
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import collect
from catalog_krakow import product_item, fetch_catalogue

SOURCE = {"id": "royal-krakow-mint", "name": "Royal Krakow Mint", "url": "https://mint.example",
          "endpoint": "https://mint.example/en/our-coins", "maxProducts": 40}
URL = SOURCE["endpoint"] + "/station-v"


def detail(availability="Last pieces"):
    return '''<body id="product" class="product product-62"><h1>STATION V</h1>
    <div id="thumbs_list"><a class="thumbnail" href="https://mint.example/502-large/coin.jpg"><img src="tiny.jpg"></a></div>
    <div class="pb-right-column"><div class="desc"><p>Silver coin STATION V.</p></div><ul>
    <li><strong>Technology</strong>:<span>color digital printing</span></li>
    <li><strong>Year of issue</strong>:<span>2026</span></li>
    <li><strong>Metal</strong>:<span>Silver</span></li>
    <li><strong>Weight</strong>:<span>1 Oz</span></li>
    <li><strong>Mintage</strong>:<span>100</span></li>
    <li><strong>Availability</strong>:<span>''' + availability + '''</span></li></ul>
    <div class="desc"><div>Order at partner</div><a href="https://dealer.example/coin">Retail order</a></div></div>
    <ul id="similar"><li><strong>Availability</strong><span>Sold out</span></li></ul>
    <script>var id_product=999; var price=0;</script></body>'''


class KrakowTests(unittest.TestCase):
    def test_labelled_specs_and_identity_ignore_partner_and_related_coin(self):
        item = product_item(detail(), URL, SOURCE)
        parsed = collect.normalize({**SOURCE, "type": "woocommerce"}, item)
        self.assertEqual(item["id"], "62")
        self.assertEqual(item["_status"], "available")
        self.assertIn("partner stock is not checked", item["_statusEvidence"])
        self.assertEqual((parsed["metal"], parsed["weight"], parsed["year"], parsed["mintage"]), ("Silver", "1 oz", 2026, 100))
        self.assertTrue(parsed["isColorizedSilverCoin"])
        self.assertEqual(parsed["image"], "https://mint.example/502-large/coin.jpg")
        self.assertIsNone(parsed["publishedAt"])
        self.assertIsNone(parsed["price"])
        self.assertNotIn("Order at partner", item["description"])
        self.assertNotIn("Sold out", item["description"])

    def test_status_is_only_explicit_mint_statement(self):
        for statement, status in [("Sold Out", "out_of_stock"), ("Coming in February", "upcoming"),
                                  ("Ready to order", "available"), ("Contact us", "unknown"), ("", "unknown")]:
            self.assertEqual(product_item(detail(statement), URL, SOURCE)["_status"], status)
        with self.assertRaises(ValueError):
            product_item("<h1>Marketing page</h1>", URL, SOURCE)
        with self.assertRaises(ValueError):
            product_item(detail(), "https://another.example/product", SOURCE)

    def test_observed_pagination_bounds_and_denials_are_reported(self):
        class FakeFetcher:
            def __init__(self): self.calls = []
            def check_robots(self, url): self.calls.append(("robots", url))
            def raw(self, url):
                self.calls.append(("read", url))
                return pages[url] if not isinstance(pages[url], Exception) else self.fail(pages[url])
            def fail(self, error): raise error
        next_page = SOURCE["endpoint"] + "?p=2"
        second = SOURCE["endpoint"] + "/second"
        pages = {SOURCE["endpoint"]: '<link rel="next" href="?p=2"><a href="'+URL+'">Coin</a><a href="https://another.example/en/our-coins/fake">Skip</a>',
                 next_page: '<a href="'+second+'">Second</a>', URL: detail(),
                 second: urllib.error.HTTPError(second,403,"Forbidden",{},None)}
        fetcher = FakeFetcher()
        items, report = fetch_catalogue(fetcher, SOURCE, 4)
        self.assertEqual(len(items),1)
        self.assertEqual(report["pagesFetched"],2)
        self.assertEqual(len(report["paginationErrors"]),1)
        for n, (action,url) in enumerate(fetcher.calls):
            if action == "read": self.assertEqual(fetcher.calls[n-1], ("robots",url))
        fetcher = FakeFetcher()
        _, report = fetch_catalogue(fetcher,{**SOURCE,"maxProducts":1},1)
        self.assertTrue(report["latestWindowCapped"])
        self.assertNotIn(("read",second),fetcher.calls)


if __name__ == "__main__": unittest.main()
