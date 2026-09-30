import unittest

from backend.praktis_playwright import extract_euro_price_from_text, extract_price_like_token


class PraktisWebsitePriceExtractionTests(unittest.TestCase):
    def test_current_search_card_price_pairs(self):
        cases = {
            "35557645": ("79.00 € 154.51 лв", 79.00),
            "35565722": ("6.99 € 13.67 лв", 6.99),
            "35565810": ("21.60 € 42.25 лв", 21.60),
            "35555802": ("20.70 € 40.49 лв", 20.70),
        }
        for sku, (text, expected) in cases.items():
            with self.subTest(sku=sku):
                self.assertEqual(extract_euro_price_from_text(text), expected)

    def test_minor_part_after_currency_marker_is_rejoined(self):
        cases = {
            "35565722": ("6 € 99 13.67 лв", 6.99),
            "35565810": ("21 € 60 42.25 лв", 21.60),
            "35555802": ("20 € 70 40.49 лв", 20.70),
        }
        for sku, (text, expected) in cases.items():
            with self.subTest(sku=sku):
                self.assertEqual(extract_euro_price_from_text(text), expected)

    def test_nearby_leva_major_part_is_not_read_as_euro_cents(self):
        text = "Долен шкаф 79.00 154 € 154.51 лв"
        self.assertEqual(extract_euro_price_from_text(text), 79.00)

    def test_euro_price_does_not_require_a_lev_conversion_pair(self):
        self.assertEqual(extract_euro_price_from_text("Артикул 60 € 42.25 лв"), 60.0)
        self.assertEqual(extract_euro_price_from_text("Артикул 154 € 154.51 лв"), 154.0)

    def test_whole_website_amount_is_not_divided_into_cents(self):
        self.assertEqual(extract_price_like_token("154"), 154.00)
        self.assertEqual(extract_euro_price_from_text("154 €"), 154.00)


if __name__ == "__main__":
    unittest.main()
