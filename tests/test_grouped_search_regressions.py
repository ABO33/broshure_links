import unittest

from backend.grouped_search import common_ordered_words


class GroupedSearchRegressionTests(unittest.TestCase):
    def test_pastelo_matches_cyrillic_and_latin_range_spelling(self):
        titles = [
            "Алкидна боя Пастело бяла 0,65л",
            "Алкидна боя Пастело електриково зелена 0,65л",
            "Алкидна боя екстра Pastelo небесносиня 0,65л",
            "Алкидна боя екстра Pastelo охра",
            "Алкидна боя екстра Pastelo черна 650 мл",
        ]

        self.assertEqual(common_ordered_words(titles), "Алкидна боя Пастело")

    def test_slash_variant_model_keeps_stable_numeric_stem(self):
        titles = ["Полилей EL 1383/2 CH", "Полилей EL 1383/3 CH"]

        self.assertEqual(common_ordered_words(titles), "Полилей EL 1383 CH")

    def test_shared_wattage_is_kept_for_ledmax_bulbs(self):
        titles = [
            "LED крушка LEDMax Е27 9W 6500K",
            "LED крушка LEDMax Е27 9W 4000K",
        ]

        self.assertEqual(common_ordered_words(titles), "LED крушка LEDMax Е27 9W")

    def test_shared_wattage_is_kept_when_product_descriptions_differ(self):
        titles = [
            "LED осветително тяло Dars 24W / BK CL-4000K черно",
            "LED панел Dars 24W 4000K",
        ]

        self.assertEqual(common_ordered_words(titles), "LED Dars 24W")

    def test_model_number_beats_low_value_width_word(self):
        titles = [
            "Балатум Bingo Alaska 592 ширина 2м",
            "Балатум Bingo Alaska 592 ширина 4м",
            "Балатум Bingo Alaska 592 ширина 3м",
        ]

        self.assertEqual(common_ordered_words(titles), "Балатум Bingo Alaska 592")

    def test_shared_count_and_color_are_kept_for_drying_rack(self):
        titles = [
            "Метален таванен простор 5 пръчки / 1.8м черен",
            "Метален таванен простор 5 пръчки / 2 м черен",
        ]

        self.assertEqual(
            common_ordered_words(titles),
            "Метален таванен простор 5 пръчки черен",
        )


if __name__ == "__main__":
    unittest.main()
