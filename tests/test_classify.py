import importlib.util
import pathlib
import unittest


spec = importlib.util.spec_from_file_location("classify", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "classify.py")
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def classify(title="Dragon 1oz Silver Coin", description="", **kwargs):
    return c.classify_product(title, kwargs.pop("product_type", ""), description,
                              kwargs.pop("tags", []), metal=kwargs.pop("metal", "Silver"),
                              category=kwargs.pop("category", "Coin"), **kwargs)


class ColorClassificationTests(unittest.TestCase):
    def test_spelling_variants_and_translation(self):
        for finish in ["Colorized", "Colourized", "Colorised", "Colourised", "Colored",
                       "Coloured", "Colouring", "UV printed", "color printing", "koloriert", "kolorowany"]:
            with self.subTest(finish=finish):
                result = classify(title=f"{finish} 1oz Silver Coin")
                self.assertTrue(result["isColorizedSilverCoin"])
                self.assertEqual(result["finish"], "Colorized")
                self.assertTrue(result["colorizedEvidence"].startswith("Title:"))

    def test_full_late_description_is_evidence(self):
        result = classify(description="A legendary creature. " * 80 + "The reverse of this coin is selectively colorized.")
        self.assertTrue(result["isColorizedSilverCoin"])
        self.assertIn("selectively colorized", result["colorizedEvidence"])
        self.assertLess(len(result["colorizedEvidence"]), 210)

    def test_late_tag_is_not_truncated(self):
        result = classify(tags=[f"series {i}" for i in range(45)] + [{"name": "Colorized"}])
        self.assertEqual(result["colorizedEvidence"], "Tag: Colorized")

    def test_structured_finish_and_boolean_attributes(self):
        for attrs in [{"Finish": "Colorized"}, {"Colorization": "Yes"},
                      [{"name": "Finish", "terms": [{"name": "Antique", "slug": "antique"}, {"name": "Colouring"}]}],
                      [{"name": "Special features", "options": ["UV printed", "High relief"]}]]:
            with self.subTest(attrs=attrs):
                self.assertTrue(classify(attributes=attrs)["isColorized"])
        self.assertFalse(classify(attributes={"Finish": "Yes"})["isColorized"])
        self.assertFalse(classify(attributes={"Colorization": "No"})["isColorized"])

    def test_negative_or_unsupported_claims_stay_unconfirmed(self):
        for description in ["This coin is not colorized.", "Uncolored silver coin.",
                            "Without any colorized finish.", "Colorized: no", "Color may vary by screen.",
                            "An art coin with gold plating and high relief.", "A silver sculpture.",
                            "Historically, coins were traditionally colorized by hand.",
                            "Unlike colorized coins, this has a plain proof finish.",
                            "We sell colorized coins and bars.", "Explore our colorized coins."]:
            with self.subTest(description=description):
                result = classify(description=description)
                self.assertFalse(result["isColorized"])
                self.assertIsNone(result["colorizedEvidence"])

    def test_packaging_does_not_establish_colorization(self):
        for description in ["Silver coin in a colored box.", "A silver coin with a full-color booklet.",
                            "The presentation box is enamelled.", "The certificate is digitally printed.",
                            "Includes UV printed COA."]:
            with self.subTest(description=description):
                self.assertFalse(classify(description=description)["isColorized"])
        self.assertFalse(classify(title="Silver Coin in Colored Box")["isColorized"])

    def test_colored_coin_with_plain_box_is_positive(self):
        result = classify(description="The coin is coloured and comes in a quadrum capsule. The box is not colored.")
        self.assertTrue(result["isColorizedSilverCoin"])

    def test_colorized_base_metal_is_not_silver(self):
        for title, metal in [("Colorized silver plated copper coin", "Silver"),
                             ("Colorized copper coin with silver plating", "Silver"),
                             ("Colorized silver-coated brass coin", "Silver"),
                             ("Colorized copper round", "Copper"),
                             ("Colorized gold coin", "Gold")]:
            with self.subTest(title=title):
                result = classify(title=title, metal=metal)
                self.assertTrue(result["isColorized"])
                self.assertFalse(result["colorizedSilver"])
                self.assertFalse(result["isColorizedSilverCoin"])

    def test_gilded_silver_keeps_silver_base(self):
        self.assertTrue(classify(title="Colorized gold plated .999 silver coin")["isColorizedSilverCoin"])

    def test_coin_focus_excludes_bars_and_collectible_forms(self):
        for title, category in [("Colorized silver bar", "Bar"), ("Colorized silver coin bar", "Coin"),
                                ("Colorized silver foil", "Collectible"), ("Colorized silver coin statue", "Coin")]:
            with self.subTest(title=title):
                result = classify(title=title, category=category)
                self.assertTrue(result["colorizedSilver"])
                self.assertFalse(result["isColorizedSilverCoin"])

    def test_generic_title_recovers_coin_from_full_description(self):
        # Observed PiK wording: generic titles often lack the word coin.
        result = classify(title="Halving – Crypto-Themed – American Eagle 1$ 1oz USA 2026",
                          description="This 1 oz silver coin named Halving is one of the new series, inspired by the world of cryptocurrency. The coin is coloured and comes in a quadrum capsule.",
                          category="Collectible")
        self.assertTrue(result["isColorizedSilverCoin"])

    def test_generic_title_keeps_explicit_body_form(self):
        result = classify(title="Girl with a Pearl Earring 5$ 0.5oz Liberia",
                          description="We present a coin dedicated to a famous painting. The coin is coloured.",
                          category="Collectible")
        self.assertTrue(result["isColorizedSilverCoin"])
        result = classify(title="Great White Apex of the Deep",
                          description="This .999 silver coin statue shows a shark. The coin has enamel waves.",
                          category="Collectible")
        self.assertTrue(result["colorizedSilver"])
        self.assertFalse(result["isColorizedSilverCoin"])

    def test_explicit_coin_form_outranks_mixed_retailer_taxonomy(self):
        for title in ["2026 Cook Islands Spectrum Parrot 1 oz Silver Colorized Proof Coin",
                      "Scottsdale Christmas 1 oz Colorized Silver Round"]:
            with self.subTest(title=title):
                result = classify(title=title, product_type="1 oz Silver Rounds and Bars Colorized Coins Silver (Rounds and Bars)", category="Bar")
                self.assertTrue(result["isColorizedSilverCoin"])
        result = classify(title="Colorized American Silver Eagle Coin in Bar",
                          product_type="Silver Coins", category="Coin")
        self.assertFalse(result["isColorizedSilverCoin"])

    def test_uv_color_title_and_printed_coin_type_are_explicit(self):
        result = classify(title="2027 Liberia 10 Gram Joyeuse Sword UV Color Antiqued Silver Coin")
        self.assertTrue(result["isColorizedSilverCoin"])
        result = classify(title="2026 Silver Eagle Storm Edition", product_type="Printed Coins")
        self.assertTrue(result["isColorized"])
        self.assertEqual(result["colorizedEvidence"], "Product type: Printed Coins")

    def test_mixed_departments_require_direct_finish_evidence(self):
        result = classify(title="2027 Mexico 110 Anniversary 1 oz Silver Antiqued Round",
                          product_type="Silver Rounds and Bars Colorized Coins Nature Pre-Order Antiqued Coins",
                          description="This silver round has an antiqued finish.")
        self.assertFalse(result["isColorized"])
        for product_type in ["Colorized Coins", "Silver Colored Coins", "Colored Silver Coins"]:
            with self.subTest(product_type=product_type):
                self.assertTrue(classify(product_type=product_type)["isColorized"])
        self.assertTrue(classify(tags=["Colorized Coins"])["isColorized"])
        self.assertTrue(classify(attributes=[{"name": "Feature", "terms": [{"name": "Colorized"}]}])["isColorized"])

    def test_explicit_hd_printing_in_product_description(self):
        for printing in ["HD printing", "high-definition printing"]:
            with self.subTest(printing=printing):
                result = classify(title="Liberia 2026 Xipe Totec 2oz Silver Coin",
                                  description=f"This ultra high relief silver coin combines dramatic sculpting with advanced {printing} technology.")
                self.assertTrue(result["isColorizedSilverCoin"])
        self.assertFalse(classify(description="A beautiful, colorful artistic style.")["isColorized"])

    def test_explicit_coloration_in_product_description(self):
        for coloration in ["coloration", "colouration"]:
            with self.subTest(coloration=coloration):
                result = classify(title="Yellowstone National Park Topography 5 Oz Silver Coin 2025",
                                  description=f"The impressive {coloration} depicts the rough and natural geology very realistically and brings Yellowstone Lake to life.")
                self.assertTrue(result["isColorizedSilverCoin"])

    def test_html_markup_and_scripts(self):
        self.assertTrue(classify(description="<p>The coin has <strong>colour</strong> printing.</p>")["isColorized"])
        self.assertFalse(classify(description="<script>const title = 'Colorized coin';</script>Plain silver coin")["isColorized"])


if __name__ == "__main__":
    unittest.main()
