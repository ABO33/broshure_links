# Praktis Brochure Linker

Python backend web app for adding clickable Praktis links to brochure PDFs.

## Run

Use the bundled Python runtime in Codex or any Python that has `pdfplumber`,
`pypdf`, and `reportlab` installed.

```bash
python app.py
```

Then open:

```text
http://127.0.0.1:5174
```

## What It Does

- Upload a brochure PDF.
- Extract readable text-layer SKUs from 5 to 12 digits.
- Skip SKU codes that are only inside images.
- If a SKU line contains comma-separated values, only the first value before the comma is used.
- Variant SKU tables are ignored; the link is placed on the main product item only.
- Resolve URLs from an optional CSV/JSON mapping file.
- Optionally try exact live Praktis lookup.
- Fall back to `https://praktis.bg/catalogsearch/result?q={sku}` when enabled.
- Optionally compare the brochure price with the website price when an exact product page URL is available.
- Write invisible PDF link annotations over detected item boxes.
- Download the linked PDF plus CSV/JSON reports.

## Mapping File

CSV:

```csv
sku,url,title
35535079,https://praktis.bg/example-product,Optional title
```

JSON:

```json
[
  { "sku": "35535079", "url": "https://praktis.bg/example-product", "title": "Optional title" }
]
```

or:

```json
{
  "35535079": "https://praktis.bg/example-product"
}
```

## Notes

The app can create useful search links even if Praktis blocks automated product
page verification. Exact product links are best supplied through the mapping
file when live lookup is blocked.

Price comparison needs an exact product page URL. Search-result fallback links
are reported as `search_only` for price checks because they do not identify one
product price reliably.
