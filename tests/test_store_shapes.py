from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import collect as c


class StoreShapeTests(unittest.TestCase):
    def test_theme_and_element_symbol_do_not_hide_silver_material(self):
        self.assertEqual(c.detect_metal("Gullweig 2 oz Ag 999.9 Cast Bar", "A goddess of living gold"), "Silver")
        self.assertEqual(c.detect_metal("Leprechaun's Pot of Gold 1oz Coloured Coin", "This extraordinary silver coin"), "Silver")
        self.assertEqual(c.detect_metal("Pot of Gold 1 oz .999 Gold Coin", "Gold coin"), "Gold")

    def test_sparse_woo_image_object_keeps_product_and_first_valid_image(self):
        source = {"id": "sce", "name": "SCE Coins", "type": "woocommerce", "url": "https://mint.example"}
        item = {"id": 77808, "name": "Dia de los Muertos Spider 1 oz Silver Coin",
                "permalink": "https://mint.example/product/spider/",
                "images": {"0": {"src": "https://mint.example/reverse.jpg"},
                           "2": {"src": "https://mint.example/blister.jpg"}},
                "is_in_stock": False, "prices": {"price": "9900", "currency_code": "EUR", "currency_minor_unit": 2}}
        p = c.normalize(source, item)
        self.assertEqual(p["sourceProductId"], "77808")
        self.assertEqual(p["image"], "https://mint.example/reverse.jpg")
        self.assertEqual(p["status"], "out_of_stock")
        self.assertEqual(p["price"], 99)
        item["images"] = [None, "invalid", {"src": "javascript:alert(1)"}, {"thumbnail": "https://mint.example/thumb.jpg"}]
        self.assertEqual(c.normalize(source, item)["image"], "https://mint.example/thumb.jpg")


if __name__ == "__main__":
    unittest.main()
