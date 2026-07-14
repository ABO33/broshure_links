# PDF Regression Cases

This file is the durable history for PDF parsing and link-box bugs. Every fix should add or update an automated test in `test_linker_regressions.py` before changing shared geometry, SKU, or price rules.

| Area | Brochure and case | Required behavior | Automated test |
| --- | --- | --- | --- |
| Header price | `WEB_07_2026_Broshura.pdf`, page 9, SKUs `35572383`, `35572384`, `35609031` | Read `18.00`, never cents from the neighboring leva price | `test_split_price_does_not_take_cents_from_another_column` |
| Header price | `WEB_07_2026_Broshura.pdf`, page 17, SKU `3504920` | Read the complete euro digit sequence `83.00` | `test_sequence_price_wins_over_distant_leva_decimal` |
| Variant rows | `WEB_07_2026_Broshura.pdf`, page 17, shorthand table rows | Keep each shorthand expression on its own table row | `test_table_shorthand_expression_stays_on_its_own_row` |
| Link boxes | `WEB_07_2026_Broshura.pdf`, page 19, `35616592` / `386277` and `3500766` / `35603610` | A shorthand SKU black bar with its own price box starts a new item cell | `test_shorthand_item_header_below_variant_table_starts_a_new_box` |
| Measure units | Excel column O and PDF price header | Ignore case and punctuation (`бр`, `бр.`, `БР`, `Бр`) and tolerate known spelling variants | `test_measure_unit_comparison_ignores_common_spelling_variants` |
| Measure units | `WEB_07_2026_Broshura.pdf`, pages 9 and 17, SKUs `35648454`, `35565721`-`35565723`, `3504920` | Reconstruct split PDF text `€/б` + `р.` as `бр` before comparing with Excel | `test_split_pdf_measure_unit_is_reconstructed_before_comparison` |
| Measure units | `WEB_07_2026_Broshura.pdf`, page 17, variant SKU `3457010` | Reconstruct split units in variant-table headers, not only parent price boxes | `test_split_variant_table_measure_unit_is_reconstructed` |
| Measure units | Excel/PDF package units | Treat `пак`, `пак.`, mixed case, and `пакет` as the same unit | `test_package_measure_unit_abbreviations_match` |
| Measure status | Unit mismatch during Excel comparison | Add the unit warning to status data | `test_measure_unit_mismatch_is_added_to_status_flags` |

## Change Rule

When a brochure exposes a new failure:

1. Reproduce it using the smallest possible synthetic `PageText` fixture.
2. Add the expected SKU, price, or box boundary here.
3. Run the complete regression suite, not only the new test.
4. Validate the affected real PDF page with debug boxes before finishing.
