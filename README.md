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

Docker mode also starts Uptime Kuma for monitoring:

```text
http://127.0.0.1:3001
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

## Monitoring, Logs, And Discord Alerts

The production-style monitoring setup is free and Docker-based:

- The app writes colored console logs and rotating file logs in `logs/brochure-linker.log`.
- `/api/health` checks that the local web service is alive.
- `/api/health/deep` checks the service, disk space, internet access, Praktis reachability, and Chrome CDP when Docker scraping is enabled.
- `/api/metrics` shows request counters, running jobs, last success, last failure, and uptime.
- Discord alerts can be split across two channels: normal processing messages go to the regular webhook, and health problems/errors go to the error webhook.
- Uptime Kuma watches the app from outside the app container, so it can alert when the app/container is down.

Install manually:

1. Docker Desktop or Docker Engine.
2. Git, if you want `docker-start.ps1` to pull changes automatically.

Installed automatically by Docker:

- Python dependencies from `requirements.txt`.
- Playwright Chromium dependencies inside the app image.
- Uptime Kuma from the free `louislam/uptime-kuma:1` image.

### Discord Setup

Create two Discord webhooks:

1. Open Discord server settings.
2. Go to `Integrations` -> `Webhooks`.
3. Create one webhook in the regular processing/logs channel.
4. Create one webhook in the error alerts channel.
5. Copy `.env.example` to `.env`.
6. Paste the webhook URLs:

```text
DISCORD_INFO_WEBHOOK_URL=https://discord.com/api/webhooks/REGULAR_CHANNEL_WEBHOOK
DISCORD_ERROR_WEBHOOK_URL=https://discord.com/api/webhooks/ERROR_CHANNEL_WEBHOOK
```

`DISCORD_WEBHOOK_URL` is still supported for a single-channel setup. If both `DISCORD_INFO_WEBHOOK_URL` and `DISCORD_ERROR_WEBHOOK_URL` are set, the split-channel setup is used:

- `INFO` messages: regular channel.
- `WARNING`, `ERROR`, and `CRITICAL` messages: error channel.
- Processing failures and health problems always use the error channel.

Recommended defaults:

```text
DISCORD_LOG_LEVEL=ERROR
DISCORD_ERROR_LEVEL=WARNING
DISCORD_PROCESS_NOTIFICATIONS=1
DISCORD_STARTUP_NOTIFICATIONS=1
BROCHURE_SLOW_SECONDS=600
```

`DISCORD_LOG_LEVEL` controls which Python logger records are forwarded to Discord.
`DISCORD_ERROR_LEVEL=WARNING` keeps health problems in the error channel even though they are warning-level events.
Set `DISCORD_LOG_LEVEL=INFO` only when debugging, because it can be noisy.

### Uptime Kuma Setup

Open `http://127.0.0.1:3001`, create the admin user, then add monitors:

1. App health
   - Monitor type: `HTTP(s)`
   - URL: `http://brochure-linker:5174/api/health`
   - Expected status: `200`
2. Deep health
   - Monitor type: `HTTP(s)`
   - URL: `http://brochure-linker:5174/api/health/deep`
   - Expected status: `200`
   - This monitor goes down when internet, Praktis, disk, or Chrome CDP checks fail.
3. Praktis internet check
   - Monitor type: `HTTP(s)`
   - URL: `https://praktis.bg/`
   - Accepted status codes can include `200-499`, because Cloudflare may answer with 403 while the site is still reachable.

In Uptime Kuma, add a Discord notification using the error-channel webhook. This catches container/app outages even when the app itself is not alive to send Discord messages.

If you need alerts when the whole server or office PC is powered off, run Uptime Kuma on another always-on machine or a small VPS and point it to:

```text
http://YOUR-SERVER-IP:5174/api/health
http://YOUR-SERVER-IP:5174/api/health/deep
```

Useful local commands:

```powershell
docker compose ps
docker compose logs -f brochure-linker
docker compose logs -f uptime-kuma
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
