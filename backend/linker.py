from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import logging
import math
import re
from typing import Iterable
from urllib.parse import urlparse

import pdfplumber
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Link
from reportlab.pdfgen import canvas

from .excel_prices import parse_excel_prices
from .grouped_search import make_group_search_url_from_titles
from .logging_config import configure_logging
from .mapping import parse_mapping
from .praktis_playwright import compare_prices_with_playwright, count_search_results_with_playwright
from .resolver import compare_website_price, count_search_results, resolve_skus, search_url_for_sku


@dataclass
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    fragment: bool = False
    comma_primary: bool = False
    original_text: str = ""

    @property
    def width(self) -> float:
        return max(0.1, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.1, self.bottom - self.top)

    @property
    def mid_y(self) -> float:
        return (self.top + self.bottom) / 2


@dataclass
class PageText:
    page_number: int
    width: float
    height: float
    words: list[Word]
    footer_top: float | None = None
    image_blocks: list[dict] | None = None


@dataclass
class SkuExpansion:
    skus: list[str]
    repeated_skus: list[str]


SKU_RE = re.compile(r"\d{5,12}")
SPLIT_SKU_TOKEN_RE = re.compile(r"^[\d,-]+$")
ASCII_DIGITS_RE = re.compile(r"^[0-9]+$")
HEADER_SKU_TEXT_INSET = 4.0
HEADER_PRICE_BOX_PADDING = 6.5
HEADER_PAGE_EDGE_TOLERANCE = 18.0
HEADER_EDGE_TOUCH_TOLERANCE = 6.0
HEADER_PRICE_GROUP_GAP = 68.0
BGN_PER_EUR = 1.95583
MAX_SHORTHAND_RANGE_ITEMS = 60
UNDEFINED_BROCHURE_PRICE_TEXT = "Not defined"
MISSING_BROCHURE_PRICE_TEXT = "?"
logger = logging.getLogger(__name__)


def process_brochure(
    pdf_bytes: bytes,
    pdf_name: str,
    mapping_bytes: bytes | None,
    mapping_name: str,
    options: dict | None = None,
    excel_bytes: bytes | None = None,
    excel_name: str = "",
) -> dict:
    configure_logging()
    options = options or {}
    min_digits = _int_option(options, "minDigits", 5, 5, 12)
    max_digits = _int_option(options, "maxDigits", 12, min_digits, 12)
    box_padding = _float_option(options, "boxPadding", 0, 0, 16)
    mode = str(options.get("mode") or "").strip() or legacy_mode(options)
    link_annotations = mode != "excel_prices"
    website_prices = mode in {"website_links_prices", "full_check"}
    excel_prices_enabled = mode in {"excel_prices", "full_check"}
    live_lookup = bool(options.get("liveLookup")) and website_prices
    fallback_search = mode in {"fallback_links", "website_links_prices", "full_check"} or bool(options.get("fallbackSearch"))
    debug_boxes = bool(options.get("debugBoxes"))
    if excel_prices_enabled and not excel_bytes:
        raise ValueError("Upload an Excel .xlsx file for the selected price check mode.")

    logger.info(
        "Brochure processing started: file=%s mode=%s minDigits=%s maxDigits=%s padding=%s",
        pdf_name,
        mode,
        min_digits,
        max_digits,
        box_padding,
    )
    pages = extract_text_pages(pdf_bytes, min_digits, max_digits)
    logger.info("Extracted readable text from %s PDF pages", len(pages))
    process_pages, page_scope = select_processing_pages(pages, options)
    logger.info(
        "Selected page scope: scope=%s pages=%s",
        page_scope,
        ",".join(str(page.page_number) for page in process_pages),
    )
    detections = detect_boxes(
        process_pages,
        min_digits,
        max_digits,
        box_padding,
        skip_last_page=page_scope == "all",
    )
    logger.info("Detected %s SKU/link boxes", len(detections))
    groups = attach_variant_parent_groups(detections)
    logger.info("Detected %s complex SKU groups", len(groups))
    flag_repeated_page_skus(detections)
    skus = sorted({item["sku"] for item in detections})
    excel_prices = parse_excel_prices(excel_bytes, excel_name) if excel_bytes else {}
    if excel_bytes:
        logger.info("Loaded %s Excel price rows from %s", len(excel_prices), excel_name)
    mapping = parse_mapping(mapping_bytes, mapping_name)
    if mapping_bytes:
        logger.info("Loaded %s manual mapping rows from %s", len(mapping), mapping_name)
    resolved = resolve_skus(skus, mapping, live_lookup, fallback_search)
    logger.info("Resolved initial links for %s unique SKUs", len(skus))
    if website_prices:
        logger.info("Starting website price/link checks for %s detections", len(detections))
        attach_price_comparisons(detections, resolved)
        logger.info("Finished website price/link checks")
    if excel_prices_enabled:
        logger.info("Starting Excel price comparisons")
        attach_excel_comparisons(detections, excel_prices)
        logger.info("Finished Excel price comparisons")
    if website_prices and excel_prices_enabled:
        logger.info("Starting triple price comparisons")
        attach_triple_comparisons(detections)
    else:
        mark_triple_not_checked(detections)
    if mode == "excel_prices":
        mark_price_only_statuses(detections, resolved)
    if fallback_search:
        ensure_search_fallbacks(resolved, skus)
    sanitize_resolved_links(resolved, skus)
    if link_annotations:
        logger.info("Applying grouped search links")
        apply_grouped_search_links(groups, resolved, validate_counts=website_prices)
        logger.info("Finished grouped search links")
    update_from_price_display(detections)
    linked_pdf, linked_count = write_links(pdf_bytes, pages, detections, resolved, debug_boxes, link_annotations)
    logger.info("Wrote %s PDF link annotations", linked_count)

    rows = []
    for item in detections:
        link = resolved.get(item["sku"], {})
        rows.append(
            {
                "page": item["page"],
                "sku": item["sku"],
                "parent_sku": item.get("parent_sku", ""),
                "status": item.get("status") or link.get("status", "unresolved"),
                "box_type": item["box_type"],
                "url": link.get("url", ""),
                "title": link.get("title", ""),
                "message": item.get("message") or link.get("message", ""),
                "brochure_price": item.get("brochure_price"),
                "brochure_price_text": item.get("brochure_price_text", ""),
                "brochure_price_not_defined": bool(item.get("brochure_price_not_defined")),
                "website_price": link.get("website_price"),
                "price_status": item.get("price_status") or link.get("price_status", ""),
                "price_message": item.get("price_message") or link.get("price_message", ""),
                "excel_price": item.get("excel_price"),
                "excel_status": item.get("excel_status", ""),
                "excel_message": item.get("excel_message", ""),
                "triple_status": item.get("triple_status", ""),
                "triple_message": item.get("triple_message", ""),
                "confidence": item["confidence"],
                "box": item["box"],
            }
        )

    linked_skus = {row["sku"] for row in rows if row["url"]}
    blocked = sum(1 for sku in skus if resolved.get(sku, {}).get("status") == "blocked")
    price_compared = sum(1 for row in rows if row.get("price_status") in {"match", "different"})
    price_matched = sum(1 for row in rows if row.get("price_status") == "match")
    price_different = sum(1 for row in rows if row.get("price_status") == "different")
    excel_compared = sum(1 for row in rows if row.get("excel_status") in {"match", "different"})
    excel_matched = sum(1 for row in rows if row.get("excel_status") == "match")
    excel_different = sum(1 for row in rows if row.get("excel_status") == "different")
    triple_compared = sum(1 for row in rows if row.get("triple_status") in {"match", "different"})
    triple_matched = sum(1 for row in rows if row.get("triple_status") == "match")
    triple_different = sum(1 for row in rows if row.get("triple_status") == "different")
    variant_rows = sum(1 for row in rows if row.get("box_type") == "variant")

    logger.info(
        "Brochure processing finished: detections=%s uniqueSkus=%s linkedSkus=%s priceDiffs=%s",
        len(detections),
        len(skus),
        len(linked_skus),
        price_different,
    )
    return {
        "outputFileName": output_name(pdf_name),
        "pdfBase64": _to_base64(linked_pdf),
        "summary": {
            "pages": len(process_pages),
            "totalPages": len(pages),
            "pageScope": page_scope,
            "detections": len(detections),
            "uniqueSkus": len(skus),
            "linkedSkus": len(linked_skus),
            "linkedAnnotations": linked_count,
            "unresolvedSkus": len(skus) - len(linked_skus),
            "blockedLookups": blocked,
            "priceCompared": price_compared,
            "priceMatched": price_matched,
            "priceDifferent": price_different,
            "excelCompared": excel_compared,
            "excelMatched": excel_matched,
            "excelDifferent": excel_different,
            "tripleCompared": triple_compared,
            "tripleMatched": triple_matched,
            "tripleDifferent": triple_different,
            "variantRows": variant_rows,
            "mode": mode,
        },
        "rows": rows,
    }


def legacy_mode(options: dict) -> str:
    if options.get("comparePrices"):
        return "website_links_prices"
    if options.get("fallbackSearch"):
        return "fallback_links"
    return "excel_prices" if options.get("excelPrices") else "fallback_links"


def select_processing_pages(pages: list[PageText], options: dict) -> tuple[list[PageText], str]:
    page_mode = str(options.get("pageMode") or "all").strip().lower()
    if page_mode == "all":
        return pages, "all"

    if page_mode == "single":
        try:
            page_number = int(str(options.get("pageNumber") or "").strip())
        except ValueError:
            raise ValueError("Enter a valid page number to process.")

        if page_number < 1 or page_number > len(pages):
            raise ValueError(f"Page number must be between 1 and {len(pages)}.")

        return [pages[page_number - 1]], "single"

    if page_mode == "range":
        try:
            start = int(str(options.get("pageStart") or "").strip())
            end = int(str(options.get("pageEnd") or "").strip())
        except ValueError:
            raise ValueError("Enter valid start and end page numbers to process.")

        if start < 1 or end < 1 or start > len(pages) or end > len(pages):
            raise ValueError(f"Page range must be between 1 and {len(pages)}.")
        if start > end:
            raise ValueError("The first page in the range must be before or equal to the last page.")

        return pages[start - 1 : end], "range"

    raise ValueError("Choose whether to process the whole file, one page, or a page range.")


def extract_text_pages(pdf_bytes: bytes, min_digits: int = 5, max_digits: int = 12) -> list[PageText]:
    pages: list[PageText] = []
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            raw_words = page.extract_words(x_tolerance=1, y_tolerance=3, keep_blank_chars=False, use_text_flow=False)
            words: list[Word] = []
            base_words: list[Word] = []
            for raw in raw_words:
                text = str(raw.get("text", "")).strip()
                if not text:
                    continue
                base = Word(
                    text=text,
                    x0=float(raw["x0"]),
                    x1=float(raw["x1"]),
                    top=float(raw["top"]),
                    bottom=float(raw["bottom"]),
                    original_text=text,
                )
                base_words.append(base)
                words.extend(expand_sku_fragments(base, min_digits, max_digits))
            words.extend(stitch_split_sku_words(base_words, min_digits, max_digits))
            footer_top = float(page.height) if index == 1 else find_footer_top(page)
            pages.append(
                PageText(
                    index,
                    float(page.width),
                    float(page.height),
                    words,
                    footer_top,
                    find_large_image_blocks(page),
                )
            )
    return pages


def stitch_split_sku_words(words: list[Word], min_digits: int, max_digits: int) -> list[Word]:
    stitched: list[Word] = []
    token_lines = cluster_words_by_top(
        [
            word
            for word in words
            if word.height <= 8 and SPLIT_SKU_TOKEN_RE.fullmatch(word.text)
        ],
        tolerance=2,
    )
    for line in token_lines:
        sequence: list[Word] = []
        previous: Word | None = None
        for word in sorted(line, key=lambda item: item.x0):
            gap = word.x0 - previous.x1 if previous else 0
            if previous and gap > 4:
                stitched.extend(build_stitched_sku(sequence, min_digits, max_digits))
                sequence = []
            sequence.append(word)
            previous = word
        stitched.extend(build_stitched_sku(sequence, min_digits, max_digits))
    return stitched


def build_stitched_sku(sequence: list[Word], min_digits: int, max_digits: int) -> list[Word]:
    if len(sequence) < 2:
        return []
    if is_ascii_digits(sequence[0].text) and min_digits <= len(sequence[0].text) <= max_digits:
        return []
    original = "".join(word.text for word in sequence)
    first = original.split(",", 1)[0].split("-", 1)[0].strip()
    if not is_ascii_digits(first) or not min_digits <= len(first) <= max_digits:
        return []

    x1 = x_at_sequence_char(sequence, len(first))
    return [
        Word(
            text=first,
            x0=sequence[0].x0,
            x1=x1,
            top=min(word.top for word in sequence),
            bottom=max(word.bottom for word in sequence),
            comma_primary=True,
            original_text=original,
        )
    ]


def x_at_sequence_char(sequence: list[Word], char_count: int) -> float:
    seen = 0
    for word in sequence:
        next_seen = seen + len(word.text)
        if char_count <= next_seen:
            inside = max(0, char_count - seen)
            return word.x0 + word.width * (inside / max(1, len(word.text)))
        seen = next_seen
    return sequence[-1].x1


def expand_sku_fragments(word: Word, min_digits: int, max_digits: int) -> list[Word]:
    if "," in word.text:
        first = word.text.split(",", 1)[0].strip()
        first = first.split("-", 1)[0].strip()
        if is_ascii_digits(first) and min_digits <= len(first) <= max_digits:
            ratio = len(first) / max(1, len(word.text))
            return [
                Word(
                    text=first,
                    x0=word.x0,
                    x1=word.x0 + word.width * ratio,
                    top=word.top,
                    bottom=word.bottom,
                    comma_primary=True,
                    original_text=word.text,
                )
            ]
        return [word]

    if "-" in word.text:
        first = word.text.split("-", 1)[0].strip()
        if is_ascii_digits(first) and min_digits <= len(first) <= max_digits:
            ratio = len(first) / max(1, len(word.text))
            return [
                Word(
                    text=first,
                    x0=word.x0,
                    x1=word.x0 + word.width * ratio,
                    top=word.top,
                    bottom=word.bottom,
                    comma_primary=True,
                    original_text=word.text,
                )
            ]

    matches = [match for match in SKU_RE.finditer(word.text) if min_digits <= len(match.group(0)) <= max_digits]
    if not matches or (len(matches) == 1 and word.text == matches[0].group(0)):
        return [word]

    expanded = [word]
    text_len = max(1, len(word.text))
    for match in matches:
        start_ratio = match.start() / text_len
        end_ratio = match.end() / text_len
        expanded.append(
            Word(
                text=match.group(0),
                x0=word.x0 + word.width * start_ratio,
                x1=word.x0 + word.width * end_ratio,
                top=word.top,
                bottom=word.bottom,
                fragment=True,
                original_text=word.text,
            )
        )
    return expanded


def detect_boxes(
    pages: list[PageText],
    min_digits: int,
    max_digits: int,
    box_padding: float,
    skip_last_page: bool = True,
) -> list[dict]:
    detections: list[dict] = []
    product_pages = pages[:-1] if skip_last_page and len(pages) > 1 else pages
    for page in product_pages:
        item_detections = detect_page_boxes(page, min_digits, max_digits, box_padding)
        item_detections, header_variants = expand_header_shorthand_groups(page, item_detections, min_digits, max_digits)
        variant_detections = detect_variant_table_rows(page, item_detections, min_digits, max_digits)
        variant_detections.extend(expand_variant_shorthand_rows(page, variant_detections, min_digits, max_digits))
        detections.extend(item_detections)
        detections.extend(dedupe_detections(header_variants + variant_detections))
    return detections


def expand_header_shorthand_groups(
    page: PageText,
    items: list[dict],
    min_digits: int,
    max_digits: int,
) -> tuple[list[dict], list[dict]]:
    variants: list[dict] = []
    for item in items:
        expression = detection_sku_expression(page, item, header=True)
        if not contains_sku_expression_punctuation(expression):
            continue
        expansion = expand_sku_expression_details(expression, min_digits, max_digits)
        skus = expansion.skus
        if len(skus) < 2:
            continue
        if expansion.repeated_skus:
            flag_sku_illustration_error(item, expression, expansion.repeated_skus)

        first_sku = skus[0]
        old_sku = item["sku"]
        switched_parent = first_sku != old_sku
        if switched_parent:
            logger.info("Switching shorthand parent SKU from %s to %s on page %s", old_sku, first_sku, item["page"])
            item["sku"] = first_sku

        if switched_parent:
            adjust_item_box_to_expression(page, item, expression)
        existing = {item["sku"]}
        if is_missing_brochure_price_detection(item):
            variant_message = "Header shorthand SKU; brochure price is missing."
        elif item.get("brochure_price_from_marker") or item.get("brochure_price_not_defined"):
            variant_message = "Header shorthand SKU; brochure shows a 'from' price so exact brochure price is not defined."
        else:
            variant_message = "Header shorthand SKU; brochure price is the displayed item price."
        for sku in skus:
            if sku in existing:
                continue
            existing.add(sku)
            variants.append(
                shorthand_variant_detection(
                    page,
                    item,
                    sku,
                    item.get("brochure_price_text", ""),
                    item.get("brochure_price"),
                    variant_message,
                )
            )

        logger.info("Expanded header shorthand group: page=%s parent=%s skus=%s", item["page"], item["sku"], ",".join(skus))

    return items, dedupe_detections(variants)


def expand_variant_shorthand_rows(
    page: PageText,
    variants: list[dict],
    min_digits: int,
    max_digits: int,
) -> list[dict]:
    expanded: list[dict] = []
    for variant in variants:
        expression = detection_sku_expression(page, variant, header=False)
        if "," not in expression and "-" not in expression:
            continue
        if not variant_expression_extends_base_sku(expression, variant["sku"]):
            continue
        expansion = expand_sku_expression_details(expression, min_digits, max_digits)
        skus = expansion.skus
        if len(skus) < 2:
            continue
        if expansion.repeated_skus:
            flag_sku_illustration_error(variant, expression, expansion.repeated_skus)
        existing = {variant["sku"]}
        for sku in skus:
            if sku in existing:
                continue
            existing.add(sku)
            expanded.append(
                shorthand_variant_detection(
                    page,
                    variant,
                    sku,
                    variant.get("brochure_price_text", ""),
                    variant.get("brochure_price"),
                    "Table shorthand SKU; brochure price is the displayed row euro price.",
                )
            )
        logger.info(
            "Expanded table shorthand row: page=%s base=%s skus=%s",
            variant["page"],
            variant["sku"],
            ",".join(skus),
        )
    return dedupe_detections(expanded)


def variant_expression_extends_base_sku(expression: str, base_sku: str) -> bool:
    text = str(expression or "").strip()
    sku = str(base_sku or "").strip()
    if not sku or not text.startswith(sku):
        return False
    tail = text[len(sku) :].lstrip()
    return tail.startswith((",", "-"))


def shorthand_variant_detection(
    page: PageText,
    source: dict,
    sku: str,
    price_text: str,
    price: float | None,
    message: str,
) -> dict:
    box = dict(source["box"])
    price_missing = is_missing_brochure_price_text(price_text) or is_missing_brochure_price_detection(source)
    price_not_defined = bool(
        price_missing or source.get("brochure_price_not_defined") or source.get("brochure_price_from_marker")
    )
    display_price_text = (
        MISSING_BROCHURE_PRICE_TEXT
        if price_missing
        else UNDEFINED_BROCHURE_PRICE_TEXT
        if price_not_defined
        else price_text
    )
    return _detection(
        page,
        sku,
        "variant",
        box,
        0.72,
        display_price_text,
        None if price_not_defined else price,
        linkable=False,
        status="",
        message=message,
        brochure_price_not_defined=price_not_defined,
        brochure_price_from_marker=bool(source.get("brochure_price_from_marker")),
        from_price_text=price_text if price_not_defined else "",
        from_price=price if price_not_defined else None,
    )


def detection_sku_expression(page: PageText, item: dict, header: bool) -> str:
    price_left = find_detection_price_left(page, item)
    box = item["box"]
    left = max(0.0, box["x"] - (42 if header else 3))
    right = max(left + 4, price_left - 1.5)
    if not header:
        right = max(left + 4, min(right, find_variant_code_column_right(page, item, price_left)))
    top = max(0.0, box["y"] - 2)
    if header:
        bottom = min(box["y"] + box["height"], max(box["y"] + 28, find_detection_price_bottom(page, item)))
    else:
        bottom = box["y"] + min(box["height"], 12)

    lines = sku_expression_lines(page, left, right, top, bottom)
    return " ".join(lines)


def find_variant_code_column_right(page: PageText, item: dict, price_left: float) -> float:
    sku_word = find_detection_sku_word(page, item)
    if not sku_word:
        return price_left - 1.5

    header_candidates = [
        word
        for word in page.words
        if 4 <= sku_word.top - word.top <= 55
        and word.x0 > sku_word.x1 + 2
        and word.x0 < price_left - 2
        and not is_priceish_text(word.text)
        and not is_euro_header(word.text)
        and not is_leva_header(word.text)
        and not SKU_RE.search(word.text)
    ]
    if not header_candidates:
        return price_left - 1.5

    next_header = min(header_candidates, key=lambda word: (word.x0, abs(sku_word.top - word.top)))
    return max(sku_word.x1 + 4, next_header.x0 - 2)


def find_detection_sku_word(page: PageText, item: dict) -> Word | None:
    box = item["box"]
    sku = item["sku"]
    candidates = [
        word
        for word in page.words
        if word.text == sku
        and box["x"] - 3 <= word.x0 <= box["x"] + box["width"] + 3
        and box["y"] - 3 <= word.top <= box["y"] + box["height"] + 3
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda word: (abs(word.top - box["y"]), word.x0))[0]


def find_detection_price_left(page: PageText, item: dict) -> float:
    match = find_detection_price_word(page, item)
    if match:
        return match.x0
    return item["box"]["x"] + item["box"]["width"]


def find_detection_price_bottom(page: PageText, item: dict) -> float:
    match = find_detection_price_word(page, item)
    if match:
        return match.bottom
    return item["box"]["y"] + 32


def find_detection_price_word(page: PageText, item: dict) -> Word | None:
    price = item.get("brochure_price")
    price_text = str(item.get("brochure_price_text") or "").strip()
    if price is None and not price_text:
        return None

    box = item["box"]
    candidates: list[Word] = []
    top = max(0.0, box["y"] - 3)
    bottom = min(page.height, box["y"] + box["height"] + 3)
    for word in page.words:
        if word.x0 < box["x"] - 4 or word.x0 > box["x"] + box["width"] + 4:
            continue
        if word.top < top or word.bottom > bottom:
            continue
        if price_text and word.text == price_text:
            candidates.append(word)
            continue
        parsed = parse_table_price_word(word.text)
        if parsed is None:
            parsed = brochure_price_to_decimal(word.text)
        if price is not None and parsed is not None and prices_equal(parsed, price):
            candidates.append(word)

    if not candidates:
        return None
    return sorted(candidates, key=lambda word: (word.top, word.x0, -word.height))[0]


def adjust_item_box_to_expression(page: PageText, item: dict, expression: str) -> None:
    if not expression:
        return
    box = item["box"]
    price_left = find_detection_price_left(page, item)
    words = [
        word
        for word in page.words
        if box["y"] - 2 <= word.top <= min(box["y"] + 32, find_detection_price_bottom(page, item) + 1)
        and word.x0 < price_left - 1
        and word.x1 >= box["x"] - 42
        and expression_token_text(word)
    ]
    if not words:
        return
    new_x = clamp(min(word.x0 for word in words) - HEADER_SKU_TEXT_INSET, 0, page.width)
    if new_x >= box["x"]:
        return
    old_right = box["x"] + box["width"]
    box["x"] = new_x
    box["width"] = clamp(old_right - new_x, 8, page.width - new_x)


def sku_expression_lines(page: PageText, left: float, right: float, top: float, bottom: float) -> list[str]:
    words = [
        word
        for word in page.words
        if word.x1 >= left
        and word.x0 <= right
        and top <= word.top <= bottom
        and word.height <= 9
        and expression_token_text(word)
        and not is_euro_header(word.text)
        and not is_leva_header(word.text)
    ]
    lines: list[str] = []
    for line in cluster_words_by_top(words, tolerance=2.3):
        text = sku_expression_line_text(line)
        if text:
            lines.append(text)
    return lines


def sku_expression_line_text(line: list[Word]) -> str:
    selected: list[tuple[float, float, str]] = []
    covered_until = -1.0
    for word in sorted(
        line,
        key=lambda item: (
            item.x0,
            -item.width,
            expression_token_noise_score(expression_token_text(item)),
            -len(expression_token_text(item)),
        ),
    ):
        if word.x1 <= covered_until + 0.3 and is_expression_fragment(word.text):
            continue
        text = expression_token_text(word)
        if not text:
            continue
        selected.append((word.x0, word.x1, text))
        covered_until = max(covered_until, word.x1)
        if should_cover_original_expression(word):
            covered_until = max(covered_until, estimated_original_expression_right(word))
    if not selected:
        return ""

    parts: list[str] = []
    previous_right = None
    for x0, x1, text in sorted(selected, key=lambda item: item[0]):
        if previous_right is not None and x0 - previous_right > 5:
            parts.append(" ")
        parts.append(text)
        previous_right = max(previous_right or x1, x1)
    return "".join(parts)


def expression_token_noise_score(text: str) -> int:
    score = 0
    if re.search(r"\d{2,}\s*-\s*\d{2,}", str(text or "")):
        score += 2
    return score


def expression_token_text(word: Word) -> str:
    original = str(word.original_text or "").strip()
    text = str(word.text or "").strip()
    if text in {",", "-"}:
        return text
    chosen = original if original and (word.comma_primary or contains_sku_expression_punctuation(original)) else text
    chosen = chosen.replace("\u2013", "-").replace("\u2014", "-")
    return chosen if re.search(r"\d", chosen) and re.fullmatch(r"[\d,\-\s]+", chosen) else ""


def contains_sku_expression_punctuation(text: str) -> bool:
    return "," in text or "-" in text or "\u2013" in text or "\u2014" in text


def should_cover_original_expression(word: Word) -> bool:
    original = str(word.original_text or "").strip()
    if not original or original == word.text:
        return False
    stripped = original.strip(" ,-")
    return stripped != word.text


def estimated_original_expression_right(word: Word) -> float:
    original = str(word.original_text or word.text)
    return word.x0 + word.width * (len(original) / max(1, len(word.text))) + 5


def is_expression_fragment(text: str) -> bool:
    return bool(re.fullmatch(r"[\d,\-]+", str(text or "")))


def expand_sku_expression(text: str, min_digits: int, max_digits: int) -> list[str]:
    return expand_sku_expression_details(text, min_digits, max_digits).skus


def expand_sku_expression_details(text: str, min_digits: int, max_digits: int) -> SkuExpansion:
    cleaned = str(text or "").replace("\u2013", "-").replace("\u2014", "-")
    tokens = list(re.finditer(r"\d+\s*-\s*\d+|\d+", cleaned))
    skus: list[str] = []
    repeated_skus: list[str] = []
    anchor: str | None = None
    previous_token = ""
    previous_short_token = False

    for match in tokens:
        token = re.sub(r"\s+", "", match.group(0))
        if token == previous_token and "-" not in token and len(token) < min_digits:
            continue
        previous_token = token
        has_prefix_separator = expression_has_separator_before(cleaned, match.start())
        has_suffix_separator = expression_has_separator_after(cleaned, match.end())
        if "-" in token:
            left, right = token.split("-", 1)
            if len(left) < min_digits and not has_prefix_separator:
                previous_short_token = False
                continue
            start = complete_sku_token(left, anchor, min_digits, max_digits)
            if not start:
                previous_short_token = False
                continue
            end = complete_range_end(start, right, min_digits, max_digits)
            if not end:
                maybe_add_glued_suffix_token(skus, left, anchor, min_digits, max_digits, repeated_skus)
                previous_short_token = False
                continue
            if not add_sku_range(skus, start, end, repeated_skus):
                maybe_add_glued_suffix_token(skus, left, anchor, min_digits, max_digits, repeated_skus)
            if len(left) >= min_digits:
                anchor = start
            previous_short_token = len(left) < min_digits
            continue

        is_short_token = len(token) < min_digits
        if is_short_token and not has_prefix_separator and not (previous_short_token and has_suffix_separator):
            previous_short_token = False
            continue
        sku = complete_sku_token(token, anchor, min_digits, max_digits)
        if not sku:
            previous_short_token = False
            continue
        append_unique(skus, sku, repeated_skus)
        if len(token) >= min_digits:
            anchor = sku
        previous_short_token = is_short_token

    return SkuExpansion(skus=skus, repeated_skus=repeated_skus)


def expression_has_separator_before(text: str, index: int) -> bool:
    prefix = str(text or "")[:index].rstrip()
    return bool(prefix) and prefix[-1] in {",", "-"}


def expression_has_separator_after(text: str, index: int) -> bool:
    suffix = str(text or "")[index:].lstrip()
    return bool(suffix) and suffix[0] in {","}


def maybe_add_glued_suffix_token(
    skus: list[str],
    token: str,
    anchor: str | None,
    min_digits: int,
    max_digits: int,
    repeated_skus: list[str] | None,
) -> bool:
    if not anchor or len(token) <= 1 or len(token) >= min_digits:
        return False
    first_digit = token[:1]
    sku = complete_sku_token(first_digit, anchor, min_digits, max_digits)
    if not sku:
        return False
    append_unique(skus, sku, repeated_skus)
    return True


def flag_sku_illustration_error(item: dict, expression: str, repeated_skus: list[str]) -> None:
    item["status"] = item.get("status") or "sku_illustration_error"
    repeated = ", ".join(repeated_skus[:8])
    if len(repeated_skus) > 8:
        repeated = f"{repeated}, ..."
    message = f"SKU illustration error: shorthand expression repeats or overlaps SKU(s) {repeated}."
    item["message"] = append_item_message(item.get("message", ""), message)
    logger.warning(
        "SKU illustration shorthand overlap: page=%s sku=%s repeated=%s expression=%s",
        item.get("page"),
        item.get("sku"),
        ",".join(repeated_skus),
        expression,
    )


def complete_sku_token(token: str, anchor: str | None, min_digits: int, max_digits: int) -> str | None:
    token = str(token or "").strip()
    if not is_ascii_digits(token):
        return None
    if min_digits <= len(token) <= max_digits:
        return token
    if not anchor or len(token) >= len(anchor):
        return None
    sku = anchor[: len(anchor) - len(token)] + token
    return sku if min_digits <= len(sku) <= max_digits else None


def complete_range_end(start: str, end_token: str, min_digits: int, max_digits: int) -> str | None:
    end_token = str(end_token or "").strip()
    if not is_ascii_digits(end_token):
        return None
    if min_digits <= len(end_token) <= max_digits:
        return end_token
    if len(end_token) >= len(start):
        return None
    sku = start[: len(start) - len(end_token)] + end_token
    return sku if min_digits <= len(sku) <= max_digits else None


def add_sku_range(skus: list[str], start: str, end: str, repeated_skus: list[str] | None = None) -> bool:
    try:
        start_int = int(start)
        end_int = int(end)
    except ValueError:
        return False
    if end_int < start_int:
        return False
    if end_int - start_int >= MAX_SHORTHAND_RANGE_ITEMS:
        logger.warning(
            "Skipping oversized shorthand SKU range: start=%s end=%s count=%s",
            start,
            end,
            end_int - start_int + 1,
        )
        return False
    width = len(start)
    for value in range(start_int, end_int + 1):
        append_unique(skus, str(value).zfill(width), repeated_skus)
    return True


def append_unique(values: list[str], value: str, repeated_values: list[str] | None = None) -> None:
    if value in values:
        if repeated_values is not None and value not in repeated_values:
            repeated_values.append(value)
        return
    values.append(value)


def attach_variant_parent_groups(detections: list[dict]) -> list[dict]:
    items = [item for item in detections if item.get("box_type") == "item"]
    variants = [item for item in detections if item.get("box_type") == "variant"]
    groups_by_parent: dict[int, dict] = {}

    for variant in variants:
        parent = find_parent_item_for_variant(variant, items)
        if not parent:
            continue
        variant["parent_sku"] = parent["sku"]
        key = id(parent)
        group = groups_by_parent.setdefault(key, {"parent": parent, "variants": []})
        group["variants"].append(variant)

    groups: list[dict] = []
    for group in groups_by_parent.values():
        parent = group["parent"]
        variants_sorted = sorted(group["variants"], key=lambda item: (item["box"]["y"], item["box"]["x"], item["sku"]))
        skus = [parent["sku"]]
        for variant in variants_sorted:
            if variant["sku"] not in skus:
                skus.append(variant["sku"])
        if len(skus) < 2:
            continue
        parent["group_skus"] = skus
        parent["box_type"] = "complex"
        groups.append({"parent": parent, "variants": variants_sorted, "skus": skus})
    return groups


def flag_repeated_page_skus(detections: list[dict]) -> None:
    by_page_sku: dict[tuple[int, str], list[dict]] = {}
    for item in detections:
        by_page_sku.setdefault((item["page"], item["sku"]), []).append(item)

    for (page, sku), items in by_page_sku.items():
        if len(items) < 2:
            continue
        message = f"SKU repeats {len(items)} times on page {page}."
        for item in items:
            item["status"] = "repeating_item"
            item["message"] = append_item_message(item.get("message", ""), message)
        logger.warning("Repeated SKU on same page: page=%s sku=%s count=%s", page, sku, len(items))


def append_item_message(current: str, extra: str) -> str:
    current = str(current or "").strip()
    extra = str(extra or "").strip()
    if not current:
        return extra
    if not extra or extra in current:
        return current
    return f"{current} {extra}"


def find_parent_item_for_variant(variant: dict, items: list[dict]) -> dict | None:
    variant_box = variant["box"]
    center_x = variant_box["x"] + variant_box["width"] / 2
    center_y = variant_box["y"] + variant_box["height"] / 2
    candidates: list[tuple[float, dict]] = []

    for item in items:
        if item["page"] != variant["page"]:
            continue
        box = item["box"]
        if not (
            box["x"] - 3 <= center_x <= box["x"] + box["width"] + 3
            and box["y"] - 3 <= center_y <= box["y"] + box["height"] + 3
        ):
            continue
        area = box["width"] * box["height"]
        candidates.append((area, item))

    if candidates:
        return sorted(candidates, key=lambda pair: pair[0])[0][1]

    fallback: list[tuple[float, dict]] = []
    for item in items:
        if item["page"] != variant["page"]:
            continue
        box = item["box"]
        overlap_x = min(box["x"] + box["width"], variant_box["x"] + variant_box["width"]) - max(box["x"], variant_box["x"])
        if overlap_x <= 8:
            continue
        if variant_box["y"] < box["y"] - 3 or variant_box["y"] > box["y"] + box["height"] + 12:
            continue
        fallback.append((abs((box["y"] + box["height"]) - variant_box["y"]), item))

    return sorted(fallback, key=lambda pair: pair[0])[0][1] if fallback else None


def variant_sku_candidate_words(
    words: list[Word],
    item_skus: set[str],
    min_digits: int,
    max_digits: int,
) -> list[Word]:
    virtual_by_original: dict[int, Word] = {}
    for word in words:
        virtual = merged_variant_sku_prefix(word, words, min_digits, max_digits)
        if virtual and virtual.text not in item_skus:
            virtual_by_original[id(word)] = virtual

    candidates: list[Word] = list(virtual_by_original.values())
    candidates.extend(word for word in words if id(word) not in virtual_by_original)
    return sorted(candidates, key=lambda item: (item.top, item.x0, item.text))


def merged_variant_sku_prefix(
    word: Word,
    words: list[Word],
    min_digits: int,
    max_digits: int,
) -> Word | None:
    if not is_ascii_digits(word.text) or len(word.text) <= min_digits or word.height > 8.8:
        return None

    fragments = [
        other
        for other in words
        if other is not word
        and is_ascii_digits(other.text)
        and len(other.text) < len(word.text)
        and abs(other.mid_y - word.mid_y) <= 1.8
        and other.x0 >= word.x0 - 0.5
        and other.x1 <= word.x1 + 0.5
    ]
    if len(fragments) < 2:
        return None

    text = ""
    picked: list[Word] = []
    for fragment in sorted(fragments, key=lambda item: (item.x0, item.x1)):
        trial = text + fragment.text
        if not word.text.startswith(trial):
            continue
        text = trial
        picked.append(fragment)
        suffix_len = len(word.text) - len(text)
        if min_digits <= len(text) <= max_digits and 1 <= suffix_len <= 3:
            return Word(
                text=text,
                x0=picked[0].x0,
                x1=fragment.x1,
                top=min(item.top for item in picked),
                bottom=max(item.bottom for item in picked),
                comma_primary=True,
                original_text=word.text,
            )

    return None


def detect_page_boxes(page: PageText, min_digits: int, max_digits: int, box_padding: float) -> list[dict]:
    candidates = [
        word
        for word in page.words
        if is_sku(word.text, min_digits, max_digits)
        and word.height <= 11
        and word.top > 3
        and word.bottom < page.height - 35
        and not set(word.text) == {"0"}
        and not is_after_comma_continuation(page, word, min_digits, max_digits)
    ]
    if not candidates:
        return []

    header_detections = detect_header_driven_boxes(page, candidates, min_digits, max_digits, box_padding)
    if header_detections:
        return header_detections

    row_step = estimate_row_step([word.top for word in candidates])
    sku_inset = estimate_sku_inset(candidates)

    primary: list[tuple[Word, int]] = []
    for word in candidates:
        row_index = max(0, int(math.floor(word.top / row_step)))
        offset = word.top - row_index * row_step
        if is_primary_sku(page, word, offset):
            primary.append((word, row_index))

    primary_by_row: dict[int, list[Word]] = {}
    for word, row_index in primary:
        primary_by_row.setdefault(row_index, []).append(word)

    detections: list[dict] = []
    row_keys = sorted(primary_by_row)
    for row_pos, row_index in enumerate(row_keys):
        row_items = sorted(primary_by_row[row_index], key=lambda item: item.x0)
        row_start = max(0.0, row_index * row_step)
        next_row = row_keys[row_pos + 1] if row_pos + 1 < len(row_keys) else None
        row_end = min(page.height - 35, (next_row * row_step) if next_row is not None else row_start + row_step)

        for index, word in enumerate(row_items):
            next_word = row_items[index + 1] if index + 1 < len(row_items) else None
            x = clamp(word.x0 - sku_inset - box_padding, 0, page.width)
            next_x = clamp(next_word.x0 - sku_inset, 0, page.width) if next_word else page.width
            box = {
                "x": x,
                "y": clamp(row_start - box_padding, 0, page.height),
                "width": clamp(next_x - x + box_padding, 8, page.width - x),
                "height": clamp(row_end - row_start + box_padding * 2, 8, page.height - row_start),
            }
            brochure_price_text, brochure_price, brochure_price_from_marker = find_brochure_price(page, box, word)
            detections.append(
                _detection(
                    page,
                    word.text,
                    "item",
                    box,
                    0.88,
                    brochure_price_text,
                    brochure_price,
                    brochure_price_not_defined=is_missing_brochure_price_text(brochure_price_text),
                    brochure_price_from_marker=brochure_price_from_marker,
                )
            )

    seen: set[tuple] = set()
    clean: list[dict] = []
    for item in sorted(detections, key=lambda d: (d["page"], d["box"]["y"], d["box"]["x"], d["sku"])):
        key = (item["page"], item["sku"], round(item["box"]["x"]), round(item["box"]["y"]))
        if key in seen:
            continue
        seen.add(key)
        clean.append(item)

    return clean


def detect_variant_table_rows(
    page: PageText,
    item_detections: list[dict],
    min_digits: int,
    max_digits: int,
) -> list[dict]:
    footer_top = page.footer_top or page.height - 35
    item_skus = {item["sku"] for item in item_detections}
    variants: list[dict] = []

    for word in variant_sku_candidate_words(page.words, item_skus, min_digits, max_digits):
        if word.fragment and not word.comma_primary:
            continue
        if not is_sku(word.text, min_digits, max_digits):
            continue
        if word.text in item_skus:
            continue
        if word.height > 8.8:
            continue
        if word.top <= 24 or word.bottom >= footer_top - 4:
            continue
        if is_after_comma_continuation(page, word, min_digits, max_digits):
            continue

        line = same_line_words(page, word, max(3.0, word.height * 0.9))
        if is_overlapped_price_noise(word, line) and not has_nearby_code_column_header(page, word):
            continue
        right_limit = min(
            [
                other.x0
                for other in line
                if other is not word
                and other.x0 > word.x1 + 8
                and is_sku(other.text, min_digits, max_digits)
                and not (is_overlapped_price_noise(other, line) and not has_nearby_code_column_header(page, other))
            ]
            or [page.width]
        )
        price_window = find_table_price_window(page, word, right_limit)
        if not price_window:
            continue
        euro_window = price_window["euro_window"]
        price_candidates = [
            candidate
            for candidate in table_price_candidates(line, word.x1 + 8, right_limit)
            if euro_window[0] <= candidate[0] and candidate[1] <= euro_window[1]
        ]
        all_price_candidates = table_price_candidates(line, word.x1 + 8, right_limit)

        if not price_candidates:
            continue

        price_candidates.sort(key=lambda item: item[0])
        chosen = price_candidates[0]
        table_issue = table_price_issue(price_window, chosen, all_price_candidates)
        price_group_right = chosen[1]
        line_right = max(chosen[1], price_group_right)
        line_top = min(other.top for other in line)
        line_bottom = max(other.bottom for other in line)
        box = {
            "x": clamp(word.x0 - 1, 0, page.width),
            "y": clamp(line_top - 1, 0, page.height),
            "width": clamp(line_right - word.x0 + 2, 8, page.width - word.x0),
            "height": clamp(line_bottom - line_top + 2, 6, page.height - line_top),
        }
        variants.append(
            _detection(
                page,
                word.text,
                "variant",
                box,
                0.78,
                chosen[3],
                chosen[2],
                linkable=False,
                status="table_header_error" if table_issue else "",
                message=table_issue,
                brochure_price_not_defined=is_missing_brochure_price_text(chosen[3]),
            )
        )

    return dedupe_detections(variants)


def table_price_candidates(line: list[Word], left: float, right: float) -> list[tuple[float, float, float, str]]:
    candidates: list[tuple[float, float, float, str]] = []
    tokens = normalized_table_price_tokens([word for word in line if word.x0 > left and word.x0 < right])

    for word in tokens:
        if is_missing_brochure_price_text(word.text):
            candidates.append((word.x0, word.x1, None, MISSING_BROCHURE_PRICE_TEXT))
            continue

        price = parse_table_price_word(word.text)
        if price is not None:
            candidates.append((word.x0, word.x1, price, word.text))

    sequence: list[Word] = []
    previous: Word | None = None
    for word in sorted(tokens, key=lambda item: item.x0):
        if not re.fullmatch(r"\d+|[.,]", word.text):
            candidates.extend(build_table_price_from_sequence(sequence))
            sequence = []
            previous = None
            continue
        gap = word.x0 - previous.x1 if previous else 0
        if previous and gap > 3:
            candidates.extend(build_table_price_from_sequence(sequence))
            sequence = []
        sequence.append(word)
        previous = word
    candidates.extend(build_table_price_from_sequence(sequence))

    candidates = [
        candidate
        for candidate in candidates
        if not is_contained_price_candidate(candidate, candidates)
    ]
    seen: set[tuple[int, int, str]] = set()
    clean: list[tuple[float, float, float, str]] = []
    for x0, x1, price, text in sorted(candidates, key=lambda item: (item[0], item[1])):
        key = (round(x0), round(x1), text)
        if key in seen:
            continue
        seen.add(key)
        clean.append((x0, x1, price, text))
    return clean


def normalized_table_price_tokens(tokens: list[Word]) -> list[Word]:
    out: list[Word] = []
    for word in tokens:
        if is_overlapped_price_noise(word, tokens):
            continue
        out.extend(split_price_token(word))
    return sorted(out, key=lambda item: (item.x0, item.x1, item.text))


def is_overlapped_price_noise(word: Word, tokens: list[Word]) -> bool:
    if not is_ascii_digits(word.text) or len(word.text) < 5:
        return False
    pieces = [
        other
        for other in tokens
        if other is not word
        and len(other.text) < len(word.text)
        and abs(other.mid_y - word.mid_y) <= 1.5
        and other.x0 >= word.x0 - 0.2
        and other.x1 <= word.x1 + 0.2
        and re.fullmatch(r"\d+[.,]?|[.,]\d+|[.,]", other.text)
    ]
    return len(pieces) >= 2


def split_price_token(word: Word) -> list[Word]:
    text = word.text
    if re.fullmatch(r"\d+[.,]", text):
        return split_word_at_chars(word, len(text) - 1)
    if re.fullmatch(r"[.,]\d+", text):
        return split_word_at_chars(word, 1)
    return [word]


def split_word_at_chars(word: Word, split_at: int) -> list[Word]:
    text = word.text
    if split_at <= 0 or split_at >= len(text):
        return [word]
    split_x = word.x0 + word.width * (split_at / max(1, len(text)))
    first = Word(
        text=text[:split_at],
        x0=word.x0,
        x1=split_x,
        top=word.top,
        bottom=word.bottom,
        fragment=True,
        original_text=word.original_text or word.text,
    )
    second = Word(
        text=text[split_at:],
        x0=split_x,
        x1=word.x1,
        top=word.top,
        bottom=word.bottom,
        fragment=True,
        original_text=word.original_text or word.text,
    )
    return [first, second]


def find_table_price_window(page: PageText, sku_word: Word, right_limit: float) -> dict | None:
    header_seeds = [
        word
        for word in page.words
        if (is_euro_header(word.text) or is_leva_header(word.text))
        and word.x0 > sku_word.x1 + 4
        and word.x0 < right_limit
        and -4 <= sku_word.top - word.top <= 90
    ]
    if not header_seeds:
        return None

    seen_row_mids: list[float] = []
    for seed in sorted(header_seeds, key=lambda item: (sku_word.top - item.top, item.x0 - sku_word.x1)):
        if any(abs(seed.mid_y - seen_mid) <= 3 for seen_mid in seen_row_mids):
            continue
        seen_row_mids.append(seed.mid_y)
        same_header = same_line_words(page, seed, 6)
        if not has_table_sku_column_header(sku_word, same_header):
            continue
        price_headers = sorted(
            [
                word
                for word in same_header
                if (is_euro_header(word.text) or is_leva_header(word.text))
                and word.x0 > sku_word.x1 + 4
                and word.x0 < right_limit + 4
            ],
            key=lambda item: item.x0,
        )
        if not price_headers:
            continue
        first_header = price_headers[0]
        second_header = price_headers[1] if len(price_headers) > 1 else None
        left = max(sku_word.x1 + 1, first_header.x0 - 10)
        right = (second_header.x0 - 2) if second_header else first_header.x1 + 42
        if right > left + 4:
            return {
                "euro_window": (left, right),
                "first_header": first_header.text,
                "second_header": second_header.text if second_header else "",
                "second_window": (
                    second_header.x0 - 2,
                    min(right_limit, second_header.x1 + 42),
                )
                if second_header
                else None,
            }
    return None


def table_price_issue(
    price_window: dict,
    euro_candidate: tuple[float, float, float, str],
    all_price_candidates: list[tuple[float, float, float, str]],
) -> str:
    issues: list[str] = []
    first_header = price_window.get("first_header", "")
    second_header = price_window.get("second_header", "")
    second_window = price_window.get("second_window")

    if is_leva_header(first_header) or (second_header and not is_leva_header(second_header)):
        issues.append("Table price headers are inconsistent; euro column should be first.")

    leva_candidate = None
    if second_window:
        second_left, second_right = second_window
        second_left_tolerance = 6.0
        second_candidates = [
            candidate
            for candidate in all_price_candidates
            if second_left - second_left_tolerance <= candidate[0]
            and candidate[1] <= second_right
            and candidate[0] > euro_candidate[1] + 0.5
        ]
        if second_candidates:
            leva_candidate = sorted(second_candidates, key=lambda item: item[0])[0]

    if (
        leva_candidate
        and euro_candidate[2] is not None
        and leva_candidate[2] is not None
        and not bgn_matches_euro(euro_candidate[2], leva_candidate[2])
    ):
        issues.append(
            f"Table leva/euro conversion mismatch: {leva_candidate[2]:.2f} / {euro_candidate[2]:.2f} is not {BGN_PER_EUR:.5f}."
        )

    return " ".join(issues)


def bgn_matches_euro(euro_price: float, leva_price: float) -> bool:
    expected = round(float(euro_price) * BGN_PER_EUR, 2)
    return abs(expected - float(leva_price)) <= 0.02


def has_table_sku_column_header(sku_word: Word, header_line: list[Word]) -> bool:
    for word in header_line:
        if word.x0 > sku_word.x0 + 10 or word.x1 < sku_word.x0 - 28:
            continue
        if is_euro_header(word.text) or is_leva_header(word.text):
            continue
        if is_priceish_text(word.text) or SKU_RE.search(word.text):
            continue
        return True
    return False


def has_nearby_code_column_header(page: PageText, sku_word: Word) -> bool:
    nearby = [
        word
        for word in page.words
        if 4 <= sku_word.top - word.top <= 45
        and word.x0 >= sku_word.x0 - 12
        and word.x0 <= sku_word.x0 + 40
        and not is_priceish_text(word.text)
        and not is_euro_header(word.text)
        and not is_leva_header(word.text)
    ]
    for line in cluster_words_by_top(nearby, tolerance=3):
        text = "".join(word.text for word in sorted(line, key=lambda item: item.x0)).lower()
        text = re.sub(r"[^a-zа-я]+", "", text)
        if "код" in text or "kog" in text or "kod" in text:
            return True
    return False


def is_euro_header(text: str) -> bool:
    lowered = str(text or "").lower()
    return "\u20ac" in lowered or "\u0432\u201a\u00ac" in lowered or "eur" in lowered


def is_leva_header(text: str) -> bool:
    lowered = str(text or "").lower()
    return (
        lowered.startswith("\u043b\u0432")
        or lowered.startswith("\u0440\u00bb\u0440\u0456")
        or lowered.startswith("lv")
        or lowered.startswith("bgn")
    )


def is_priceish_text(text: str) -> bool:
    return bool(re.fullmatch(r"[\d\s.,/-]+", str(text or "")))


def is_contained_price_candidate(
    candidate: tuple[float, float, float, str],
    candidates: list[tuple[float, float, float, str]],
) -> bool:
    x0, x1, _price, text = candidate
    for other_x0, other_x1, _other_price, other_text in candidates:
        if other_text == text and abs(other_x0 - x0) < 0.1 and abs(other_x1 - x1) < 0.1:
            continue
        if other_x0 <= x0 + 0.1 and other_x1 >= x1 - 0.1 and len(other_text) > len(text):
            return True
    return False


def build_table_price_from_sequence(sequence: list[Word]) -> list[tuple[float, float, float, str]]:
    if len(sequence) < 3:
        return []
    out: list[tuple[float, float, float, str]] = []
    for start in range(len(sequence)):
        text = ""
        for end in range(start, min(len(sequence), start + 7)):
            text += sequence[end].text
            price = parse_table_price_word(text)
            if price is not None:
                out.append((sequence[start].x0, sequence[end].x1, price, text))
    return out


def detect_header_driven_boxes(
    page: PageText,
    candidates: list[Word],
    min_digits: int,
    max_digits: int,
    box_padding: float,
) -> list[dict]:
    footer_top = page.footer_top or page.height - 35
    candidate_rows = cluster_words_by_top(
        [word for word in candidates if not word.fragment and word.top < footer_top - 8],
        tolerance=8,
    )
    headers: list[dict] = []

    for row in candidate_rows:
        row_words = [
            word
            for word in sorted(row, key=lambda item: item.x0)
            if not is_multiline_header_continuation(word, candidates)
            and not is_sku_range_continuation(page, word, row)
        ]
        row_headers = []
        for index, word in enumerate(row_words):
            right_edge = (
                row_words[index + 1].x0 - HEADER_SKU_TEXT_INSET
                if index + 1 < len(row_words)
                else page.width
            )
            if is_variant_table_sku_word(page, word, min_digits, max_digits) and not find_direct_header_price_word(
                page, word, right_edge
            ):
                continue
            price_word = find_header_price_word(page, word, right_edge)
            if not price_word:
                continue
            left_edge = clamp(word.x0 - HEADER_SKU_TEXT_INSET, 0, page.width)
            right_edge = find_header_price_box_right(page, word, price_word, right_edge)
            top = clamp(min(word.top - 3, price_word.top - 13), 0, page.height)
            row_headers.append(
                {
                    "word": word,
                    "price_word": price_word,
                    "left": left_edge,
                    "right": clamp(right_edge, left_edge + 8, page.width),
                    "top": top,
                }
            )
        headers.extend(row_headers)

    primary_candidates = [word for word in candidates if not word.fragment]
    has_top_page_header = len(headers) == 1 and headers[0]["top"] <= 24
    if len(headers) < 3 and not (len(headers) == 1 and len(primary_candidates) == 1) and not has_top_page_header:
        return []

    headers.sort(key=lambda item: (item["top"], item["left"]))
    detections: list[dict] = []
    for header in headers:
        bottom = find_next_header_top(header, headers, footer_top)
        bottom = find_large_image_boundary_top(page, header, headers, bottom)
        if bottom <= header["top"] + 18:
            continue
        box = {
            "x": clamp(header["left"] - box_padding, 0, page.width),
            "y": clamp(header["top"] - box_padding, 0, page.height),
            "width": clamp(header["right"] - header["left"] + box_padding * 2, 8, page.width - header["left"]),
            "height": clamp(bottom - header["top"] + box_padding * 2, 8, page.height - header["top"]),
        }
        price_text = header["price_word"].text
        price_from_marker = has_from_price_marker(page, box, header["price_word"])
        detections.append(
            _detection(
                page,
                header["word"].text,
                "item",
                box,
                0.94,
                brochure_price_display_text(price_text),
                brochure_price_to_decimal(price_text),
                brochure_price_not_defined=is_missing_brochure_price_text(price_text),
                brochure_price_from_marker=price_from_marker,
            )
        )

    return dedupe_detections(detections)


def cluster_words_by_top(words: list[Word], tolerance: float) -> list[list[Word]]:
    rows: list[list[Word]] = []
    for word in sorted(words, key=lambda item: item.top):
        if not rows or abs(median([item.top for item in rows[-1]]) - word.top) > tolerance:
            rows.append([word])
        else:
            rows[-1].append(word)
    return rows


def find_header_price_word(page: PageText, sku_word: Word, right_edge: float) -> Word | None:
    prices = find_direct_header_price_words(page, sku_word, right_edge)
    prices.extend(find_split_header_price_words(page, sku_word, right_edge))
    if not prices:
        return None
    return sorted(prices, key=lambda item: (item.x0, item.top))[0]


def find_direct_header_price_word(page: PageText, sku_word: Word, right_edge: float) -> Word | None:
    prices = find_direct_header_price_words(page, sku_word, right_edge)
    return sorted(prices, key=lambda item: (item.x0, item.top))[0] if prices else None


def find_direct_header_price_words(page: PageText, sku_word: Word, right_edge: float) -> list[Word]:
    return [word for word in page.words if is_header_price_word(word, sku_word, right_edge)]


def is_variant_table_sku_word(page: PageText, word: Word, min_digits: int, max_digits: int) -> bool:
    if word.fragment and not word.comma_primary:
        return False
    if not is_sku(word.text, min_digits, max_digits):
        return False
    if word.height > 8.8 or word.top <= 24:
        return False
    if is_after_comma_continuation(page, word, min_digits, max_digits):
        return False

    line = same_line_words(page, word, max(3.0, word.height * 0.9))
    right_limit = min(
        [
            other.x0
            for other in line
            if other is not word
            and other.x0 > word.x1 + 8
            and is_sku(other.text, min_digits, max_digits)
        ]
        or [page.width]
    )
    return find_table_price_window(page, word, right_limit) is not None


def is_sku_range_continuation(page: PageText, word: Word, row: list[Word]) -> bool:
    if not is_ascii_digits(word.text):
        return False
    previous_skus = [
        other
        for other in row
        if other is not word
        and is_ascii_digits(other.text)
        and other.x1 < word.x0
        and abs(other.mid_y - word.mid_y) <= 4
    ]
    if not previous_skus:
        return False
    previous = sorted(previous_skus, key=lambda item: item.x1)[-1]
    between = [
        other
        for other in page.words
        if other is not word
        and previous.x1 - 1 <= other.x0 <= word.x0 + 1
        and abs(other.mid_y - word.mid_y) <= 4
    ]
    return any(other.text.strip() in {"-", "\u2013", "\u2014"} for other in between)


def is_header_price_word(word: Word, sku_word: Word, right_edge: float) -> bool:
    if not (is_ascii_digits(word.text) or is_missing_brochure_price_text(word.text)):
        return False
    if not 3 <= len(word.text) <= 6:
        return False
    if word.height < 13:
        return False
    if word.x0 < sku_word.x0 + 8 or word.x0 >= right_edge - 1:
        return False
    delta_top = word.top - sku_word.top
    return 4 <= delta_top <= 18


def find_split_header_price_words(page: PageText, sku_word: Word, right_edge: float) -> list[Word]:
    split_prices: list[Word] = []
    digits = [word for word in page.words if is_ascii_digits(word.text)]
    for whole in digits:
        if not 1 <= len(whole.text) <= 4:
            continue
        if whole.text.startswith("0"):
            continue
        if whole.height < 18:
            continue
        if whole.x0 < sku_word.x0 + 8 or whole.x0 >= right_edge - 1:
            continue
        delta_top = whole.top - sku_word.top
        if not 4 <= delta_top <= 44:
            continue

        cents_candidates = [
            cents
            for cents in digits
            if cents is not whole
            and len(cents.text) == 2
            and -2 <= cents.x0 - whole.x1 <= 60
            and abs(cents.mid_y - whole.mid_y) <= 18
            and cents.x0 < right_edge - 1
        ]
        if not cents_candidates:
            continue
        cents = sorted(cents_candidates, key=lambda item: (item.x0, abs(item.mid_y - whole.mid_y)))[0]
        split_prices.append(
            Word(
                text=f"{whole.text}{cents.text}",
                x0=whole.x0,
                x1=cents.x1,
                top=min(whole.top, cents.top),
                bottom=max(whole.bottom, cents.bottom),
                original_text=f"{whole.text}{cents.text}",
            )
        )
    return split_prices


def find_header_price_box_right(
    page: PageText,
    sku_word: Word,
    price_word: Word,
    default_right_edge: float,
) -> float:
    price_words = [
        word
        for word in page.words
        if is_ascii_digits(word.text)
        and word.height >= 13
        and word.x0 >= sku_word.x0 + 8
        and word.x0 < default_right_edge - 1
        and 4 <= word.top - sku_word.top <= 44
        and abs(word.mid_y - price_word.mid_y) <= 26
    ]
    if not price_words:
        return default_right_edge

    group_right = price_word.x1
    for word in sorted(price_words, key=lambda item: item.x0):
        if word.x1 < price_word.x0 - 1:
            continue
        if word.x0 <= group_right + HEADER_PRICE_GROUP_GAP:
            group_right = max(group_right, word.x1)

    right = group_right + HEADER_PRICE_BOX_PADDING
    if default_right_edge - right <= HEADER_PAGE_EDGE_TOLERANCE:
        return default_right_edge
    return clamp(right, sku_word.x0 + 16, default_right_edge)


def is_multiline_header_continuation(word: Word, candidates: list[Word]) -> bool:
    for other in candidates:
        if other is word:
            continue
        if other.top >= word.top:
            continue
        if word.top - other.top > 8:
            continue
        if abs(other.x0 - word.x0) > 5:
            continue
        if min(other.x1, word.x1) - max(other.x0, word.x0) <= 8:
            continue
        return True
    return False


def find_next_header_top(current: dict, headers: list[dict], footer_top: float) -> float:
    bottom = footer_top
    left = current["left"]
    right = current["right"]
    for other in headers:
        if other is current or other["top"] <= current["top"] + 18:
            continue
        overlap = min(right, other["right"]) - max(left, other["left"])
        if overlap <= HEADER_EDGE_TOUCH_TOLERANCE:
            continue
        bottom = min(bottom, other["top"])
    return bottom


def find_large_image_boundary_top(page: PageText, current: dict, headers: list[dict], proposed_bottom: float) -> float:
    image_blocks = page.image_blocks or []
    if not image_blocks:
        return proposed_bottom

    row_peers = [
        header
        for header in headers
        if header is not current
        and abs(header["top"] - current["top"]) <= 14
        and min(header["right"], current["right"]) - max(header["left"], current["left"]) <= HEADER_EDGE_TOUCH_TOLERANCE
    ]
    if not row_peers:
        return proposed_bottom

    left = current["left"]
    right = current["right"]
    current_width = max(1.0, right - left)
    candidates: list[float] = []
    for block in image_blocks:
        top = float(block["top"])
        if top <= current["top"] + 85:
            continue
        if top >= proposed_bottom - 18:
            continue
        if top < page.height * 0.35:
            continue
        overlap = min(right, float(block["x1"])) - max(left, float(block["x0"]))
        if overlap < max(35.0, current_width * 0.25):
            continue
        candidates.append(top)

    if candidates:
        boundary = min(candidates)
        logger.info(
            "Limiting item box at large image boundary: page=%s sku=%s top=%.1f imageTop=%.1f",
            page.page_number,
            current["word"].text,
            current["top"],
            boundary,
        )
        return min(proposed_bottom, boundary)
    return proposed_bottom


def dedupe_detections(detections: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    clean: list[dict] = []
    for item in sorted(detections, key=lambda d: (d["page"], d["box"]["y"], d["box"]["x"], d["sku"])):
        key = (item["page"], item["sku"], round(item["box"]["x"]), round(item["box"]["y"]))
        if key in seen:
            continue
        if is_near_duplicate_detection(item, clean):
            continue
        seen.add(key)
        clean.append(item)
    return clean


def is_near_duplicate_detection(item: dict, existing: list[dict]) -> bool:
    box = item["box"]
    for other in existing:
        other_box = other["box"]
        if item["page"] != other["page"] or item["sku"] != other["sku"]:
            continue
        if item.get("box_type") != other.get("box_type"):
            continue
        if str(item.get("brochure_price_text") or "") != str(other.get("brochure_price_text") or ""):
            continue
        if abs(box["x"] - other_box["x"]) > 2 or abs(box["width"] - other_box["width"]) > 4:
            continue
        if abs(box["y"] - other_box["y"]) <= 6:
            return True
    return False


def is_sku(text: str, min_digits: int, max_digits: int) -> bool:
    return is_ascii_digits(text) and min_digits <= len(text) <= max_digits


def is_ascii_digits(text: str) -> bool:
    return bool(ASCII_DIGITS_RE.fullmatch(str(text or "")))


def is_primary_sku(page: PageText, word: Word, offset: float) -> bool:
    if word.fragment and not word.comma_primary:
        return False
    if same_line_has_currency(page, word):
        return True
    return 18 <= offset <= 62


def same_line_has_currency(page: PageText, word: Word) -> bool:
    for other in same_line_words(page, word, 4):
        if other.x0 < word.x1 or other.x0 > word.x1 + 80:
            continue
        lower = other.text.lower()
        if lower.startswith("\u043b\u0432") or lower.startswith("lv") or lower.startswith("bgn"):
            return True
    return False


def is_after_comma_continuation(page: PageText, word: Word, min_digits: int, max_digits: int) -> bool:
    for previous in same_line_words(page, word, 4):
        if previous is word or previous.x1 > word.x0:
            continue
        if word.x0 - previous.x1 > 70:
            continue
        original = previous.original_text or previous.text
        if not original.endswith(","):
            continue
        first = original.split(",", 1)[0].strip()
        if is_ascii_digits(first) and min_digits <= len(first) <= max_digits:
            return True
    return False


def same_line_words(page: PageText, word: Word, tolerance: float) -> list[Word]:
    return sorted([other for other in page.words if abs(other.mid_y - word.mid_y) <= tolerance], key=lambda item: item.x0)


def find_brochure_price(page: PageText, box: dict, sku_word: Word) -> tuple[str, float | None, bool]:
    top_limit = box["y"] - 2
    bottom_limit = min(box["y"] + 78, sku_word.bottom + 18)
    left = box["x"]
    right = box["x"] + box["width"]
    candidates: list[Word] = []

    for word in page.words:
        if not (is_ascii_digits(word.text) or is_missing_brochure_price_text(word.text)):
            continue
        if not 3 <= len(word.text) <= 6:
            continue
        if word.height < 11:
            continue
        center_x = (word.x0 + word.x1) / 2
        if not (left <= center_x <= right):
            continue
        if not (top_limit <= word.top <= bottom_limit):
            continue
        candidates.append(word)

    if not candidates:
        return "", None, False

    candidates.sort(key=lambda item: (abs(item.top - sku_word.top), -item.height))
    price_word = candidates[0]
    raw = price_word.text
    return brochure_price_display_text(raw), brochure_price_to_decimal(raw), has_from_price_marker(page, box, price_word)


def has_from_price_marker(page: PageText, box: dict, price_word: Word) -> bool:
    for word in page.words:
        if not is_from_price_marker(word.text):
            continue
        if word.x0 < box["x"] - 6 or word.x1 > price_word.x0 + 3:
            continue
        if not 0 <= price_word.x0 - word.x1 <= 36:
            continue
        if abs(word.mid_y - price_word.mid_y) <= 18:
            return True
    return False


def is_from_price_marker(text: str) -> bool:
    cleaned = re.sub(r"[^A-Za-zА-Яа-я]", "", str(text or "")).casefold()
    return cleaned in {"ot", "от"}


def find_large_image_blocks(page) -> list[dict]:
    blocks: list[dict] = []
    page_width = float(page.width)
    page_height = float(page.height)
    for image in getattr(page, "images", []):
        try:
            x0 = float(image["x0"])
            x1 = float(image["x1"])
            top = float(image["top"])
            bottom = float(image["bottom"])
        except (KeyError, TypeError, ValueError):
            continue
        width = x1 - x0
        height = bottom - top
        if width < page_width * 0.82:
            continue
        if height < page_height * 0.18:
            continue
        if x0 > page_width * 0.08 or x1 < page_width * 0.92:
            continue
        blocks.append(
            {
                "x0": x0,
                "x1": x1,
                "top": top,
                "bottom": bottom,
                "width": width,
                "height": height,
            }
        )
    return sorted(blocks, key=lambda block: (block["top"], block["x0"]))


def find_footer_top(page) -> float | None:
    footer_candidates = []
    for rect in getattr(page, "rects", []):
        color = rect.get("non_stroking_color")
        if not color or len(color) < 3:
            continue
        is_dark = color[0] < 0.35 and color[1] < 0.35 and color[2] < 0.35
        if not is_dark:
            continue
        if rect.get("top", 0) < float(page.height) * 0.72:
            continue
        if rect.get("width", 0) < float(page.width) * 0.45:
            continue
        footer_candidates.append(float(rect["top"]))
    if footer_candidates:
        return min(footer_candidates)
    return float(page.height) - 35


def brochure_price_to_decimal(raw: str) -> float | None:
    if is_missing_brochure_price_text(raw):
        return None
    if not is_ascii_digits(raw):
        return None
    return round(int(raw) / 100, 2)


def parse_table_price_word(raw: str) -> float | None:
    text = str(raw or "").replace("\xa0", "").replace(" ", "")
    match = re.fullmatch(r"([0-9]{1,5})[,.]([0-9]{1,2})", text)
    if not match:
        return None
    return round(float(match.group(1)) + int(match.group(2).ljust(2, "0")) / 100, 2)


def is_missing_brochure_price_text(raw: str) -> bool:
    text = str(raw or "").replace("\xa0", "").replace(" ", "").strip()
    return bool(re.fullmatch(r"\?+(?:[,.]?\?*0*)?", text))


def brochure_price_display_text(raw: str) -> str:
    return MISSING_BROCHURE_PRICE_TEXT if is_missing_brochure_price_text(raw) else str(raw or "")


def attach_price_comparisons(detections: list[dict], resolved: dict[str, dict]) -> None:
    checked: dict[str, dict] = {}
    unique_skus = sorted({item["sku"] for item in detections})
    browser_results = compare_prices_with_playwright(unique_skus)
    search_fallback_blocked: dict | None = None
    for item in detections:
        sku = item["sku"]
        if sku not in checked:
            link = resolved.get(sku, {})
            browser_result = browser_results.get(sku, {})
            if browser_result:
                checked[sku] = browser_result
            elif link.get("source") == "search-fallback" and search_fallback_blocked:
                checked[sku] = dict(search_fallback_blocked)
            else:
                checked[sku] = compare_website_price(link)
                if link.get("source") == "search-fallback" and checked[sku].get("price_status") == "blocked":
                    search_fallback_blocked = checked[sku]
            apply_price_check_result(resolved.setdefault(sku, {}), checked[sku])

        brochure_price = item.get("brochure_price")
        website_price = resolved.get(sku, {}).get("website_price")
        item["website_price"] = website_price

    apply_from_price_selection(detections, "website_price", "website_from_price_candidate")
    update_from_price_display(detections)

    for item in detections:
        sku = item["sku"]
        brochure_price, not_defined = comparison_brochure_price(item, "website_from_price_candidate")
        website_price = item.get("website_price")
        if not_defined:
            item["price_status"] = undefined_brochure_price_status(item)
            item["price_message"] = undefined_brochure_price_message(item)
        elif brochure_price is None:
            item["price_status"] = "no_brochure_price"
            item["price_message"] = "No brochure price was detected."
        elif website_price is None:
            item["price_status"] = resolved.get(sku, {}).get("price_status", "no_website_price")
            item["price_message"] = resolved.get(sku, {}).get("price_message", "No website price was available.")
        elif abs(float(brochure_price) - float(website_price)) < 0.01:
            item["price_status"] = "match"
            item["price_message"] = "Brochure and website prices match."
        else:
            item["price_status"] = "different"
            item["price_message"] = "Brochure and website prices are different."


def attach_excel_comparisons(detections: list[dict], excel_prices: dict[str, dict]) -> None:
    for item in detections:
        sku = item["sku"]
        excel_item = excel_prices.get(sku)
        if excel_item and excel_item.get("excel_price") is not None:
            item["excel_price"] = excel_item["excel_price"]
            item["excel_row"] = excel_item.get("excel_row")
        else:
            item["excel_price"] = None

    apply_from_price_selection(detections, "excel_price", "excel_from_price_candidate")
    update_from_price_display(detections)

    for item in detections:
        brochure_price, not_defined = comparison_brochure_price(item, "excel_from_price_candidate")
        if not_defined:
            item["excel_status"] = undefined_brochure_price_status(item)
            item["excel_message"] = undefined_brochure_price_message(item)
        else:
            status, message = compare_two_prices(
                brochure_price,
                item.get("excel_price"),
                "Excel",
            )
            item["excel_status"] = status
            item["excel_message"] = message


def attach_triple_comparisons(detections: list[dict]) -> None:
    update_from_price_display(detections)
    for item in detections:
        brochure_price, not_defined = triple_brochure_price(item)
        website_price = item.get("website_price") or None
        excel_price = item.get("excel_price")

        if not_defined:
            item["triple_status"] = undefined_brochure_price_status(item)
            item["triple_message"] = undefined_brochure_price_message(item)
        elif brochure_price is None:
            item["triple_status"] = "no_brochure_price"
            item["triple_message"] = "No brochure price was detected."
        elif website_price is None:
            item["triple_status"] = "no_website_price"
            item["triple_message"] = "No website price was available."
        elif excel_price is None:
            item["triple_status"] = "no_excel_price"
            item["triple_message"] = "No Excel price was found for this SKU."
        elif prices_equal(brochure_price, website_price) and prices_equal(brochure_price, excel_price):
            item["triple_status"] = "match"
            item["triple_message"] = "Brochure, website, and Excel prices match."
        else:
            item["triple_status"] = "different"
            item["triple_message"] = "At least one of brochure, website, or Excel price is different."


def apply_from_price_selection(detections: list[dict], source_price_key: str, selection_key: str) -> None:
    for item in detections:
        if is_from_price_detection(item):
            item[selection_key] = False

    for group in from_price_groups(detections):
        priced_items = [
            item
            for item in group
            if item.get(source_price_key) is not None
        ]
        if not priced_items:
            logger.info(
                "No source prices available for 'from' price selection: key=%s parent=%s skus=%s",
                source_price_key,
                group[0].get("parent_sku") or group[0].get("sku"),
                ",".join(item["sku"] for item in group),
            )
            continue

        lowest_price = min(float(item[source_price_key]) for item in priced_items)
        selected = [
            item
            for item in priced_items
            if prices_equal(float(item[source_price_key]), lowest_price)
        ]
        for item in selected:
            item[selection_key] = True
        logger.info(
            "Selected 'from' price SKU(s): key=%s price=%.2f selected=%s group=%s",
            source_price_key,
            lowest_price,
            ",".join(item["sku"] for item in selected),
            ",".join(item["sku"] for item in group),
        )


def from_price_groups(detections: list[dict]) -> list[list[dict]]:
    grouped: dict[tuple[int, str], list[dict]] = {}
    for item in detections:
        if not is_from_price_detection(item):
            continue
        key = (int(item["page"]), str(item.get("parent_sku") or item["sku"]))
        grouped.setdefault(key, []).append(item)
    return [
        sorted(items, key=lambda item: (item.get("parent_sku") != "", item["box"]["y"], item["box"]["x"], item["sku"]))
        for items in grouped.values()
    ]


def is_from_price_detection(item: dict) -> bool:
    return bool(item.get("brochure_price_from_marker"))


def is_missing_brochure_price_detection(item: dict) -> bool:
    return bool(item.get("brochure_price_not_defined")) and is_missing_brochure_price_text(
        str(item.get("brochure_price_text") or "")
    )


def comparison_brochure_price(item: dict, selection_key: str) -> tuple[float | None, bool]:
    if is_missing_brochure_price_detection(item):
        return None, True
    if not is_from_price_detection(item):
        return item.get("brochure_price"), bool(item.get("brochure_price_not_defined"))
    if not item.get(selection_key):
        return None, True
    return item_from_price(item), False


def triple_brochure_price(item: dict) -> tuple[float | None, bool]:
    if is_missing_brochure_price_detection(item):
        return None, True
    if not is_from_price_detection(item):
        return item.get("brochure_price"), bool(item.get("brochure_price_not_defined"))
    if not (item.get("website_from_price_candidate") and item.get("excel_from_price_candidate")):
        return None, True
    return item_from_price(item), False


def update_from_price_display(detections: list[dict]) -> None:
    for item in detections:
        if not is_from_price_detection(item):
            continue
        if is_missing_brochure_price_detection(item):
            continue
        if "website_from_price_candidate" not in item and "excel_from_price_candidate" not in item:
            continue
        selected = bool(
            item.get("website_from_price_candidate")
            or item.get("excel_from_price_candidate")
        )
        if selected:
            item["brochure_price"] = item_from_price(item)
            item["brochure_price_text"] = item_from_price_text(item)
            item["brochure_price_not_defined"] = False
        else:
            item["brochure_price"] = None
            item["brochure_price_text"] = UNDEFINED_BROCHURE_PRICE_TEXT
            item["brochure_price_not_defined"] = True


def item_from_price(item: dict) -> float | None:
    if item.get("from_price") is not None:
        return item.get("from_price")
    return item.get("brochure_price")


def item_from_price_text(item: dict) -> str:
    text = str(item.get("from_price_text") or "").strip()
    if text:
        return text
    price = item_from_price(item)
    return f"{float(price):.2f}" if price is not None else ""


def from_price_not_defined_message(item: dict) -> str:
    if is_from_price_detection(item):
        return "Brochure shows a 'from' price; only the lowest priced SKU in this item box is compared."
    return "Brochure shows a 'from' price; exact SKU price is not defined."


def undefined_brochure_price_status(item: dict) -> str:
    return "brochure_price_missing" if is_missing_brochure_price_detection(item) else "brochure_price_not_defined"


def undefined_brochure_price_message(item: dict) -> str:
    if is_missing_brochure_price_detection(item):
        return "Brochure price is missing; website and Excel prices are shown as suggestions."
    return from_price_not_defined_message(item)


def mark_triple_not_checked(detections: list[dict]) -> None:
    for item in detections:
        item.setdefault("triple_status", "not_checked")
        item.setdefault("triple_message", "Triple price check was not selected.")


def compare_two_prices(brochure_price: float | None, other_price: float | None, label: str) -> tuple[str, str]:
    if brochure_price is None:
        return "no_brochure_price", "No brochure price was detected."
    if other_price is None:
        return f"no_{label.lower()}_price", f"No {label} price was found for this SKU."
    if prices_equal(brochure_price, other_price):
        return "match", f"Brochure and {label} prices match."
    return "different", f"Brochure and {label} prices are different."


def prices_equal(left: float, right: float) -> bool:
    return abs(float(left) - float(right)) < 0.01


def mark_price_only_statuses(detections: list[dict], resolved: dict[str, dict]) -> None:
    for item in detections:
        resolved[item["sku"]] = {
            "sku": item["sku"],
            "status": item.get("status") or "price_only",
            "message": item.get("message") or "Excel price check mode does not place links.",
        }


def apply_grouped_search_links(
    groups: list[dict],
    resolved: dict[str, dict],
    validate_counts: bool,
) -> None:
    prepared: list[dict] = []
    logger.info("Preparing grouped search links: groups=%s validate=%s", len(groups), validate_counts)
    for group in groups:
        skus = group["skus"]
        parent = group["parent"]
        titles = group_website_titles(skus, resolved)
        if len(titles) < 2:
            mark_group_title_missing(
                parent,
                resolved,
                "Grouped product-name link was not created because product titles were not available.",
            )
            logger.info(
                "Skipping grouped search link without enough website titles: parent=%s skus=%s titles=%s",
                parent["sku"],
                ",".join(skus),
                len(titles),
            )
            continue

        try:
            url, query = make_group_search_url_from_titles(titles)
        except ValueError:
            mark_group_title_missing(
                parent,
                resolved,
                "Grouped product-name link could not be built from the product titles.",
            )
            logger.warning(
                "Could not build grouped title query: parent=%s skus=%s",
                parent["sku"],
                ",".join(skus),
            )
            continue

        logger.info(
            "Prepared grouped search link: parent=%s skus=%s source=titles query=%s",
            parent["sku"],
            ",".join(skus),
            query,
        )
        prepared.append({"group": group, "skus": skus, "parent": parent, "url": url, "query": query})

    count_results: dict[str, dict] = {}
    if validate_counts and prepared:
        validation_requests = {item["url"]: item["skus"] for item in prepared}
        logger.info("Validating grouped search links with browser: links=%s", len(validation_requests))
        count_results = count_search_results_with_playwright(validation_requests)
        urls = list(validation_requests)
        for url in urls:
            result = count_results.get(url, {})
            if result.get("count_status") in {"ok", "blocked"}:
                continue
            logger.warning("Browser grouped validation did not finish cleanly; trying HTTP fallback: status=%s url=%s", result.get("count_status"), url)
            fallback_result = count_search_results(url)
            if fallback_result.get("count_status") == "ok":
                count_results[url] = fallback_result
            elif not result:
                count_results[url] = fallback_result

    for item in prepared:
        skus = item["skus"]
        parent = item["parent"]
        url = item["url"]
        query = item["query"]
        status = "grouped_search"
        message = f"Grouped Praktis search for {len(skus)} SKUs: {', '.join(skus)}."
        found_count = None
        found_skus: list[str] = []
        missing_skus: list[str] = []
        extra_skus: list[str] = []
        if validate_counts:
            count_result = count_results.get(url, {})
            count_status = count_result.get("count_status")
            if count_status == "ok":
                url = count_result.get("validated_url") or url
                found_count = count_result.get("found_count")
                found_skus = list(count_result.get("found_skus") or [])
                missing_skus = list(count_result.get("missing_skus") or [])
                extra_skus = list(count_result.get("extra_skus") or [])
                count_mismatch = bool(count_result.get("count_mismatch")) or (
                    found_count is not None and int(found_count) != len(skus)
                )
                if count_mismatch:
                    status = "group_count_mismatch"
                    message = (
                        count_result.get("count_message")
                        or "Grouped Praktis search result count does not match the brochure group; SKU checks were skipped."
                    )
                    logger.warning(
                        "Grouped search count mismatch: parent=%s expected=%s found=%s",
                        parent["sku"],
                        len(skus),
                        found_count,
                    )
                elif not found_skus:
                    status = "group_count_unknown"
                    message = "Grouped Praktis search loaded, but product SKUs could not be verified."
                elif not missing_skus and not extra_skus and not count_mismatch:
                    message = f"Grouped Praktis search validated all {len(skus)} brochure SKUs."
                    logger.info("Grouped search validated: parent=%s skus=%s", parent["sku"], ",".join(skus))
                else:
                    status = "group_count_mismatch"
                    details = []
                    if missing_skus:
                        details.append(f"missing: {', '.join(missing_skus)}")
                    if extra_skus:
                        details.append(f"extra: {', '.join(extra_skus)}")
                    message = "Grouped Praktis search SKU mismatch; " + "; ".join(details) + "."
                    logger.warning(
                        "Grouped search SKU mismatch: parent=%s missing=%s extra=%s",
                        parent["sku"],
                        ",".join(missing_skus),
                        ",".join(extra_skus),
                    )
            else:
                status = "group_count_unknown"
                message = count_result.get("count_message") or "Grouped Praktis search count could not be checked."
                logger.warning(
                    "Grouped search count unknown: parent=%s status=%s message=%s",
                    parent["sku"],
                    count_status,
                    message,
                )

        existing = resolved.get(parent["sku"], {})
        resolved[parent["sku"]] = {
            **existing,
            "sku": parent["sku"],
            "status": status,
            "source": "grouped-search",
            "url": url,
            "title": f"Grouped Praktis search: {query}",
            "message": message,
            "group_query": query,
            "group_skus": skus,
            "group_expected_count": len(skus),
            "group_found_count": found_count,
            "group_found_skus": found_skus,
            "group_missing_skus": missing_skus,
            "group_extra_skus": extra_skus,
        }


def mark_group_title_missing(parent: dict, resolved: dict[str, dict], message: str) -> None:
    sku = parent["sku"]
    parent["status"] = parent.get("status") or "group_title_missing"
    parent["message"] = append_item_message(parent.get("message", ""), message)
    existing = resolved.get(sku, {})
    resolved[sku] = {
        **existing,
        "sku": sku,
        "status": "group_title_missing",
        "source": "search-fallback",
        "url": search_url_for_sku(sku),
        "title": f"Praktis search for {sku}",
        "message": append_item_message(existing.get("message", ""), message),
    }


def group_website_titles(skus: list[str], resolved: dict[str, dict]) -> list[str]:
    titles: list[str] = []
    for sku in skus:
        link = resolved.get(sku, {})
        title = str(link.get("title") or "").strip()
        if link.get("status") == "linked" and title and not title.lower().startswith("praktis search for "):
            titles.append(title)
    return titles


def apply_price_check_result(link: dict, result: dict) -> None:
    for key in ("website_price", "price_status", "price_message"):
        if key in result:
            link[key] = result[key]

    if result.get("url") and should_replace_link(link, result):
        link.update(
            {
                "status": result.get("status", link.get("status", "search")),
                "source": result.get("source", "search-fallback"),
                "url": result["url"],
                "title": result.get("title", link.get("title", "")),
            }
        )


def should_replace_link(link: dict, result: dict) -> bool:
    if result.get("status") == "linked":
        return True
    if not link.get("url"):
        return True
    return link.get("source") in {"search-fallback", "playwright"} or is_generic_praktis_url(str(link.get("url", "")))


def ensure_search_fallbacks(resolved: dict[str, dict], skus: list[str]) -> None:
    for sku in skus:
        link = resolved.setdefault(sku, {"sku": sku})
        url = str(link.get("url") or "").strip()
        if url and not is_generic_praktis_url(url):
            continue

        status = link.get("status") or "link_not_found"
        if status in {"search", "unresolved"}:
            status = "search_only" if link.get("source") == "search-fallback" else "link_not_found"
        message = link.get("message") or "No exact Praktis product link was verified; SKU search link was used."
        link.update(
            {
                "sku": sku,
                "status": status,
                "source": "search-fallback",
                "url": search_url_for_sku(sku),
                "title": f"Praktis search for {sku}",
                "message": message,
            }
        )


def sanitize_resolved_links(resolved: dict[str, dict], skus: list[str]) -> None:
    for sku in skus:
        link = resolved.get(sku)
        if not link:
            continue
        url = str(link.get("url") or "").strip()
        if not is_generic_praktis_url(url):
            continue
        status = link.get("status") or "link_not_found"
        if status in {"search", "unresolved"}:
            status = "link_not_found"
        link.update(
            {
                "status": status,
                "source": "search-fallback",
                "url": search_url_for_sku(sku),
                "title": f"Praktis search for {sku}",
                "message": "Generic Praktis link was replaced with the SKU search link.",
            }
        )


def is_generic_praktis_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.netloc.lower() not in {"praktis.bg", "www.praktis.bg"}:
        return False
    path = parsed.path.strip("/").lower()
    if path in {"", "bg", "en"}:
        return True
    return path == "catalogsearch/result" and not parsed.query


def write_links(
    pdf_bytes: bytes,
    pages: list[PageText],
    detections: list[dict],
    resolved: dict[str, dict],
    debug_boxes: bool,
    link_annotations: bool = True,
) -> tuple[bytes, int]:
    reader = PdfReader(BytesIO(pdf_bytes))
    writer = PdfWriter()

    overlays = build_debug_overlay(pages, detections) if debug_boxes else None
    overlay_reader = PdfReader(BytesIO(overlays)) if overlays else None

    for index, source_page in enumerate(reader.pages):
        writer.add_page(source_page)
        if overlay_reader is not None:
            writer.pages[index].merge_page(overlay_reader.pages[index])

    linked_count = 0
    page_heights = {page.page_number: page.height for page in pages}
    for item in detections:
        if not link_annotations or not item.get("linkable", True):
            continue
        link = resolved.get(item["sku"], {})
        url = link.get("url")
        if not url:
            continue
        box = item["box"]
        height = page_heights[item["page"]]
        rect = (
            float(box["x"]),
            float(height - box["y"] - box["height"]),
            float(box["x"] + box["width"]),
            float(height - box["y"]),
        )
        writer.add_annotation(item["page"] - 1, Link(rect=rect, url=url, border=[0, 0, 0]))
        linked_count += 1

    out = BytesIO()
    writer.write(out)
    return out.getvalue(), linked_count


def build_debug_overlay(pages: list[PageText], detections: list[dict]) -> bytes:
    out = BytesIO()
    first = pages[0]
    c = canvas.Canvas(out, pagesize=(first.width, first.height))
    by_page: dict[int, list[dict]] = {}
    for item in detections:
        by_page.setdefault(item["page"], []).append(item)

    for page in pages:
        c.setPageSize((page.width, page.height))
        c.setStrokeColorRGB(0.05, 0.25, 1.0)
        c.setLineWidth(0.6)
        for item in by_page.get(page.page_number, []):
            if not item.get("linkable", True):
                continue
            box = item["box"]
            c.rect(box["x"], page.height - box["y"] - box["height"], box["width"], box["height"], stroke=1, fill=0)
        c.showPage()

    c.save()
    return out.getvalue()


def estimate_row_step(tops: Iterable[float]) -> float:
    values = sorted(tops)
    diffs: list[float] = []
    for i, left in enumerate(values):
        for right in values[i + 1 :]:
            diff = right - left
            if 80 <= diff <= 145:
                diffs.append(diff)
    if not diffs:
        return 111.72

    buckets: dict[int, int] = {}
    for diff in diffs:
        key = round(diff)
        buckets[key] = buckets.get(key, 0) + 1
    best = max(buckets, key=buckets.get)
    close = [diff for diff in diffs if abs(diff - best) <= 3]
    return median(close) or 111.72


def estimate_sku_inset(candidates: list[Word]) -> float:
    leftish = [word.x0 for word in candidates if word.x0 < 60]
    if leftish:
        return median(leftish)
    return min(word.x0 for word in candidates)


def median(values: Iterable[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _detection(
    page: PageText,
    sku: str,
    box_type: str,
    box: dict,
    confidence: float,
    brochure_price_text: str = "",
    brochure_price: float | None = None,
    linkable: bool = True,
    status: str = "",
    message: str = "",
    brochure_price_not_defined: bool = False,
    brochure_price_from_marker: bool = False,
    from_price_text: str = "",
    from_price: float | None = None,
) -> dict:
    if brochure_price_from_marker and from_price is None:
        from_price = brochure_price
    if brochure_price_from_marker and not from_price_text:
        from_price_text = brochure_price_text
    return {
        "page": page.page_number,
        "sku": sku,
        "box_type": box_type,
        "box": box,
        "confidence": confidence,
        "brochure_price_text": brochure_price_text,
        "brochure_price": brochure_price,
        "brochure_price_not_defined": brochure_price_not_defined,
        "brochure_price_from_marker": brochure_price_from_marker,
        "from_price_text": from_price_text,
        "from_price": from_price,
        "linkable": linkable,
        "status": status,
        "message": message,
    }


def _int_option(options: dict, name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(options.get(name, default))
    except (TypeError, ValueError):
        value = default
    return int(clamp(value, minimum, maximum))


def _float_option(options: dict, name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(options.get(name, default))
    except (TypeError, ValueError):
        value = default
    return clamp(value, minimum, maximum)


def output_name(pdf_name: str) -> str:
    base = (pdf_name or "brochure.pdf").rsplit(".", 1)[0]
    return f"{base}-with-praktis-links.pdf"


def _to_base64(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("ascii")
