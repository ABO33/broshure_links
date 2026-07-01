from __future__ import annotations

import asyncio
from html import unescape
import logging
import os
from pathlib import Path
from random import uniform
import re
import tempfile
from urllib.parse import quote, urljoin, urlparse

from .logging_config import configure_logging


BASE_URL = "https://praktis.bg"
SEARCH_URL_TEMPLATE = BASE_URL + "/catalogsearch/result?q={}"
SKU_RE = re.compile(r"\b\d{5,12}\b")
DEFAULT_PROFILE_DIR = Path(__file__).resolve().parents[1] / ".praktis-browser-profile"
CONCURRENCY = max(1, int(os.environ.get("PRAKTIS_CONCURRENCY", "4")))
SEARCH_TIMEOUT_MS = int(os.environ.get("PRAKTIS_TIMEOUT_MS", "30000"))
PAGE_WAIT_MS = int(os.environ.get("PRAKTIS_PAGE_WAIT_MS", "900"))
SKU_DELAY_MIN_MS = int(os.environ.get("PRAKTIS_SKU_DELAY_MIN_MS", "200"))
SKU_DELAY_MAX_MS = int(os.environ.get("PRAKTIS_SKU_DELAY_MAX_MS", "900"))
GROUP_DELAY_MIN_MS = int(os.environ.get("PRAKTIS_GROUP_DELAY_MIN_MS", "150"))
GROUP_DELAY_MAX_MS = int(os.environ.get("PRAKTIS_GROUP_DELAY_MAX_MS", "600"))
PROFILE_DIR = os.environ.get("PRAKTIS_PROFILE", str(DEFAULT_PROFILE_DIR))
CHROME_CHANNEL = os.environ.get("PRAKTIS_CHANNEL", "chrome")
HEADLESS = os.environ.get("PRAKTIS_HEADLESS", "1").lower() in {"1", "true", "yes"}
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
logger = logging.getLogger(__name__)
_PROFILE_LAUNCH_FAILED = False

try:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright
except Exception:  # pragma: no cover - exercised when dependency is missing.
    PlaywrightTimeoutError = TimeoutError
    async_playwright = None


def compare_prices_with_playwright(skus: list[str]) -> dict[str, dict]:
    configure_logging()
    unique_skus = sorted({str(sku).strip() for sku in skus if str(sku).strip()})
    if not unique_skus:
        return {}
    logger.info("Starting Praktis browser SKU lookup: skus=%s", len(unique_skus))

    if async_playwright is None:
        logger.error("Playwright is unavailable for Praktis SKU lookup")
        return {
            sku: {
                "sku": sku,
                "status": "error",
                "url": SEARCH_URL_TEMPLATE.format(quote(sku)),
                "title": f"Praktis search for {sku}",
                "price_status": "playwright_unavailable",
                "price_message": "Install Playwright to use Praktis browser price checks.",
            }
            for sku in unique_skus
        }

    try:
        results = asyncio.run(_run_batch(unique_skus))
    except Exception as exc:
        message = f"Playwright price lookup failed: {exc}"
        logger.exception("Praktis browser SKU lookup failed")
        return {
            sku: {
                "sku": sku,
                "status": "error",
                "url": SEARCH_URL_TEMPLATE.format(quote(sku)),
                "title": f"Praktis search for {sku}",
                "price_status": "error",
                "price_message": message[:300],
            }
            for sku in unique_skus
        }

    statuses: dict[str, int] = {}
    for result in results:
        statuses[result.get("status", "unknown")] = statuses.get(result.get("status", "unknown"), 0) + 1
    logger.info("Finished Praktis browser SKU lookup: %s", statuses)
    return {result["sku"]: result for result in results}


def count_search_results_with_playwright(urls: list[str] | dict[str, list[str]]) -> dict[str, dict]:
    configure_logging()
    if isinstance(urls, dict):
        requests = [
            (str(url).strip(), [str(sku).strip() for sku in skus if str(sku).strip()])
            for url, skus in urls.items()
            if str(url).strip()
        ]
    else:
        requests = [(str(url).strip(), []) for url in urls if str(url).strip()]
    seen_urls: set[str] = set()
    requests = [
        request
        for request in requests
        if not (request[0] in seen_urls or seen_urls.add(request[0]))
    ]
    unique_urls = [url for url, _skus in requests]
    if not unique_urls:
        return {}
    logger.info("Starting grouped Praktis search validation: urls=%s", len(unique_urls))

    if async_playwright is None:
        logger.error("Playwright is unavailable for grouped Praktis validation")
        return {
            url: {
                "count_status": "playwright_unavailable",
                "count_message": "Install Playwright to validate grouped Praktis search result counts.",
            }
            for url in unique_urls
        }

    try:
        results = asyncio.run(_run_count_batch(requests))
    except Exception as exc:
        message = f"Playwright grouped search count failed: {exc}"
        logger.exception("Grouped Praktis search validation failed")
        return {
            url: {
                "count_status": "error",
                "count_message": message[:300],
            }
            for url in unique_urls
        }

    statuses: dict[str, int] = {}
    for result in results:
        statuses[result.get("count_status", "unknown")] = statuses.get(result.get("count_status", "unknown"), 0) + 1
    logger.info("Finished grouped Praktis search validation: %s", statuses)
    return {result["url"]: result for result in results}


async def _run_batch(skus: list[str]) -> list[dict]:
    sem = asyncio.Semaphore(CONCURRENCY)
    Path(PROFILE_DIR).mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        context, temp_profile = await _launch_browser_context(p)

        await _warm_session(context)
        tasks = [asyncio.create_task(_scrape_one_sku(context, sku, sem)) for sku in skus]
        try:
            return await asyncio.gather(*tasks)
        finally:
            await context.close()
            if temp_profile:
                temp_profile.cleanup()


async def _run_count_batch(requests: list[tuple[str, list[str]]]) -> list[dict]:
    sem = asyncio.Semaphore(CONCURRENCY)
    Path(PROFILE_DIR).mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        context, temp_profile = await _launch_browser_context(p)

        await _warm_session(context)
        tasks = [
            asyncio.create_task(_count_one_search_url(context, url, expected_skus, sem))
            for url, expected_skus in requests
        ]
        try:
            return await asyncio.gather(*tasks)
        finally:
            await context.close()
            if temp_profile:
                temp_profile.cleanup()


async def _launch_browser_context(p):
    global _PROFILE_LAUNCH_FAILED
    if _PROFILE_LAUNCH_FAILED:
        temp_profile = tempfile.TemporaryDirectory(prefix="praktis-profile-")
        try:
            return await _launch_persistent_context(p, temp_profile.name), temp_profile
        except Exception:
            temp_profile.cleanup()
            raise

    try:
        return await _launch_persistent_context(p, PROFILE_DIR), None
    except Exception as exc:
        _PROFILE_LAUNCH_FAILED = True
        logger.warning(
            "Could not launch Praktis persistent browser profile at %s; retrying with temporary profile: %s",
            PROFILE_DIR,
            _short_error(exc),
        )
        temp_profile = tempfile.TemporaryDirectory(prefix="praktis-profile-")
        try:
            return await _launch_persistent_context(p, temp_profile.name), temp_profile
        except Exception:
            temp_profile.cleanup()
            raise


async def _launch_persistent_context(p, user_data_dir: str):
    return await p.chromium.launch_persistent_context(
        user_data_dir=user_data_dir,
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


async def _polite_delay(min_ms: int, max_ms: int) -> None:
    low = max(0, min(min_ms, max_ms))
    high = max(low, max(min_ms, max_ms))
    if high <= 0:
        return
    await asyncio.sleep(uniform(low, high) / 1000)


async def _scrape_one_sku(context, sku: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        await _polite_delay(SKU_DELAY_MIN_MS, SKU_DELAY_MAX_MS)
        page = await context.new_page()
        await _prepare_page(page)
        search_url = SEARCH_URL_TEMPLATE.format(quote(sku))

        try:
            response = await page.goto(search_url, wait_until="domcontentloaded", timeout=SEARCH_TIMEOUT_MS)
            status_code = response.status if response else None
            await _accept_cookies_if_visible(page)
            await page.wait_for_timeout(PAGE_WAIT_MS)

            if await _looks_blocked(page, status_code):
                logger.warning("Praktis lookup blocked: sku=%s status=%s", sku, status_code)
                return {
                    "sku": sku,
                    "status": "blocked",
                    "source": "playwright",
                    "url": search_url,
                    "title": f"Praktis search for {sku}",
                    "price_status": "blocked",
                    "price_message": "Praktis/Cloudflare blocked the browser price lookup.",
                }

            current_url = page.url or search_url
            if _is_product_url(current_url) and await _page_has_sku(page, sku):
                logger.info("Praktis SKU linked directly: sku=%s", sku)
                return _success_result(
                    sku=sku,
                    url=current_url,
                    title=await _extract_product_title(page),
                    price=await _extract_price_from_page(page),
                    used_search_price=False,
                )

            verified = await _find_verified_product_on_search_page(context, page, sku)
            if verified:
                logger.info(
                    "Praktis SKU linked from search result: sku=%s source=%s",
                    sku,
                    verified.get("source", "detail"),
                )
                return _success_result(
                    sku=sku,
                    url=verified["url"],
                    title=verified.get("title", ""),
                    price=verified.get("price"),
                    used_search_price=verified.get("source") == "search_card",
                )

            body_text = await _safe_inner_text(page, "body")
            if sku in body_text:
                logger.info("Praktis SKU found only on search page: sku=%s", sku)
                return _success_result(
                    sku=sku,
                    url=search_url,
                    title=await _extract_product_title(page) or f"Praktis search for {sku}",
                    price=None,
                    used_search_price=True,
                )

            logger.warning("Praktis SKU not found: sku=%s", sku)
            return {
                "sku": sku,
                "status": "not_found",
                "source": "playwright",
                "url": search_url,
                "title": f"Praktis search for {sku}",
                "price_status": "not_found",
                "price_message": "No exact Praktis result or euro price was found; SKU search link was used.",
            }

        except PlaywrightTimeoutError as exc:
            logger.warning("Praktis SKU lookup timed out: sku=%s error=%s", sku, exc)
            return {
                "sku": sku,
                "status": "error",
                "source": "playwright",
                "url": search_url,
                "title": f"Praktis search for {sku}",
                "price_status": "error",
                "price_message": f"Praktis lookup timed out; SKU search link was used. {exc}"[:300],
            }
        except Exception as exc:
            logger.exception("Praktis SKU lookup error: sku=%s", sku)
            return {
                "sku": sku,
                "status": "error",
                "source": "playwright",
                "url": search_url,
                "title": f"Praktis search for {sku}",
                "price_status": "error",
                "price_message": f"Praktis lookup error; SKU search link was used. {exc!r}"[:300],
            }
        finally:
            await _safe_close(page)


async def _count_one_search_url(context, url: str, expected_skus: list[str], sem: asyncio.Semaphore) -> dict:
    async with sem:
        await _polite_delay(GROUP_DELAY_MIN_MS, GROUP_DELAY_MAX_MS)
        page = await context.new_page()
        await _prepare_page(page)

        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=SEARCH_TIMEOUT_MS)
            status_code = response.status if response else None
            await _accept_cookies_if_visible(page)
            await page.wait_for_timeout(PAGE_WAIT_MS)

            if await _looks_blocked(page, status_code):
                logger.warning("Grouped Praktis validation blocked: url=%s status=%s", url, status_code)
                return {
                    "url": url,
                    "count_status": "blocked",
                    "count_message": "Praktis/Cloudflare blocked grouped search validation.",
                }

            return await _validate_grouped_search_page(context, page, url, expected_skus)
        except PlaywrightTimeoutError as exc:
            logger.warning("Grouped Praktis validation timed out: url=%s error=%s", url, exc)
            return {
                "url": url,
                "count_status": "error",
                "count_message": f"Grouped search validation timed out: {exc}"[:300],
            }
        except Exception as exc:
            logger.exception("Grouped Praktis validation error: url=%s", url)
            return {
                "url": url,
                "count_status": "error",
                "count_message": f"Grouped search validation error: {exc!r}"[:300],
            }
        finally:
            await _safe_close(page)


def _success_result(sku: str, url: str, title: str, price: float | None, used_search_price: bool) -> dict:
    safe_url = url if _is_product_url(url) or _is_search_url(url) else SEARCH_URL_TEMPLATE.format(quote(sku))
    result = {
        "sku": sku,
        "status": "linked" if _is_product_url(safe_url) else "link_not_found",
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


async def _validate_grouped_search_page(context, page, url: str, expected_skus: list[str]) -> dict:
    cards = await _search_result_cards(page)
    card_urls = _product_urls_from_cards(cards)
    product_urls = card_urls or await _product_result_urls(page)
    found_count = len(product_urls)
    expected_count = len(expected_skus)
    if expected_skus and found_count != expected_count:
        logger.warning(
            "Grouped Praktis count mismatch: expected=%s found=%s url=%s",
            expected_count,
            found_count,
            url,
        )
        return {
            "url": url,
            "validated_url": url,
            "count_status": "ok",
            "count_mismatch": True,
            "found_count": found_count,
            "count_delta": found_count - expected_count,
            "found_skus": [],
            "missing_skus": [],
            "extra_skus": [],
            "count_message": "Grouped Praktis search result count does not match the brochure group; SKU checks were skipped.",
        }

    found_skus: set[str] = set()
    expected = set(expected_skus)

    found_skus.update(_extract_skus_from_cards(cards))

    if not found_skus:
        for product_url in product_urls[: max(len(expected_skus) + 8, 16)]:
            product_page = await context.new_page()
            await _prepare_page(product_page)
            try:
                await product_page.goto(product_url, wait_until="domcontentloaded", timeout=SEARCH_TIMEOUT_MS)
                await _accept_cookies_if_visible(product_page)
                await product_page.wait_for_timeout(600)
                text = await _safe_inner_text(product_page, "body")
                found_skus.update(_extract_product_skus_from_text(text, expected))
            except Exception:
                continue
            finally:
                await _safe_close(product_page)

    if expected_skus and not found_skus and product_urls:
        return {
            "url": url,
            "validated_url": url,
            "count_status": "sku_unknown",
            "found_count": len(product_urls),
            "found_skus": [],
            "missing_skus": expected_skus,
            "extra_skus": [],
            "count_message": "Grouped search products loaded, but product SKUs could not be extracted.",
        }

    missing = sorted(expected - found_skus, key=expected_skus.index) if expected_skus else []
    extra = sorted(found_skus - expected) if expected_skus else []
    count_delta = len(product_urls) - len(expected_skus) if expected_skus else 0
    logger.info(
        "Grouped Praktis SKU validation finished: expected=%s foundCount=%s missing=%s extra=%s",
        len(expected_skus),
        len(product_urls),
        len(missing),
        len(extra),
    )
    return {
        "url": url,
        "validated_url": url,
        "count_status": "ok",
        "found_count": len(product_urls),
        "count_delta": count_delta,
        "found_skus": sorted(found_skus),
        "missing_skus": missing,
        "extra_skus": extra,
        "count_message": f"Grouped Praktis search returned {len(product_urls)} product results.",
    }


async def _find_verified_product_on_search_page(context, page, sku: str) -> dict | None:
    cards = await _search_result_cards(page)
    for card in cards:
        text = str(card.get("text") or "")
        data_name = str(card.get("dataName") or "")
        url = _abs(str(card.get("href") or ""))
        if sku not in f"{data_name} {text}" or not url or not _is_product_url(url):
            continue
        title = _clean_product_title(str(card.get("title") or "")) or _extract_card_title(text)
        price = card.get("price")
        if title or price is not None:
            return {
                "url": url,
                "title": title or _title_from_product_url(url),
                "price": price,
                "source": "search_card",
            }
        detail = await _load_product_detail(context, url, sku=None)
        if detail:
            return detail

    product_urls = (await _product_result_urls(page))[:24]
    for url in product_urls:
        detail = await _load_product_detail(context, url, sku=sku)
        if detail:
            return detail
    single_result = single_product_search_result(cards, product_urls)
    if single_result:
        detail = await _load_product_detail(context, single_result["url"], sku=None)
        if detail:
            logger.info("Using single Praktis search result as SKU match: sku=%s url=%s", sku, detail.get("url"))
            return {
                **detail,
                "source": "single_search_result",
            }
        logger.info("Using single Praktis search card as SKU match: sku=%s url=%s", sku, single_result["url"])
        return single_result
    return None


def single_product_search_result(cards: list[dict], product_urls: list[str]) -> dict | None:
    urls = []
    seen: set[str] = set()
    for url in product_urls:
        normalized = url.split("#", 1)[0]
        if normalized in seen:
            continue
        seen.add(normalized)
        urls.append(normalized)
    if len(urls) != 1:
        return None

    url = urls[0]
    for card in cards:
        card_url = _abs(str(card.get("href") or "")).split("#", 1)[0]
        if card_url != url:
            continue
        title = _clean_product_title(str(card.get("title") or "")) or _extract_card_title(str(card.get("text") or ""))
        return {
            "url": url,
            "title": title or _title_from_product_url(url),
            "price": card.get("price"),
            "source": "single_search_result",
        }
    return {
        "url": url,
        "title": _title_from_product_url(url),
        "price": None,
        "source": "single_search_result",
    }


async def _load_product_detail(context, url: str, sku: str | None) -> dict | None:
    product_page = await context.new_page()
    await _prepare_page(product_page)
    try:
        await product_page.goto(url, wait_until="domcontentloaded", timeout=SEARCH_TIMEOUT_MS)
        await _accept_cookies_if_visible(product_page)
        await product_page.wait_for_timeout(PAGE_WAIT_MS)
        if sku and not await _page_has_sku(product_page, sku):
            return None
        return {
            "url": product_page.url or url,
            "title": await _extract_product_title(product_page),
            "price": await _extract_price_from_page(product_page),
        }
    except Exception:
        return None
    finally:
        await _safe_close(product_page)


async def _page_has_sku(page, sku: str) -> bool:
    body = await _safe_inner_text(page, "body")
    if re.search(rf"\b{re.escape(sku)}\b", body):
        return True
    html = await _safe_content(page)
    return bool(re.search(rf"\b{re.escape(sku)}\b", html))


def _extract_skus_from_cards(cards: list[dict]) -> set[str]:
    found: set[str] = set()
    for card in cards:
        data_name = _clean_text(str(card.get("dataName") or ""))
        found.update(SKU_RE.findall(data_name))
        if data_name:
            continue
        text = _clean_text(str(card.get("text") or ""))
        found.update(SKU_RE.findall(text))
    return found


def _product_urls_from_cards(cards: list[dict]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for card in cards:
        url = _abs(str(card.get("href") or ""))
        if not url or not _is_product_url(url):
            continue
        normalized = url.split("#", 1)[0]
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


async def _search_result_cards(page) -> list[dict]:
    selectors = [
        "article[data-name^='pc:root:']",
        "li.product-item",
        ".product-item-info",
        ".product-item",
        ".products-grid .item",
    ]
    cards: list[dict] = []
    seen: set[tuple[str, str]] = set()
    script = """
    els => els.slice(0, 80).map(el => {
        const link =
            el.querySelector('a.product-item-link') ||
            el.querySelector('.product-item-name a') ||
            el.querySelector('a.cursor-pointer[href]') ||
            el.querySelector('a[href]');
        return {
            text: el.innerText || el.textContent || '',
            href: link && link.href ? link.href : '',
            title: link ? ((link.innerText || link.textContent || '').trim()) : '',
            dataName: el.getAttribute('data-name') || ''
        };
    })
    """
    for selector in selectors:
        try:
            batch = await page.locator(selector).evaluate_all(script)
        except Exception:
            batch = []
        for item in batch:
            href = str(item.get("href") or "")
            text = _clean_text(str(item.get("text") or ""))
            data_name = str(item.get("dataName") or "")
            title = _clean_product_title(str(item.get("title") or "")) or _extract_card_title(text)
            price = extract_euro_price_from_text(text)
            key = (data_name, href, text[:120])
            if key in seen:
                continue
            seen.add(key)
            cards.append({"href": href, "text": text, "title": title, "price": price, "dataName": data_name})
    return cards


async def _product_result_urls(page) -> list[str]:
    selectors = [
        "article[data-name^='pc:root:'] a[href]",
        "a.product-item-link",
        ".products-grid a[href]",
        ".product-items a[href]",
        "li.product-item a[href]",
        ".product-item a[href]",
    ]
    links: list[str] = []
    for selector in selectors:
        try:
            batch = await page.locator(selector).evaluate_all("els => els.map(a => a.href).filter(Boolean)")
        except Exception:
            batch = []
        links.extend(str(link) for link in batch)

    out: list[str] = []
    seen: set[str] = set()
    for link in links:
        candidate = _abs(link)
        if not candidate or not _is_product_url(candidate):
            continue
        normalized = candidate.split("#", 1)[0]
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def _extract_card_title(text: str) -> str:
    skip_exact = {
        "\u041a\u0443\u043f\u0438 \u0438\u0437\u0433\u043e\u0434\u043d\u043e",
        "\u0414\u043e\u0431\u0430\u0432\u0438 \u0432 \u043b\u044e\u0431\u0438\u043c\u0438",
        "\u0412\u0438\u0436 \u0434\u0435\u0442\u0430\u0439\u043b\u0438",
        "\u041f\u0440\u043e\u0434\u0443\u043a\u0442 \u043e\u0442 \u0431\u0440\u043e\u0448\u0443\u0440\u0430",
    }
    for raw_line in str(text or "").splitlines():
        line = _clean_product_title(raw_line)
        if not line or line in skip_exact:
            continue
        if re.fullmatch(r"\d+(?:[.,]\d+)?", line):
            continue
        if re.search(r"\u20ac|\u043b\u0432|bgn|eur", line, re.I):
            continue
        if len(line) < 4:
            continue
        return line
    return ""


def _extract_product_skus_from_text(text: str, expected: set[str]) -> set[str]:
    compact = _clean_text(text)
    found: set[str] = set()
    for match in re.finditer(
        r"(?:prod\.?\s*code|product\s*code|\u043a\u043e\u0434|\u0430\u0440\u0442\.?\s*\.?)\D{0,35}(\d{5,12})",
        compact,
        re.I,
    ):
        found.add(match.group(1))
    if expected and not found and re.search(r"prod\.?\s*code|product\s*code|\u043a\u043e\u0434|\u0430\u0440\u0442", compact, re.I):
        found.update(expected.intersection(SKU_RE.findall(compact)))
    return found


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


async def _extract_product_title(page) -> str:
    selectors = [
        "h1.page-title span",
        "h1.page-title",
        ".product-info-main h1",
        ".page-title span",
        "h1",
    ]
    for selector in selectors:
        try:
            text = await page.locator(selector).first.inner_text(timeout=2500)
        except Exception:
            text = ""
        title = _clean_product_title(text)
        if title:
            return title

    for selector in ("meta[property='og:title']", "meta[name='title']"):
        try:
            text = await page.locator(selector).first.get_attribute("content", timeout=1500)
        except Exception:
            text = ""
        title = _clean_product_title(text or "")
        if title:
            return title

    title = _clean_product_title(await _safe_title(page))
    if title:
        return title
    return _title_from_product_url(page.url)


def _clean_product_title(value: str) -> str:
    title = _clean_text(value)
    title = re.sub(r"\s+\|\s+.*$", "", title).strip()
    title = re.sub(r"\s+-\s+PRAKTIS.*$", "", title, flags=re.I).strip()
    title = re.sub(r"^PRAKTIS,\s*DIY\s*Store\s*-?\s*", "", title, flags=re.I).strip()
    title = re.sub(r"\s+", " ", title).strip(" -|")
    if not title or title.lower() in {"praktis", "praktis, diy store"}:
        return ""
    if title.lower().startswith("praktis search for "):
        return ""
    return title


def _title_from_product_url(url: str | None) -> str:
    if not _is_product_url(url):
        return ""
    slug = urlparse(str(url)).path.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"[-_]+", " ", slug)
    slug = re.sub(r"\s+", " ", slug).strip()
    return slug


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
            if (
                price is not None
                and not re.search(r"\b(?:bgn|lv)\b|\u043b\u0432", _clean_text(block), re.I)
                and _has_price_shape(block)
            ):
                return price

    for selector in ("main", "body"):
        text = await _safe_inner_text(page, selector)
        price = extract_euro_price_from_text(text)
        if price is not None:
            return price
    return None


def _has_price_shape(text: str) -> bool:
    block = _clean_text(text)
    return bool(
        re.search(r"\b\d{1,5}\s*[,.]\s*\d{1,2}\b", block)
        or re.search(r"\b\d{1,4}\s+\d{2}\b", block)
        or re.search(r"\b\d{3,5}\b", block)
        or re.search(r"\u20ac|eur|\u0435\u0432\u0440\u043e", block, re.I)
    )


async def _count_product_results(page) -> int:
    if _is_product_url(page.url):
        return 1

    selectors = [
        "a.product-item-link",
        ".products-grid a[href]",
        ".product-items a[href]",
        "li.product-item a[href]",
        ".product-item a[href]",
    ]
    links: list[str] = []
    for selector in selectors:
        try:
            batch = await page.locator(selector).evaluate_all("els => els.map(a => a.href).filter(Boolean)")
        except Exception:
            batch = []
        links.extend(str(link) for link in batch)

    unique_product_urls = {
        candidate
        for candidate in (_abs(link) for link in links)
        if candidate and _is_product_url(candidate)
    }
    if unique_product_urls:
        return len(unique_product_urls)

    for selector in (".product-item-info", "li.product-item", ".product-item"):
        try:
            count = await page.locator(selector).count()
        except Exception:
            count = 0
        if count:
            return count
    return 0


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
    decimal = re.search(r"\b(0|[1-9][0-9]{0,4})\s*[,.]\s*([0-9]{1,2})\b", block)
    if decimal:
        return round(float(f"{decimal.group(1)}.{decimal.group(2).ljust(2, '0')}"), 2)

    split = re.search(r"\b(0|[1-9][0-9]{0,3})\s+([0-9]{2})\b", block)
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

    for match in re.finditer(r"\b(0|[1-9][0-9]{0,4})\s*[,.]\s*([0-9]{1,2})\b", block):
        matches.append((match.end(), match.start(), 4, round(float(f"{match.group(1)}.{match.group(2).ljust(2, '0')}"), 2)))

    for match in re.finditer(r"\b(0|[1-9][0-9]{0,3})\s+([0-9]{2})\b", block):
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


def _short_error(exc: Exception, limit: int = 180) -> str:
    first_line = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
    return first_line[:limit]


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


async def _safe_content(page) -> str:
    try:
        return await page.content()
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
    last_segment = path.rsplit("/", 1)[-1]
    if "-" not in last_segment and not re.search(r"\d", last_segment):
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
