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

## Run With Docker

From this folder, run:

```powershell
.\docker-start.cmd
```

That script pulls the latest committed changes, opens a dedicated Windows Chrome
profile for the Praktis service, and starts Docker Compose. The app is available
at:

```text
http://127.0.0.1:5174
```

Other computers on the same network can open:

```text
http://YOUR-COMPUTER-IP:5174
```

The dedicated Chrome profile is stored at:

```text
%USERPROFILE%\PraktisServiceChrome
```

The first time you run Docker mode, sign in and pass any Praktis/Cloudflare
challenge in the Chrome window that opens. After that, Docker connects to this
same Windows Chrome profile through `http://host.docker.internal:9222`, so it can
reuse the trusted browser session instead of using an isolated Docker browser.

To start the service automatically after Windows login, run once:

```powershell
powershell -ExecutionPolicy Bypass -File .\install-startup-task.ps1
```

To remove that startup task:

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall-startup-task.ps1
```

## What It Does

- Upload a brochure PDF.
- Process the whole brochure, one selected page, or a page range.
- Extract readable text-layer SKUs from 5 to 12 digits.
- Skip SKU codes that are only inside images.
- Expand supported comma/range shorthand SKU groups into complex item groups.
- Include variant SKU tables in the report for price comparison; they do not create separate PDF link boxes.
- Fall back to `https://praktis.bg/catalogsearch/result?q={sku}` when selected.
- Optionally find Praktis product links and compare the brochure euro price with the Praktis euro price using a Playwright-controlled Chrome session.
- Optionally upload an `.xlsx` price file and compare brochure prices with Excel column M SKUs and column U prices.
- Optionally run a triple check across brochure, Praktis website, and Excel prices.
- Write invisible PDF link annotations over detected item boxes.
- Download the linked PDF plus Excel/JSON reports.

## Notes

The app can create useful search links even if Praktis blocks automated product
page verification.

Price comparison uses the first euro price found on the product page or SKU
search-result page through Playwright/Chrome. In Docker mode, the scraper uses
the dedicated Windows Chrome profile opened by `launch-service-chrome.ps1`.
Without Docker, it uses Playwright's persistent browser profile and runs
headless by default. Set `PRAKTIS_HEADLESS=0` before starting the app if you
need to see that non-Docker browser for debugging.

If the browser lookup cannot load or verify an exact product page, the generated
PDF uses the SKU search URL, for example
`https://praktis.bg/catalogsearch/result?q=35535079`, and the report status is
flagged as blocked, not found, error, or link not found. A generic
`https://praktis.bg/` homepage link is rejected before PDF links are written.
