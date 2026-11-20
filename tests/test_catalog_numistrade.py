from pathlib import Path
import sys
import unittest
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import collect
from catalog_numistrade import catalogue_links, product_items, fetch_catalogue


SOURCE = {"id": "numistrade", "name": "Numistrade", "url": "https://numistrade.example",
          "endpoint": "https://numistrade.example/de/neuheiten.html", "catalogOnly": True}
URL = "https://numistrade.example/de/numistrade-muenzen-im-detail/goat.html"


def table(metal="Silber 999/1000", weight="31,10 Gramm (1 Unze)", finish="PP", mintage="1000 Stück", status="Verfügbar"):
    rows = [("Ausgabejahr", "2027"), ("Metall", metal), ("Gewicht", weight), ("Qualität", finish),
            ("Nennwert", "50 Frw."), ("Auflage", mintage), ("Status", status)]
    return '<h4>' + metal + ' ' + finish + '</h4><table>' + ''.join(
        '<tr><td>' + k + ':</td><td><strong>' + v + '</strong></td></tr>' for k, v in rows) + '</table>'


def detail(tables=None):
    return ('<base href="https://numistrade.example/">'
            '<nav>Unrelated gold, silver and colored packaging</nav>'
            '<div class="c-newsFull__body"><p class="o-subline">Rwanda Lunar Ounce</p>'
            '<img src="files/goat.png"><h4>2027: Jahr der Ziege</h4>'
            + (tables if tables is not None else table() + table(metal="Gold 999/1000", weight="2,59 Gramm (1/12 Unze)", finish="BU"))
            + '</div><footer>Unrelated footer coin</footer>')


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def check_robots(self, url):
        self.calls.append(("robots", url))

    def raw(self, url):
        self.calls.append(("read", url))
        value = self.pages[url]
        if isinstance(value, Exception):
            raise value
        return value


class NumistradeCatalogueTests(unittest.TestCase):
    def test_separate_variants_with_material_weight_mintage_no_guessed_stock_or_date(self):
        items = product_items(detail(), URL, SOURCE)
        self.assertEqual(len(items), 2)
        products = [collect.normalize({**SOURCE, "type": "woocommerce"}, x) for x in items]
        self.assertEqual([(p["metal"], p["weight"]) for p in products], [("Silver", "31.10 g"), ("Gold", "2.59 g")])
        for product in products:
            self.assertEqual(product["year"], 2027)
            self.assertEqual(product["mintage"], 1000)
            self.assertEqual(product["status"], "unknown")
            self.assertIsNone(product["publishedAt"])
            self.assertIsNone(product["price"])
            self.assertEqual(product["image"], "https://numistrade.example/files/goat.png")
            self.assertFalse(product["isColorizedSilverCoin"])
            self.assertNotIn("Unrelated", product["description"])

    def test_variant_ids_ignore_order_stock_mintage_but_distinguish_release_pages(self):
        silver, gold = table(), table(metal="Gold 999/1000")
        first = product_items(detail(silver + gold), URL, SOURCE)
        second = product_items(detail(gold + table(mintage="999 Stück", status="Ausverkauft")), URL, SOURCE)
        self.assertEqual({p["id"] for p in first}, {p["id"] for p in second})
        self.assertNotEqual(first[0]["id"], product_items(detail(silver), URL.replace("goat", "dragon"), SOURCE)[0]["id"])
        self.assertEqual(len(product_items(detail(silver + silver), URL, SOURCE)), 1)

    def test_explicit_german_color_printing_is_recognized_but_gilding_is_not(self):
        for finish, expected in [("Reverse Proof Farbdruck", True), ("Reverse Proof Teilvergoldet Rosé", False)]:
            item = product_items(detail(table(finish=finish)), URL, SOURCE)[0]
            product = collect.normalize({**SOURCE, "type": "woocommerce"}, item)
            self.assertEqual(product["isColorizedSilverCoin"], expected)

    def test_only_official_observed_detail_links_respecting_base_and_limit(self):
        html = ('<base href="https://numistrade.example/">'
                '<a href="de/numistrade-muenzen-im-detail/goat.html">One</a>'
                '<a href="de/numistrade-muenzen-im-detail/goat.html#image">Repeat</a>'
                '<a href="https://evil.example/de/numistrade-muenzen-im-detail/goat.html">External</a>'
                '<a href="de/haendleranfragen.html">Enquire</a>')
        self.assertEqual(catalogue_links(html, SOURCE["endpoint"], SOURCE), [URL])
        with self.assertRaises(ValueError):
            catalogue_links(html.replace('href="https://numistrade.example/"', 'href="https://evil.example/"'), SOURCE["endpoint"], SOURCE)
        html += '<a href="de/numistrade-muenzen-im-detail/dragon.html">Two</a>'
        fetcher = FakeFetcher({SOURCE["endpoint"]: html, URL: detail()})
        items, report = fetch_catalogue(fetcher, {**SOURCE, "maxProducts": 1}, 1)
        self.assertEqual(len(items), 2)
        self.assertTrue(report["latestWindowCapped"])
        self.assertEqual(report["productPagesFetched"], 1)
        for index, (operation, url) in enumerate(fetcher.calls):
            if operation == "read":
                self.assertEqual(fetcher.calls[index - 1], ("robots", url))

    def test_denial_stops_further_details_and_keeps_successful_variants(self):
        other = URL.replace("goat", "dragon")
        last = URL.replace("goat", "last")
        html = ''.join('<a href="' + u + '">Coin</a>' for u in [URL, other, last])
        fetcher = FakeFetcher({SOURCE["endpoint"]: html, URL: detail(), other: urllib.error.HTTPError(other, 403, "Forbidden", {}, None)})
        items, report = fetch_catalogue(fetcher, SOURCE, 1)
        self.assertEqual(len(items), 2)
        self.assertEqual(len(report["paginationErrors"]), 1)
        self.assertNotIn(("read", last), fetcher.calls)
        with self.assertRaises(ValueError):
            product_items('<table><tr><td>Metall</td><td>Silber</td></tr></table>', URL, SOURCE)


if __name__ == "__main__":
    unittest.main()
