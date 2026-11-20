import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / 'scripts'))
from catalog_retail import catalogue_links, product_item, fetch_catalogue

BASE = {'url': 'https://mint.example', 'endpoint': 'https://mint.example/new/', 'retailLinkMode': 'bigcommerce'}
URL = 'https://mint.example/coin/'

def schema(offers=None, **extra):
    p = {'@type': 'Product', 'name': '2027 Silver Coin', 'sku': 'test-1', 'image': [{'contentUrl': 'https://img.example/coin.jpg'}], **extra}
    if offers is not None: p['offers'] = offers
    return '<script type="application/ld+json">' + json.dumps(p) + '</script>'

def offer(availability='InStock', **extra):
    return {'@type': 'Offer', 'availability': 'https://schema.org/' + availability, 'price': '123.45', 'priceCurrency': 'EUR', **extra}

class RetailTests(unittest.TestCase):
    def test_observed_cards_only_and_next_bounds(self):
        body = '<a href="/news/">Nav</a><h4 class="card-title"><a href="/coin/">Coin</a><a href="https://other.example/coin/">Off-site</a></h4><link rel="next" href="?page=2">'
        urls, nxt = catalogue_links(body, BASE['endpoint'], BASE)
        self.assertEqual(urls, [URL])
        self.assertEqual(nxt, BASE['endpoint'] + '?page=2')
        with self.assertRaises(ValueError):
            catalogue_links(body.replace('?page=2', 'https://other.example/new/'), BASE['endpoint'], BASE)

    def test_prefix_scoped_to_main(self):
        s = {**BASE, 'retailLinkMode': 'prefix', 'retailContainerId': 'main', 'productPathPrefix': '/products/'}
        b = '<nav><a href="/products/nav/">Nav</a></nav><main id="main"><a href="/products/coin/">Coin</a></main>'
        self.assertEqual(catalogue_links(b, BASE['endpoint'], s)[0], ['https://mint.example/products/coin/'])

    def test_sitemap_ignores_modification_date(self):
        s = {**BASE, 'retailLinkMode': 'sitemap', 'productPathPrefix': '/coin/'}
        b = '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>' + URL + '</loc><lastmod>2026-11-20</lastmod></url></urlset>'
        self.assertEqual(catalogue_links(b, BASE['endpoint'], s), ([URL], None))
        self.assertNotIn('date_created_gmt', product_item(schema(offer()), URL, BASE))

    def test_single_offer_price_and_presale(self):
        item = product_item(schema(offer('PreOrder'), description='Colourised%20silver%20coin'), URL, BASE)
        self.assertEqual((item['_status'], item['_price'], item['_currency']), ('presale', 123.45, 'EUR'))
        self.assertEqual(item['images'][0]['src'], 'https://img.example/coin.jpg')
        self.assertIn('Colourised silver coin', item['description'])

    def test_missing_aggregate_conflicting_and_free_offers(self):
        for offers in [None, {'@type': 'AggregateOffer', 'lowPrice': 5}, [offer(), offer('OutOfStock')], {**offer(), 'availability':['https://schema.org/InStock','https://schema.org/OutOfStock']}]:
            if isinstance(offers, dict) and isinstance(offers.get('availability'), list):
                offers['availability'] = ['https://schema.org/InStock','https://schema.org/OutOfStock']
            self.assertEqual(product_item(schema(offers), URL, BASE)['_status'], 'unknown')
        self.assertIsNone(product_item(schema(offer(price='0')), URL, BASE)['_price'])
        self.assertIsNone(product_item(schema(offer(price=['100', '200'])), URL, BASE)['_price'])
        self.assertIsNone(product_item(schema(offer(priceCurrency=['EUR', 'USD'])), URL, BASE)['_price'])

    def test_conflicting_visible_label_is_unknown(self):
        s = {**BASE, 'stockSignalClasses': ['coming-soon']}
        b = schema(offer()) + '<span class="coming-soon">Coming soon</span>'
        item = product_item(b, URL, s)
        self.assertEqual(item['_status'], 'unknown')
        self.assertIn('conflicts', item['_statusEvidence'])

    def test_scoped_microdata_including_nested_price(self):
        b = '''<div itemtype="https://schema.org/BreadcrumbList" itemscope><span itemprop="name">Nav</span></div>
        <div itemscope itemtype="http://schema.org/Product"><h1 itemprop="name">Silver Kiwi</h1>
        <span itemprop="sku">KIWI</span><a itemprop="image" href="https://img.example/kiwi.jpg"></a>
        <div itemprop="offers" itemscope itemtype="http://schema.org/Offer"><meta itemprop="availability" content="https://schema.org/OutOfStock">
        <meta itemprop="priceCurrency" content="NZD"><div itemscope itemprop="priceSpecification" itemtype="https://schema.org/PriceSpecification"><meta itemprop="price" content="179.95"></div></div>
        <article itemprop="description">Colorized silver <strong>coin</strong>.</article></div>'''
        p = product_item(b, URL, BASE)
        self.assertEqual((p['name'],p['id'],p['_status'],p['_price'],p['_currency']), ('Silver Kiwi','KIWI','out_of_stock',179.95,'NZD'))
        self.assertIn('Colorized silver coin', p['description'])

    def test_configured_product_scope_keeps_brand_and_duplicate_offer_values(self):
        s = {**BASE, 'microdataRootClass': 'product--details'}
        b = '''<span itemprop="name">Navigation</span><div class="product--details">
        <h1 itemprop="name">Panda Tag und Nacht</h1><meta itemprop="image" content="https://img.example/panda.jpg">
        <div itemprop="brand" itemscope itemtype="https://schema.org/Brand"><meta itemprop="name" content="Reported Mint"></div>
        <div itemprop="offers" itemscope itemtype="http://schema.org/Offer"><meta itemprop="price" content="225">
        <meta itemprop="priceCurrency" content="EUR"><span itemprop="availability" href="http://schema.org/PreOrder"></span>
        <link itemprop="availability" href="https://schema.org/PreOrder"></div></div>'''
        p = product_item(b, URL, s)
        self.assertEqual(p['name'], 'Panda Tag und Nacht')
        self.assertEqual(p['brands'], [{'name': 'Reported Mint'}])
        self.assertEqual((p['_status'], p['_price']), ('presale', 225.0))

    def test_unsafe_canonical_and_multiple_products_rejected(self):
        with self.assertRaises(ValueError):
            product_item(schema(offer(), url='https://other.example/coin/'), URL, BASE)
        with self.assertRaises(ValueError):
            product_item(schema(offer()) + schema(offer()), URL, BASE)

    def test_scoped_spec_table_recovers_year_mintage_and_metal(self):
        s = {**BASE, 'specContainerIds': ['product-specs']}
        b = schema(offer()) + '''<div id="product-specs"><table>
            <tr><td>Alloy</td><td>Silver Ag 925</td></tr>
            <tr><th>Date of Issue:</th><td>13 May 2026</td></tr>
            <tr><td>Mintage (Proof)</td><td>30,000</td></tr></table></div>
            <table><tr><td>Mintage</td><td>99</td></tr></table>'''
        attrs = {a['name']: a['terms'][0]['name'] for a in product_item(b, URL, s)['attributes']}
        self.assertEqual(attrs['metal'], 'Silver Ag 925')
        self.assertEqual(attrs['year'], '2026')
        self.assertEqual(attrs['mintage'], '30,000')

    def test_caps_details_and_marks_incomplete_window(self):
        class Fake:
            def check_robots(self, url): pass
            def raw(self, url, accept=None):
                if url == BASE['endpoint']:
                    return '<h4 class="card-title"><a href="/coin/">Coin</a><a href="/coin-2/">Coin2</a></h4><link rel="next" href="?page=2">'
                return schema(offer())
        items, report = fetch_catalogue(Fake(), {**BASE, 'maxProducts': 1}, 4)
        self.assertEqual(len(items), 1)
        self.assertTrue(report['latestWindowCapped'])
        self.assertEqual(report['pagesFetched'], 1)

if __name__ == '__main__': unittest.main()
