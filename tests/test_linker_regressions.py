import unittest

from backend.excel_prices import normalize_measure_unit
from backend.linker import (
    PageText,
    Word,
    attach_excel_comparisons,
    compare_measure_units,
    detect_header_driven_boxes,
    detection_sku_expression,
    find_header_price_word,
    find_item_brochure_unit,
    find_table_price_window,
    measure_unit_from_price_header,
)


class LinkerRegressionTests(unittest.TestCase):
    def test_split_price_does_not_take_cents_from_another_column(self):
        sku = Word("35572383", 288, 314, 539, 545)
        euro = Word("1800", 341, 374, 548, 567)
        leva = Word("3520", 380, 414, 548, 567)
        unrelated = Word("20", 433, 442, 552, 559)
        page = PageText(9, 567, 820, [sku, euro, leva, unrelated])

        self.assertEqual(find_header_price_word(page, sku, page.width).text, "1800")

    def test_sequence_price_wins_over_distant_leva_decimal(self):
        sku = Word("3504920", 288, 312, 18, 24)
        euro_major = Word("83", 330, 352, 28, 46)
        euro_zero_1 = Word("0", 352, 360, 27, 38)
        euro_zero_2 = Word("0", 360, 367, 27, 38)
        leva_decimal = Word("33", 402, 414, 27, 38)
        page = PageText(17, 567, 820, [sku, euro_major, euro_zero_1, euro_zero_2, leva_decimal])

        self.assertEqual(find_header_price_word(page, sku, page.width).text, "8300")

    def test_table_shorthand_expression_stays_on_its_own_row(self):
        current = Word("3452806", 421, 437, 758, 763, comma_primary=True, original_text="3452806,18")
        next_row = Word("3452808", 421, 437, 767, 772, comma_primary=True, original_text="3452808,20")
        price = Word("3.10", 513, 528, 758, 764)
        page = PageText(17, 567, 820, [current, next_row, price])
        item = {
            "sku": "3452806",
            "box": {"x": 420, "y": 757, "width": 109, "height": 12},
            "brochure_price": 3.10,
            "brochure_price_text": "3.10",
        }

        self.assertEqual(detection_sku_expression(page, item, header=False), "3452806,18")

    def test_measure_units_are_normalized(self):
        self.assertEqual(normalize_measure_unit("БР"), "бр")
        self.assertEqual(normalize_measure_unit("квм"), "кв.м")
        self.assertEqual(normalize_measure_unit("линм"), "л.м")
        self.assertEqual(normalize_measure_unit("к-т"), "комплект")

    def test_measure_unit_comparison_ignores_common_spelling_variants(self):
        spellings = ("бр.", "БР.", "БР", "бр", "Бр", " Б.Р,-_/ 123 ")
        for brochure_unit in spellings:
            for excel_unit in spellings:
                self.assertEqual(compare_measure_units(brochure_unit, excel_unit)[0], "match")
        self.assertEqual(compare_measure_units("комплек", "комплект")[0], "match")
        self.assertEqual(compare_measure_units("бр", "кв.м")[0], "unit_mismatch")

    def test_shorthand_item_header_below_variant_table_starts_a_new_box(self):
        first_sku = Word("35616592", 10, 35, 10, 16)
        first_price = Word("740", 105, 132, 19, 38)
        table_code = Word("Код", 10, 27, 54, 61)
        table_euro = Word("€/бр.", 105, 130, 54, 61)
        shorthand_sku = Word(
            "386277",
            10,
            31,
            100,
            106,
            comma_primary=True,
            original_text="386277,386269,71,2,4,5",
        )
        shorthand_euro = Word("€/бр.", 105, 130, 100, 106)
        shorthand_price = Word("940", 105, 132, 109, 128)
        next_sku = Word("3500766", 10, 35, 200, 206)
        next_price = Word("1090", 105, 138, 209, 228)
        page = PageText(
            19,
            190,
            300,
            [
                first_sku,
                first_price,
                table_code,
                table_euro,
                shorthand_sku,
                shorthand_euro,
                shorthand_price,
                next_sku,
                next_price,
            ],
            285,
        )

        detections = detect_header_driven_boxes(
            page,
            [first_sku, shorthand_sku, next_sku],
            5,
            12,
            0,
        )
        by_sku = {item["sku"]: item for item in detections}

        self.assertIn("386277", by_sku)
        self.assertAlmostEqual(by_sku["35616592"]["box"]["height"], 90)
        self.assertAlmostEqual(by_sku["386277"]["box"]["height"], 100)

    def test_measure_unit_mismatch_is_added_to_status_flags(self):
        item = {
            "sku": "12345",
            "brochure_price": 10.0,
            "brochure_price_not_defined": False,
            "brochure_unit": "бр.",
        }
        attach_excel_comparisons(
            [item],
            {"12345": {"excel_price": 10.0, "excel_unit": "кв.м"}},
        )

        self.assertEqual(item["unit_status"], "unit_mismatch")
        self.assertEqual(item["status_flags"], ["unit_mismatch"])

    def test_split_pdf_measure_unit_is_reconstructed_before_comparison(self):
        euro_header = Word("€/б", 50, 64, 10, 18)
        unit_suffix = Word("р.", 64, 69, 12, 17)
        price = Word("6660", 50, 82, 20, 39)
        page = PageText(9, 190, 300, [euro_header, unit_suffix, price])
        box = {"x": 0, "y": 0, "width": 150, "height": 100}

        self.assertEqual(find_item_brochure_unit(page, box, price), "бр")
        self.assertEqual(compare_measure_units(find_item_brochure_unit(page, box, price), "бр")[0], "match")

    def test_split_variant_table_measure_unit_is_reconstructed(self):
        code_header = Word("Код", 10, 28, 10, 17)
        euro_header = Word("€/б", 100, 114, 10, 18)
        unit_suffix = Word("р.", 114, 119, 12, 17)
        leva_header = Word("лв./бр.", 140, 170, 10, 18)
        sku = Word("3457010", 10, 32, 22, 27)
        euro_price = Word("29.00", 100, 122, 21, 28)
        page = PageText(
            17,
            190,
            300,
            [code_header, euro_header, unit_suffix, leva_header, sku, euro_price],
        )

        window = find_table_price_window(page, sku, page.width)

        self.assertIsNotNone(window)
        self.assertEqual(measure_unit_from_price_header(window["first_header"]), "бр")

    def test_package_measure_unit_abbreviations_match(self):
        spellings = ("пак", "пак.", "ПАК", "пакет", "Пакет")
        for brochure_unit in spellings:
            for excel_unit in spellings:
                self.assertEqual(compare_measure_units(brochure_unit, excel_unit)[0], "match")


if __name__ == "__main__":
    unittest.main()
