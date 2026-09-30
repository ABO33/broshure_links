# PDF Regression Cases

This file is the durable history for PDF parsing and link-box bugs. Every fix should add or update an automated test in `test_linker_regressions.py` before changing shared geometry, SKU, or price rules.

| Area | Brochure and case | Required behavior                                                                                                                          | Automated test |
| --- | --- |--------------------------------------------------------------------------------------------------------------------------------------------| --- |
| Header price | `WEB_07_2026_Broshura.pdf`, page 9, SKUs `35572383`, `35572384`, `35609031` | Read `18.00`, never cents from the neighboring leva price                                                                                  | `test_split_price_does_not_take_cents_from_another_column` |
| Header price | `WEB_07_2026_Broshura.pdf`, page 17, SKU `3504920` | Read the complete euro digit sequence `83.00`                                                                                              | `test_sequence_price_wins_over_distant_leva_decimal` |
| Variant rows | `WEB_07_2026_Broshura.pdf`, page 17, shorthand table rows | Keep each shorthand expression on its own table row                                                                                        | `test_table_shorthand_expression_stays_on_its_own_row` |
| Link boxes | `WEB_07_2026_Broshura.pdf`, page 19, `35616592` / `386277` and `3500766` / `35603610` | A shorthand SKU black bar with its own price box starts a new item cell                                                                    | `test_shorthand_item_header_below_variant_table_starts_a_new_box` |
| Measure units | Excel column O and PDF price header | Ignore case and punctuation (`бр`, `бр.`, `БР`, `Бр`) and tolerate known spelling variants                                                 | `test_measure_unit_comparison_ignores_common_spelling_variants` |
| Measure units | `WEB_07_2026_Broshura.pdf`, pages 9 and 17, SKUs `35648454`, `35565721`-`35565723`, `3504920` | Reconstruct split PDF text `€/б` + `р.` as `бр` before comparing with Excel                                                                | `test_split_pdf_measure_unit_is_reconstructed_before_comparison` |
| Measure units | `WEB_07_2026_Broshura.pdf`, page 17, variant SKU `3457010` | Reconstruct split units in variant-table headers, not only parent price boxes                                                              | `test_split_variant_table_measure_unit_is_reconstructed` |
| Website price | Praktis euro price text, SKUs `35557645`, `35565722`, `35565810`, `35555802` | Read the euro amount directly without requiring a lev conversion pair; never use a detached neighboring amount as euro cents | `test_praktis_price_extraction.py` |
| Euro-only link boxes | `WEB_08_2_2026_Broshura_200_295_mm.pdf`, page 12, SKUs `35522159`, `35525231` | Accept price digits aligned with the SKU line and use the visible black/accent header bounds for exact cell placement | `test_euro_only_header_uses_visual_cell_geometry` |
| Euro-only validation | `WEB_08_2_2026_Broshura_200_295_mm.pdf`, all pages | Do not raise lev/euro conversion errors when only the euro price box is present | `test_euro_only_price_does_not_validate_neighboring_currency` |
| Visual header priority | `WEB_08_2_2026_Broshura_200_295_mm.pdf`, page 12, SKU `35642158` | A real colored price header remains an item even when a nearby variant table could classify its SKU as a row | `test_visual_header_wins_over_variant_table_heuristic` |
| Multiline visual header | `WEB_08_2_2026_Broshura_200_295_mm.pdf`, page 12, SKU expression starting `268047` / `3497245` | Multiple SKU lines inside one visual header produce one item box owned by the first line | `test_multiline_sku_expression_claims_one_visual_header` |
| Non-header table SKU | `WEB_08_2_2026_Broshura_200_295_mm.pdf`, page 20, SKU `3513969` | Do not create a free-floating link box for a table code without a visual product header | `test_new_design_skips_nonvisual_table_sku` |
| Measure units | Excel/PDF package units | Treat `пак`, `пак.`, mixed case, and `пакет` as the same unit                                                                              | `test_package_measure_unit_abbreviations_match` |
| Measure status | Unit mismatch during Excel comparison | Add the unit warning to status data                                                                                                        | `test_measure_unit_mismatch_is_added_to_status_flags` |
| Complex links | Name-based Praktis group validation fails | Reuse the parent product's last breadcrumb category, append `?onSale=3`, skip category result validation, and flag `group_category_review` | `test_failed_name_group_uses_parent_category_without_category_validation` |
| Complex links | Name-based Praktis group validation succeeds | Keep the validated name-search link and do not invoke the category fallback                                                                | `test_valid_name_group_stays_on_grouped_search` |
| Complex query | Parent `3448864`, mixed `Пастело` / `Pastelo` titles | Treat Cyrillic and Latin range spelling as the same shared term | `test_pastelo_matches_cyrillic_and_latin_range_spelling` |
| Complex query | Parent `3457009`, models `1383/2` and `1383/3` | Keep shared model stem `1383` | `test_slash_variant_model_keeps_stable_numeric_stem` |
| Complex query | Parents `3500249` and `35537415` | Keep wattage when it is identical across every grouped title | `test_shared_wattage_is_kept_for_ledmax_bulbs`, `test_shared_wattage_is_kept_when_product_descriptions_differ` |
| Complex query | Parent `35538157`, model `592` | Prefer the shared model number and discard low-value `ширина` | `test_model_number_beats_low_value_width_word` |
| Complex query | Parent `35652059`, shared count and color | Keep `5 пръчки черен`, while removing differing lengths | `test_shared_count_and_color_are_kept_for_drying_rack` |

## Change Rule

When a brochure exposes a new failure:

1. Reproduce it using the smallest possible synthetic `PageText` fixture.
2. Add the expected SKU, price, or box boundary here.
3. Run the complete regression suite, not only the new test.
4. Validate the affected real PDF page with debug boxes before finishing.
