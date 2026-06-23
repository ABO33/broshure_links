# Praktis Brochure Linker

Python backend web app for adding clickable Praktis links to brochure PDFs.

## Run

Use the bundled Python runtime in Codex or any Python that has the packages from
`requirements.txt` installed.

```bash
python app.py
```

Then open on the same computer:

```text
http://127.0.0.1:5174
```

The app binds to `0.0.0.0` by default, so another computer on the same network
can open it with:

```text
http://YOUR-COMPUTER-IP:5174
```

Windows Firewall may ask to allow Python on private networks the first time you
run it. To force local-only mode, run:

```bash
python app.py --host 127.0.0.1
```

You can also change the port:

```bash
python app.py --port 8080
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
- Optionally compare the brochure euro price with the Praktis euro price using a Playwright-controlled Chrome session.
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

Price comparison uses the first euro price found on the product page or SKU
search-result page through Playwright/Chrome. The browser runs headless by
default and its profile defaults to `C:\PraktisProfile`, matching the working
scraper style, so Cloudflare cookies can persist between runs. Set
`PRAKTIS_HEADLESS=0` before starting the app if you need to see the browser for
debugging.

If the browser lookup cannot load or verify an exact product page, the generated
PDF uses the SKU search URL, for example
`https://praktis.bg/catalogsearch/result?q=35535079`. A generic
`https://praktis.bg/` homepage link is rejected before PDF links are written.
