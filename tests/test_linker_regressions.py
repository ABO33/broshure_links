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
    item_price_box_status,
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

    def test_euro_only_header_uses_visual_cell_geometry(self):
        first_sku = Word("35522159", 197.7, 222.8, 18.3, 23.8)
        first_price = Word("1610", 340.2, 370.0, 19.9, 38.4)
        next_sku = Word("35525231", 197.7, 223.0, 148.6, 154.1)
        next_price = Word("1690", 340.0, 370.0, 150.2, 168.7)
        page = PageText(
            12,
            566.929,
            836.22,
            [first_sku, first_price, next_sku, next_price],
            800,
            header_blocks=[
                {"x0": 194.83, "x1": 372.09, "top": 14.17, "bottom": 38.27},
                {"x0": 194.83, "x1": 372.09, "top": 144.51, "bottom": 168.60},
            ],
        )

        detections = detect_header_driven_boxes(page, [first_sku, next_sku], 5, 12, 0)

        self.assertEqual(len(detections), 2)
        self.assertEqual(detections[0]["brochure_price"], 16.10)
        self.assertAlmostEqual(detections[0]["box"]["x"], 194.83)
        self.assertAlmostEqual(detections[0]["box"]["y"], 14.17)
        self.assertAlmostEqual(detections[0]["box"]["width"], 177.26)
        self.assertAlmostEqual(detections[0]["box"]["height"], 130.34)

    def test_euro_only_price_does_not_validate_neighboring_currency(self):
        sku = Word("35522159", 10, 35, 10, 16)
        euro = Word("1610", 100, 130, 11.5, 30)
        unrelated_price = Word("9999", 140, 175, 11.5, 30)
        page = PageText(12, 190, 300, [sku, euro, unrelated_price])
        box = {"x": 6, "y": 7, "width": 175, "height": 100}

        self.assertEqual(item_price_box_status(page, box, sku, euro), ("", ""))

    def test_visual_header_wins_over_variant_table_heuristic(self):
        code_header = Word("Код", 10, 28, 70, 77)
        euro_header = Word("€/бр.", 100, 125, 70, 77)
        sku = Word("35642158", 10, 35, 100, 105.5, comma_primary=True, original_text="35642158-63")
        price = Word("2250", 100, 132, 101.6, 120.1)
        page = PageText(
            12,
            190,
            300,
            [code_header, euro_header, sku, price],
            285,
            header_blocks=[{"x0": 7, "x1": 145, "top": 96, "bottom": 120}],
        )

        detections = detect_header_driven_boxes(page, [sku], 5, 12, 0)

        self.assertEqual([item["sku"] for item in detections], ["35642158"])
        self.assertEqual(detections[0]["brochure_price"], 22.50)

    def test_new_design_skips_nonvisual_table_sku(self):
        product_sku = Word("35534881", 10, 35, 10, 15.5)
        product_price = Word("2599", 100, 132, 11.6, 30.1)
        table_sku = Word("3513969", 145, 170, 150, 155.5)
        unrelated_price = Word("2170", 175, 208, 151.6, 170.1)
        page = PageText(
            20,
            220,
            300,
            [product_sku, product_price, table_sku, unrelated_price],
            285,
            header_blocks=[{"x0": 7, "x1": 138, "top": 6, "bottom": 30}],
        )

        detections = detect_header_driven_boxes(page, [product_sku, table_sku], 5, 12, 0)

        self.assertEqual([item["sku"] for item in detections], ["35534881"])

    def test_multiline_sku_expression_claims_one_visual_header(self):
        first_line = Word("268047", 10, 30, 100, 105.5, comma_primary=True, original_text="268047,52,6,8,60,")
        second_line = Word("3497245", 20, 45, 106, 111.5, comma_primary=True, original_text="3497245-51")
        price = Word("550", 100, 128, 101.6, 120.1)
        page = PageText(
            12,
            190,
            300,
            [first_line, second_line, price],
            285,
            header_blocks=[{"x0": 7, "x1": 145, "top": 96, "bottom": 120}],
        )

        detections = detect_header_driven_boxes(page, [first_line, second_line], 5, 12, 0)

        self.assertEqual([item["sku"] for item in detections], ["268047"])


if __name__ == "__main__":
    unittest.main()
