# PDF Parser Contract

These rules capture the brochure behavior established through real PDF regressions. Parser changes must preserve them unless the brochure design deliberately changes.

## Item Cell Geometry

- A readable product cell starts at a black SKU bar paired with its price box.
- The cell's left edge starts at its own SKU bar. It must not enter the product cell on the left.
- The cell's right edge is the right edge of its own price box. A taller neighboring cell may continue independently.
- The cell's bottom is the first lower item header whose horizontal span overlaps the current cell.
- A comma- or range-form SKU bar is still an item header when it has a same-line euro/lev header and a large price directly below it.
- Small SKU rows under `Код`/description/euro/lev table headers are variants, not item-cell boundaries.
- Variant rows are included in price results and complex grouping but never receive separate PDF link annotations.
- Full-page products may extend to the footer boundary when there is no lower overlapping item header.

## Page Boundaries

- Page 1 has no footer and must not be shortened using the normal footer rule.
- When processing the whole brochure, the final non-product/contact page is skipped.
- Later pages stop product cells at the detected footer, including taller footer designs with phone numbers.
- Large embedded advertising artwork below readable products may stop a cell only when the image-boundary evidence is strong; ordinary multi-image product cells must remain intact.

## SKU Reading

- Read only text-layer SKU data. Codes embedded only in images are not linked.
- Valid SKUs contain 5 to 12 digits.
- For simple comma suffixes, expand the changed ending from the preceding full SKU.
- For shortened ranges such as `3448892-9`, include every SKU through `3448899`.
- For full ranges such as `35551387-35551394`, include both endpoints and all SKUs between them.
- Mixed comma/range expressions continue from the most recent appropriate full base SKU.
- A repeated or overlapping shorthand expansion is flagged as an illustration error only on the parent expression that contains the overlap.
- Numbers in description, size, litre, phone, and page-footer columns must not become SKU variants.
- Repeated SKUs are flagged only when the actual SKU appears in more than one product occurrence, not when shorthand expansion and a table row describe the same occurrence.

## Price Reading

- The brochure euro column is always the first price column; the leva column is second.
- Variant-table prices come only from the SKU and euro columns. Description, size, litres, and leva values are not price candidates.
- Split euro prices must use nearby digits from the same price box; cents from a neighboring leva or product column are invalid.
- Parent and variant leva/euro conversion uses the fixed `1.95583` rate and is flagged when inconsistent.
- A promotion percentage is checked only after the displayed current price is valid.
- A missing `?` price remains undefined while website and Excel prices may be shown as suggestions.
- For an `от` item, compare the displayed brochure price only with the lowest-priced SKU in the group; all other group SKUs have an undefined brochure price.

## Measure Units

- PDF units are read from the euro price header and compared with Excel column O.
- Comparison lowercases both values and removes every non-letter character before matching. For example, `бр`, `бр.`, `БР`, `Бр`, and punctuation-heavy forms are equivalent.
- Known abbreviations and small spelling variants are canonicalized before comparison.
- A mismatch is displayed as the sole Status badge `Грешна мерна единица`; it is not a separate table column.

## Regression Workflow

1. Reproduce the real page and inspect extracted word coordinates.
2. Create the smallest synthetic `PageText` test that still triggers the failure.
3. Change the narrowest rule that distinguishes the correct item from the false candidate.
4. Run the full test suite.
5. Process and render the affected real page with debug boxes.
6. Add the real SKU/page case to `tests/REGRESSION_CASES.md`.
