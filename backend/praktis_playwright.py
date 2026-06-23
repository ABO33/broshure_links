from __future__ import annotations

import asyncio
from html import unescape
import os
from pathlib import Path
from random import uniform
import re
from urllib.parse import quote, urljoin, urlparse


BASE_URL = "https://praktis.bg"
SEARCH_URL_TEMPLATE = BASE_URL + "/catalogsearch/result?q={}"
CONCURRENCY = max(1, int(os.environ.get("PRAKTIS_CONCURRENCY", "2")))
SEARCH_TIMEOUT_MS = int(os.environ.get("PRAKTIS_TIMEOUT_MS", "30000"))
PAGE_WAIT_MS = int(os.environ.get("PRAKTIS_PAGE_WAIT_MS", "1500"))
PROFILE_DIR = os.environ.get("PRAKTIS_PROFILE", r"C:\PraktisProfile")
CHROME_CHANNEL = os.environ.get("PRAKTIS_CHANNEL", "chrome")
HEADLESS = os.environ.get("PRAKTIS_HEADLESS", "1").lower() in {"1", "true", "yes"}
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

try:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright
except Exception:  # pragma: no cover - exercised when dependency is missing.
    PlaywrightTimeoutError = TimeoutError
    async_playwright = None


def compare_prices_with_playwright(skus: list[str]) -> dict[str, dict]:
    unique_skus = sorted({str(sku).strip() for sku in skus if str(sku).strip()})
    if not unique_skus:
        return {}

    if async_playwright is None:
        return {
            sku: {
                "sku": sku,
                "price_status": "playwright_unavailable",
                "price_message": "Install Playwright to use Praktis browser price checks.",
            }
            for sku in unique_skus
        }

    try:
        results = asyncio.run(_run_batch(unique_skus))
    except Exception as exc:
        message = f"Playwright price lookup failed: {exc}"
        return {
            sku: {
                "sku": sku,
                "price_status": "error",
                "price_message": message[:300],
            }
            for sku in unique_skus
        }

    return {result["sku"]: result for result in results}


async def _run_batch(skus: list[str]) -> list[dict]:
    sem = asyncio.Semaphore(CONCURRENCY)
    Path(PROFILE_DIR).mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            channel=CHROME_CHANNEL,
            headless=HEADLESS,
            viewport={"width": 1366, "height": 768},
            locale="bg-BG",
            timezone_id="Europe/Sofia",
            user_agent=USER_AGENT,
            extra_http_headers={
                "Accept-Language": "bg-BG,bg;q=0.9,en-US;q=0.8,en;q=0.7",
                "Referer": BASE_URL + "/",
            },
            args=["--disable-blink-features=AutomationControlled"],
        )

        await _warm_session(context)
        tasks = [asyncio.create_task(_scrape_one_sku(context, sku, sem)) for sku in skus]
        try:
            return await asyncio.gather(*tasks)
        finally:
            await context.close()


async def _warm_session(context) -> None:
    page = await context.new_page()
    try:
        await _prepare_page(page)
        await page.goto(BASE_URL + "/", wait_until="domcontentloaded", timeout=SEARCH_TIMEOUT_MS)
        await _accept_cookies_if_visible(page)
        await page.wait_for_timeout(1000)
    except Exception:
        pass
    finally:
        await _safe_close(page)


async def _scrape_one_sku(context, sku: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        await asyncio.sleep(uniform(0.6, 1.8))
        page = await context.new_page()
        await _prepare_page(page)
        search_url = SEARCH_URL_TEMPLATE.format(quote(sku))

        try:
            response = await page.goto(search_url, wait_until="domcontentloaded", timeout=SEARCH_TIMEOUT_MS)
            status_code = response.status if response else None
            await _accept_cookies_if_visible(page)
            await page.wait_for_timeout(PAGE_WAIT_MS)

            if await _looks_blocked(page, status_code):
                return {
                    "sku": sku,
                    "status": "search",
                    "source": "playwright",
                    "url": search_url,
                    "title": f"Praktis search for {sku}",
                    "price_status": "blocked",
                    "price_message": "Praktis/Cloudflare blocked the browser price lookup.",
                }

            current_url = page.url or search_url
            title = await _safe_title(page)
            price = await _extract_price_from_page(page)
            product_url = current_url if _is_product_url(current_url) else await _find_product_link_on_search_page(page)

            if product_url and price is None and product_url != current_url:
                await page.goto(product_url, wait_until="domcontentloaded", timeout=SEARCH_TIMEOUT_MS)
                await _accept_cookies_if_visible(page)
                await page.wait_for_timeout(PAGE_WAIT_MS)
                title = await _safe_title(page)
                price = await _extract_price_from_page(page)

            if product_url or price is not None:
                return _success_result(
                    sku=sku,
                    url=product_url or search_url,
                    title=title,
                    price=price,
                    used_search_price=product_url is None or product_url == search_url,
                )

            body_text = await _safe_inner_text(page, "body")
            if sku in body_text:
                return _success_result(
                    sku=sku,
                    url=search_url,
                    title=title or f"Praktis search for {sku}",
                    price=None,
                    used_search_price=True,
                )

            return {
                "sku": sku,
                "status": "search",
                "source": "playwright",
                "url": search_url,
                "title": f"Praktis search for {sku}",
                "price_status": "not_found",
                "price_message": "No exact Praktis result or euro price was found; SKU search link was used.",
            }

        except PlaywrightTimeoutError as exc:
            return {
                "sku": sku,
                "status": "search",
                "source": "playwright",
                "url": search_url,
                "title": f"Praktis search for {sku}",
                "price_status": "error",
                "price_message": f"Praktis lookup timed out; SKU search link was used. {exc}"[:300],
            }
        except Exception as exc:
            return {
                "sku": sku,
                "status": "search",
                "source": "playwright",
                "url": search_url,
                "title": f"Praktis search for {sku}",
                "price_status": "error",
                "price_message": f"Praktis lookup error; SKU search link was used. {exc!r}"[:300],
            }
        finally:
            await _safe_close(page)


def _success_result(sku: str, url: str, title: str, price: float | None, used_search_price: bool) -> dict:
    safe_url = url if _is_product_url(url) or _is_search_url(url) else SEARCH_URL_TEMPLATE.format(quote(sku))
    result = {
        "sku": sku,
        "status": "linked" if _is_product_url(safe_url) else "search",
        "source": "playwright",
        "url": safe_url,
        "title": title or f"Praktis result for {sku}",
    }
    if price is None:
        result.update(
            {
                "price_status": "not_found",
                "price_message": "No euro website price was found by the Praktis browser lookup.",
            }
        )
    else:
        result.update(
            {
                "website_price": price,
                "price_status": "website_price_found",
                "price_message": (
                    "First euro price on the Praktis search result was used."
                    if used_search_price
                    else "Euro price found on the Praktis product page."
                ),
            }
        )
    return result


async def _prepare_page(page) -> None:
    await page.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
    )


async def _accept_cookies_if_visible(page) -> None:
    candidates = [
        "button:has-text('\u041f\u0440\u0438\u0435\u043c\u0430\u043c')",
        "button:has-text('\u0421\u044a\u0433\u043b\u0430\u0441\u0435\u043d')",
        "button:has-text('Accept')",
        "text=\u041f\u0440\u0438\u0435\u043c\u0430\u043c",
        "text=Accept",
    ]
    for selector in candidates:
        try:
            locator = page.locator(selector).first
            if await locator.count() > 0 and await locator.is_visible(timeout=1000):
                await locator.click(timeout=2000)
                await page.wait_for_timeout(500)
                return
        except Exception:
            pass


async def _looks_blocked(page, status_code: int | None) -> bool:
    if status_code in {403, 429}:
        return True
    title = await _safe_title(page)
    body = await _safe_inner_text(page, "body")
    return bool(
        re.search(
            r"cf_chl_|challenge-platform|enable javascript and cookies|just a moment|cloudflare",
            f"{title}\n{body}",
            re.I,
        )
    )


async def _find_product_link_on_search_page(page, sku: str | None = None) -> str | None:
    selectors = [
        "a.product-item-link",
        ".product-item a[href]",
        "article a[href]",
        "section a[href]",
        "main a[href]",
        "a[href]",
    ]

    for selector in selectors:
        try:
            links = await page.locator(selector).evaluate_all(
                "els => els.map(a => a.href).filter(Boolean)"
            )
        except Exception:
            links = []

        for link in links:
            candidate = _abs(link)
            if candidate and _is_product_url(candidate):
                return candidate

    try:
        html = await page.content()
    except Exception:
        return None
    for candidate in _extract_product_links_from_html(html):
        if _is_product_url(candidate):
            return candidate
    return None


def _extract_product_links_from_html(html: str) -> list[str]:
    hrefs = re.findall(r"<a[^>]+href=[\"']([^\"']+)[\"']", html, flags=re.I)
    out: list[str] = []
    seen: set[str] = set()
    bad_parts = (
        "/catalogsearch/",
        "/customer/",
        "/checkout/",
        "/wishlist/",
        "/catalog/category/",
        "/blog/",
        "/contacts",
        "javascript:",
        "#",
    )

    for href in hrefs:
        full = _abs(href)
        if not full:
            continue
        low = full.lower()
        if any(part in low for part in bad_parts):
            continue
        if re.search(r"\.(?:jpg|jpeg|png|webp|svg|css|js|ico)(?:\?|$)", low):
            continue
        if full not in seen:
            seen.add(full)
            out.append(full)
    return out


async def _extract_price_from_page(page) -> float | None:
    selectors = [
        "[data-price-type='finalPrice'] .price",
        "[data-price-type='finalPrice']",
        ".product-info-price .price",
        ".product-item .price",
        ".price-box .price",
        ".price-box",
        ".price-container",
    ]

    for selector in selectors:
        try:
            blocks = await page.locator(selector).evaluate_all(
                """
                els => els.slice(0, 30).map(el => [
                    el.innerText || el.textContent || '',
                    el.getAttribute('data-price-amount') || '',
                    el.getAttribute('content') || ''
                ].join(' '))
                """
            )
        except Exception:
            blocks = []

        for block in blocks:
            price = extract_euro_price_from_text(block)
            if price is not None:
                return price
            price = extract_price_like_token(block)
            if price is not None and not re.search(r"\b(?:bgn|lv)\b|\u043b\u0432", _clean_text(block), re.I):
                return price

    for selector in ("main", "body"):
        text = await _safe_inner_text(page, selector)
        price = extract_euro_price_from_text(text)
        if price is not None:
            return price
    return None


def extract_euro_price_from_text(text: str) -> float | None:
    cleaned = _clean_text(text)
    if not cleaned:
        return None

    for match in re.finditer(r"\u20ac|eur|\u0435\u0432\u0440\u043e", cleaned, re.I):
        before = cleaned[max(0, match.start() - 90) : match.start()]
        price = extract_last_price_like_token(before)
        if price is not None:
            return price

        block = cleaned[match.start() : match.start() + 180]
        price = extract_price_like_token(block)
        if price is not None:
            return price

    patterns = [
        r'"price"\s*:\s*"?([0-9]+(?:[.,][0-9]+)?)',
        r"itemprop=[\"']price[\"'][^>]*content=[\"']([0-9]+(?:[.,][0-9]+)?)",
        r"content=[\"']([0-9]+(?:[.,][0-9]+)?)[\"'][^>]*itemprop=[\"']price[\"']",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, cleaned, re.I):
            nearby = cleaned[max(0, match.start() - 120) : match.end() + 120]
            if re.search(r"\u20ac|eur|\u0435\u0432\u0440\u043e", nearby, re.I):
                return _decimal(match.group(1))
    return None


def extract_price_like_token(text: str) -> float | None:
    block = _clean_text(text)
    decimal = re.search(r"\b([1-9][0-9]{0,4})\s*[,.]\s*([0-9]{1,2})\b", block)
    if decimal:
        return round(float(f"{decimal.group(1)}.{decimal.group(2).ljust(2, '0')}"), 2)

    split = re.search(r"\b([1-9][0-9]{0,3})\s+([0-9]{2})\b", block)
    if split:
        return round(float(split.group(1)) + int(split.group(2)) / 100, 2)

    compact = re.search(r"\b([1-9][0-9]{2,5})\b", block)
    if compact:
        return round(int(compact.group(1)) / 100, 2)

    integer = re.search(r"\b([1-9][0-9]{0,4})\b", block)
    if integer:
        return round(float(integer.group(1)), 2)
    return None


def extract_last_price_like_token(text: str) -> float | None:
    block = _clean_text(text)
    matches: list[tuple[int, int, int, float]] = []

    for match in re.finditer(r"\b([1-9][0-9]{0,4})\s*[,.]\s*([0-9]{1,2})\b", block):
        matches.append((match.end(), match.start(), 4, round(float(f"{match.group(1)}.{match.group(2).ljust(2, '0')}"), 2)))

    for match in re.finditer(r"\b([1-9][0-9]{0,3})\s+([0-9]{2})\b", block):
        matches.append((match.end(), match.start(), 3, round(float(match.group(1)) + int(match.group(2)) / 100, 2)))

    for match in re.finditer(r"\b([1-9][0-9]{3,5})\b", block):
        matches.append((match.end(), match.start(), 2, round(int(match.group(1)) / 100, 2)))

    for match in re.finditer(r"\b([1-9][0-9]{0,4})\b", block):
        matches.append((match.end(), match.start(), 1, round(float(match.group(1)), 2)))

    if not matches:
        return None
    return sorted(matches, key=lambda item: (item[0], item[2], item[1]))[-1][3]


def _decimal(value: str) -> float | None:
    try:
        return round(float(re.sub(r"\s+", "", value).replace(",", ".")), 2)
    except (TypeError, ValueError):
        return None


def _clean_text(text: str) -> str:
    cleaned = unescape(str(text or ""))
    cleaned = cleaned.replace("\xa0", " ")
    cleaned = cleaned.replace("\u00a0", " ")
    cleaned = cleaned.replace("\u0432\u201a\u00ac", "\u20ac")
    cleaned = cleaned.replace("\u00e2\u201a\u00ac", "\u20ac")
    return re.sub(r"\s+", " ", cleaned).strip()


async def _safe_inner_text(page, selector: str) -> str:
    try:
        return await page.locator(selector).inner_text(timeout=5000)
    except Exception:
        return ""


async def _safe_title(page) -> str:
    try:
        return " ".join((await page.title()).split())
    except Exception:
        return ""


async def _safe_close(page) -> None:
    try:
        await page.close()
    except Exception:
        pass


def _abs(url: str | None) -> str | None:
    if not url:
        return None
    if url.startswith("//"):
        return "https:" + url
    return urljoin(BASE_URL, url)


def _is_product_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {"praktis.bg", "www.praktis.bg"}:
        return False
    path = parsed.path.strip("/")
    if not path:
        return False
    if path in {"bg", "en"}:
        return False
    low = url.lower()
    if any(part in low for part in ("/catalogsearch/", "/customer/", "/checkout/", "/wishlist/", "/catalog/category/")):
        return False
    if re.search(r"\.(?:jpg|jpeg|png|webp|svg|css|js|ico)(?:\?|$)", low):
        return False
    return True


def _is_search_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.netloc.lower() in {"praktis.bg", "www.praktis.bg"} and parsed.path.rstrip("/") == "/catalogsearch/result"
