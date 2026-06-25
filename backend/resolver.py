from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen
import re

from .praktis_playwright import extract_euro_price_from_text


BASE_URL = "https://praktis.bg"
SEARCH_URL_TEMPLATE = "https://praktis.bg/catalogsearch/result?q={}"
PRODUCT_CODE_LABELS = ("prod", "code", "\u043f\u0440\u043e\u0434", "\u043a\u043e\u0434")


class LinkExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[str] = []
        self._title = ""
        self._in_title = False
        self._h1_depth = 0
        self.h1_text = ""

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "a" and attrs_dict.get("href"):
            self.links.append(attrs_dict["href"])
        elif tag == "title":
            self._in_title = True
        elif tag == "h1":
            self._h1_depth += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "h1" and self._h1_depth:
            self._h1_depth -= 1

    def handle_data(self, data):
        if self._in_title:
            self._title += data
        if self._h1_depth:
            self.h1_text += data

    @property
    def title(self):
        return " ".join((self.h1_text or self._title).split())


def search_url_for_sku(sku: str) -> str:
    return SEARCH_URL_TEMPLATE.format(quote(sku))


def is_blocked_page(html: str, status: int = 200) -> bool:
    return status in {403, 429} or bool(
        re.search(r"cf_chl_|challenge-platform|Enable JavaScript and cookies|Just a moment", html, re.I)
    )


def product_code_matches(html: str, sku: str) -> bool:
    compact = " ".join(html.split())
    escaped = re.escape(sku)
    strict = re.search(
        rf"(prod\.?\s*code|\u043f\u0440\u043e\u0434\.?\s*\u043a\u043e\u0434)\s*:?\s*{escaped}\b",
        compact,
        re.I,
    )
    lowered = compact.lower()
    loose = re.search(rf"\b{escaped}\b", compact) and any(label in lowered for label in PRODUCT_CODE_LABELS)
    return bool(strict or loose)


def fetch_html(url: str) -> tuple[str, str, int]:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "bg-BG,bg;q=0.9,en;q=0.8",
        },
    )
    try:
        with urlopen(request, timeout=15) as response:
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace"), response.geturl(), response.status
    except HTTPError as exc:
        raw = exc.read()
        charset = exc.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace"), exc.geturl(), exc.code


def extract_product_candidates(html: str, source_url: str) -> list[str]:
    parser = LinkExtractor()
    parser.feed(html)
    urls: list[str] = []
    seen: set[str] = set()

    for href in parser.links:
        url = urljoin(source_url, href)
        if not url.startswith(BASE_URL):
            continue
        if re.search(r"/(cart|checkout|login|register|wishlist|compare|contacts|stores)(/|$)", url):
            continue
        if re.search(r"\.(png|jpe?g|webp|svg|css|js|ico)(\?|$)", url, re.I):
            continue
        normalized = url.split("#", 1)[0]
        if normalized not in seen:
            seen.add(normalized)
            urls.append(normalized)

    return urls[:8]


def resolve_skus(skus: list[str], mapping: dict[str, dict], live_lookup: bool, fallback_search: bool) -> dict[str, dict]:
    resolved: dict[str, dict] = {}

    for sku in skus:
        if sku in mapping:
            resolved[sku] = {
                **mapping[sku],
                "status": "mapped",
                "source": "mapping",
            }
            continue

        if live_lookup:
            resolved[sku] = resolve_live(sku)
        else:
            resolved[sku] = {
                "sku": sku,
                "status": "unresolved",
                "message": "No mapping entry and live lookup is off.",
            }

        if fallback_search and not resolved[sku].get("url"):
            resolved[sku] = {
                **resolved[sku],
                "sku": sku,
                "status": "search_only",
                "url": search_url_for_sku(sku),
                "title": f"Praktis search for {sku}",
                "source": "search-fallback",
            }

    return resolved


def resolve_live(sku: str) -> dict:
    search_url = search_url_for_sku(sku)
    blocked = False

    try:
        html, final_url, status = fetch_html(search_url)
    except Exception as exc:
        return {"sku": sku, "status": "error", "message": str(exc)}

    if is_blocked_page(html, status):
        return {"sku": sku, "status": "blocked", "message": "Praktis blocked automated lookup."}

    if product_code_matches(html, sku):
        parser = LinkExtractor()
        parser.feed(html)
        return {
            "sku": sku,
            "status": "linked",
            "url": final_url,
            "title": parser.title,
            "source": "live",
            "website_price": extract_price_from_html(html),
        }

    for candidate_url in extract_product_candidates(html, final_url):
        try:
            detail_html, detail_url, detail_status = fetch_html(candidate_url)
        except Exception:
            continue

        if is_blocked_page(detail_html, detail_status):
            blocked = True
            continue

        if product_code_matches(detail_html, sku):
            parser = LinkExtractor()
            parser.feed(detail_html)
            return {
                "sku": sku,
                "status": "linked",
                "url": detail_url,
                "title": parser.title,
                "source": "live",
                "website_price": extract_price_from_html(detail_html),
            }

    if blocked:
        return {"sku": sku, "status": "blocked", "message": "Praktis blocked detail page lookup."}

    return {"sku": sku, "status": "unresolved", "message": "No exact product page was verified."}


def compare_website_price(resolved: dict) -> dict:
    url = resolved.get("url")
    if not url:
        return {"price_status": "no_url", "price_message": "No exact product URL is available."}

    if resolved.get("website_price") is not None:
        return {"website_price": resolved["website_price"], "price_status": "website_price_found"}

    try:
        html, _final_url, status = fetch_html(url)
    except Exception as exc:
        return {"price_status": "error", "price_message": str(exc)}

    if is_blocked_page(html, status):
        return {"price_status": "blocked", "price_message": "Praktis blocked website price lookup."}

    price = extract_price_from_html(html)
    if price is None:
        return {"price_status": "not_found", "price_message": "No website price was found on the page."}

    message = "Website price found."
    if resolved.get("source") == "search-fallback" or "/catalogsearch/result" in url:
        message = "First price on Praktis search result was used."
    return {"website_price": price, "price_status": "website_price_found", "price_message": message}


def extract_price_from_html(html: str) -> float | None:
    euro_price = extract_euro_price_from_text(html)
    if euro_price is not None:
        return euro_price

    patterns = [
        r'"price"\s*:\s*"?([0-9]+(?:[.,][0-9]+)?)',
        r"itemprop=[\"']price[\"'][^>]*content=[\"']([0-9]+(?:[.,][0-9]+)?)",
        r"content=[\"']([0-9]+(?:[.,][0-9]+)?)[\"'][^>]*itemprop=[\"']price[\"']",
        r"([0-9]+(?:[.,][0-9]{2})?)\s*(?:\u043b\u0432|bgn|lv)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.I)
        if match:
            price = parse_decimal_price(match.group(1))
            if price is not None:
                return price
    return None


def parse_decimal_price(value: str) -> float | None:
    cleaned = re.sub(r"\s+", "", value).replace(",", ".")
    try:
        price = float(cleaned)
    except ValueError:
        return None
    return round(price, 2)
