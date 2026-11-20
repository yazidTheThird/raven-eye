from pathlib import Path
import sys
import unittest
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from catalog_issuers import cbr_rows, cbr_detail, coins_today_items, fetch_catalogue
import collect


BANK = {"id": "central-bank-russia", "name": "Bank of Russia", "url": "https://bank.example",
        "type": "cbr_catalog", "endpoint": "https://bank.example/coins/", "catalogOnly": True,
        "detailLimit": 40, "maxProducts": 80}
TODAY = {"id": "coins-today", "name": "Coins Today", "url": "https://coins.example",
         "type": "coins_today", "endpoint": "https://coins.example/catalog?menu=todayex&ca_id=ALL&page=1&itemcnt=48&sorttype=new"}


def bank_row(identity="5111-0542", date="10.11.2026"):
    return (f'<tr data-tr-link="ShowCoins/?cat_num={identity}">'
            f'<td class="coins-list_item_image"><img src="/images/{identity}r.jpg"></td>'
            '<td class="coins-list_item_face-value">3 rubles</td>'
            '<td class="coins-list_item_name">Nuclear Icebreaker Arktika</td>'
            '<td class="coins-list_item_metall">Silver 925/1000</td>'
            f'<td class="coins-list_item_date">{date}</td></tr>')


def bank_detail(identity="5111-0542"):
    specs = [("Metal, fineness", "Silver 925/1000"), ("Fine metal content not less than, g", "31.10"),
             ("Total weight, g", "33.94 (±0.31)"), ("Mintage, pcs", "3,000")]
    return (f'<input name="UniDbQuery.cat_num" value="{identity}"><div class="commemor-coin_content">' +
            ''.join('<div class="commemor-coin_info_characteristic"><div class="characteristic_denomenation">' + k +
                    '</div><div class="characteristic_value">' + v + '</div></div>' for k, v in specs) +
            '<h2>Reverse</h2><p>A relief coloured image of Nuclear Icebreaker Arktika.</p></div>')


def card(identity="coin1", sold=True, price="", title="2 oz Silver 999 Antique Stackables - Kings of Pirates", external=False):
    url = ('https://evil.example/' if external else '') + '?menu=goods_detail&gp_id=' + identity
    return ('<div class="prd_box"><a href="' + url + '"><img class="prd_box_img" src="/img/coin.jpg">'
            '<div class="prd_box2_name">' + title + '</div><div class="prd_box2_price">' + price + '</div></a>' +
            ('<span class="soldout_badge"><i></i> SOLD OUT</span>' if sold else '<button title="ORDER">ORDER</button>') + '</div>')


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.robots, self.reads = pages, [], []

    def check_robots(self, url):
        self.robots.append(url)

    def raw(self, url):
        self.reads.append(url)
        value = self.pages[url]
        if isinstance(value, Exception):
            raise value
        return value


class IssuerCatalogueTests(unittest.TestCase):
    def test_bank_date_is_release_not_publication_and_annual_ids_stay_distinct(self):
        items = cbr_rows(bank_row("5111-0178-26") + bank_row("5111-0178-25", "14.01.2025"), BANK["endpoint"])
        self.assertNotEqual(items[0]["id"], items[1]["id"])
        self.assertEqual(items[0]["_releaseDate"], "2026-11-10")
        self.assertTrue(items[0]["_metadataPartial"])
        p = collect.normalize({**BANK, "type": "woocommerce"}, items[0])
        self.assertIsNone(p["publishedAt"])
        self.assertEqual(p["status"], "unknown")
        self.assertIsNone(p["price"])
        self.assertEqual(p["metal"], "Silver")

    def test_bank_details_add_explicit_specs_and_colored_reverse(self):
        raw = cbr_detail(bank_detail(), cbr_rows(bank_row(), BANK["endpoint"])[0])
        self.assertFalse(raw["_metadataPartial"])
        p = collect.normalize({**BANK, "type": "woocommerce"}, raw)
        self.assertEqual(p["weight"], "31.10 g")
        self.assertEqual(p["mintage"], 3000)
        self.assertEqual(p["year"], 2026)
        self.assertTrue(p["isColorizedSilverCoin"])
        with self.assertRaisesRegex(ValueError, "identity"):
            cbr_detail(bank_detail("wrong"), raw)

    def test_bank_missing_date_is_not_fabricated_and_foreign_links_are_rejected(self):
        with self.assertRaises(ValueError):
            cbr_rows(bank_row(date="not a date"), BANK["endpoint"])
        with self.assertRaises(ValueError):
            cbr_rows(bank_row().replace('ShowCoins/', 'https://evil.example/ShowCoins/'), BANK["endpoint"])

    def test_bank_base_metal_labels_are_supported_without_guessing_silver(self):
        for label, material in [("Alloy", "Cupro-nickel"), ("Material", "Nickel plated steel")]:
            row = bank_row().replace("Silver 925/1000", material)
            body = bank_detail().replace("Metal, fineness", label).replace("Silver 925/1000", material)
            raw = cbr_detail(body, cbr_rows(row, BANK["endpoint"])[0])
            p = collect.normalize({**BANK, "type": "woocommerce"}, raw)
            self.assertEqual(p["metal"], "Other")
            self.assertFalse(p["isColorizedSilverCoin"])

    def test_today_stock_requires_visible_badge_and_price_requires_visible_amount(self):
        items, more = coins_today_items(card(price="$ 123.45") + card("unknown", sold=False) + card("bad", external=True), TODAY)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["_status"], "out_of_stock")
        self.assertEqual(items[0]["_price"], 123.45)
        self.assertEqual(items[0]["_currency"], "USD")
        self.assertEqual(items[1]["_status"], "unknown")
        self.assertIsNone(items[1]["_price"])
        self.assertEqual(items[0]["permalink"], "https://coins.example/index.php?menu=goods_detail&gp_id=coin1")
        self.assertEqual(more, 1)

    def test_today_pagination_is_bounded_and_every_request_checks_robots(self):
        html = card() + '<a onclick="loadProductList(2,\'ALL\',\'\',\'new\')">2</a>'
        f = FakeFetcher({TODAY["endpoint"]: html})
        items, report = fetch_catalogue(f, TODAY, 1)
        self.assertEqual(len(items), 1)
        self.assertTrue(report["latestWindowCapped"])
        self.assertEqual(f.robots, f.reads)

    def test_bank_detail_budget_keeps_all_rows_and_reports_metadata_limit(self):
        source = {**BANK, "detailLimit": 1}
        url = BANK["endpoint"] + 'ShowCoins/?cat_num=5111-0542'
        f = FakeFetcher({BANK["endpoint"]: bank_row() + bank_row("old", "01.01.2025"), url: bank_detail()})
        items, report = fetch_catalogue(f, source, 1)
        self.assertEqual(len(items), 2)
        self.assertEqual(report["detailPagesFetched"], 1)
        self.assertTrue(report["detailWindowCapped"])
        self.assertFalse(report["latestWindowCapped"])
        self.assertFalse(items[0]["_metadataPartial"])
        self.assertTrue(items[1]["_metadataPartial"])

    def test_bank_detail_failure_retains_valid_catalogue_and_stops_on_denial(self):
        a, b = bank_row(), bank_row("5111-0541", "01.10.2026")
        url = BANK["endpoint"] + 'ShowCoins/?cat_num=5111-0542'
        f = FakeFetcher({BANK["endpoint"]: a + b, url: HTTPError(url, 403, "denied", {}, None)})
        items, report = fetch_catalogue(f, BANK, 1)
        self.assertEqual(len(items), 2)
        self.assertEqual(len(report["paginationErrors"]), 1)
        self.assertEqual(len(f.reads), 2)
        self.assertEqual(f.robots, f.reads)


if __name__ == "__main__":
    unittest.main()
