from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import math
import re
from typing import Iterable
from urllib.parse import urlparse

import pdfplumber
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Link
from reportlab.pdfgen import canvas

from .excel_prices import parse_excel_prices
from .mapping import parse_mapping
from .praktis_playwright import compare_prices_with_playwright
from .resolver import compare_website_price, resolve_skus, search_url_for_sku


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


SKU_RE = re.compile(r"\d{5,12}")
SPLIT_SKU_TOKEN_RE = re.compile(r"^[\d,-]+$")
HEADER_SKU_TEXT_INSET = 4.0
HEADER_PRICE_BOX_PADDING = 6.5
HEADER_PAGE_EDGE_TOLERANCE = 18.0
HEADER_EDGE_TOUCH_TOLERANCE = 6.0
HEADER_PRICE_GROUP_GAP = 68.0


def process_brochure(
    pdf_bytes: bytes,
    pdf_name: str,
    mapping_bytes: bytes | None,
    mapping_name: str,
    options: dict | None = None,
    excel_bytes: bytes | None = None,
    excel_name: str = "",
) -> dict:
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

    pages = extract_text_pages(pdf_bytes, min_digits, max_digits)
    process_pages, page_scope = select_processing_pages(pages, options)
    detections = detect_boxes(
        process_pages,
        min_digits,
        max_digits,
        box_padding,
        skip_last_page=page_scope == "all",
    )
    skus = sorted({item["sku"] for item in detections})
    mapping = parse_mapping(mapping_bytes, mapping_name)
    resolved = resolve_skus(skus, mapping, live_lookup, fallback_search)
    if website_prices:
        attach_price_comparisons(detections, resolved)
    if excel_prices_enabled:
        excel_prices = parse_excel_prices(excel_bytes, excel_name)
        attach_excel_comparisons(detections, excel_prices)
    if website_prices and excel_prices_enabled:
        attach_triple_comparisons(detections)
    else:
        mark_triple_not_checked(detections)
    if mode == "excel_prices":
        mark_price_only_statuses(detections, resolved)
    if fallback_search:
        ensure_search_fallbacks(resolved, skus)
    sanitize_resolved_links(resolved, skus)
    linked_pdf, linked_count = write_links(pdf_bytes, pages, detections, resolved, debug_boxes, link_annotations)

    rows = []
    for item in detections:
        link = resolved.get(item["sku"], {})
        rows.append(
            {
                "page": item["page"],
                "sku": item["sku"],
                "status": link.get("status", "unresolved"),
                "box_type": item["box_type"],
                "url": link.get("url", ""),
                "title": link.get("title", ""),
                "message": link.get("message", ""),
                "brochure_price": item.get("brochure_price"),
                "brochure_price_text": item.get("brochure_price_text", ""),
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
    if str(options.get("pageMode") or "all") != "single":
        return pages, "all"

    try:
        page_number = int(str(options.get("pageNumber") or "").strip())
    except ValueError:
        raise ValueError("Enter a valid page number to process.")

    if page_number < 1 or page_number > len(pages):
        raise ValueError(f"Page number must be between 1 and {len(pages)}.")

    return [pages[page_number - 1]], "single"


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
            pages.append(PageText(index, float(page.width), float(page.height), words, find_footer_top(page)))
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
    if sequence[0].text.isdigit() and min_digits <= len(sequence[0].text) <= max_digits:
        return []
    original = "".join(word.text for word in sequence)
    first = original.split(",", 1)[0].split("-", 1)[0].strip()
    if not first.isdigit() or not min_digits <= len(first) <= max_digits:
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
        if first.isdigit() and min_digits <= len(first) <= max_digits:
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
        if first.isdigit() and min_digits <= len(first) <= max_digits:
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
        detections.extend(item_detections)
        detections.extend(detect_variant_table_rows(page, item_detections, min_digits, max_digits))
    return detections


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
            brochure_price_text, brochure_price = find_brochure_price(page, box, word)
            detections.append(_detection(page, word.text, "item", box, 0.88, brochure_price_text, brochure_price))

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

    for word in page.words:
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
        euro_window = find_table_euro_window(page, word, right_limit)
        if not euro_window:
            continue
        price_candidates = [
            candidate
            for candidate in table_price_candidates(line, word.x1 + 8, right_limit)
            if euro_window[0] <= candidate[0] and candidate[1] <= euro_window[1]
        ]

        if not price_candidates:
            continue

        price_candidates.sort(key=lambda item: item[0])
        chosen = price_candidates[0]
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
            )
        )

    return dedupe_detections(variants)


def table_price_candidates(line: list[Word], left: float, right: float) -> list[tuple[float, float, float, str]]:
    candidates: list[tuple[float, float, float, str]] = []
    tokens = [word for word in line if word.x0 > left and word.x0 < right]

    for word in tokens:
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


def find_table_euro_window(page: PageText, sku_word: Word, right_limit: float) -> tuple[float, float] | None:
    euro_headers = [
        word
        for word in page.words
        if is_euro_header(word.text)
        and word.x0 > sku_word.x1 + 4
        and word.x0 < right_limit
        and -4 <= sku_word.top - word.top <= 90
    ]
    if not euro_headers:
        return None

    for euro in sorted(euro_headers, key=lambda item: (sku_word.top - item.top, item.x0 - sku_word.x1)):
        if not has_table_sku_column_header(page, sku_word, euro):
            continue
        same_header = same_line_words(page, euro, 5)
        leva_headers = [
            word
            for word in same_header
            if is_leva_header(word.text)
            and word.x0 > euro.x1
            and word.x0 < right_limit + 4
        ]
        left = max(sku_word.x1 + 1, euro.x0 - 10)
        right = (min(word.x0 for word in leva_headers) - 2) if leva_headers else euro.x1 + 42
        if right > left + 4:
            return left, right
    return None


def has_table_sku_column_header(page: PageText, sku_word: Word, euro_header: Word) -> bool:
    for word in same_line_words(page, euro_header, 6):
        if word.x0 > sku_word.x0 + 10 or word.x1 < sku_word.x0 - 28:
            continue
        if is_euro_header(word.text) or is_leva_header(word.text):
            continue
        if is_priceish_text(word.text) or SKU_RE.search(word.text):
            continue
        return True
    return False


def is_euro_header(text: str) -> bool:
    lowered = str(text or "").lower()
    return "€" in lowered or "eur" in lowered


def is_leva_header(text: str) -> bool:
    lowered = str(text or "").lower()
    return lowered.startswith("лв") or lowered.startswith("lv") or lowered.startswith("bgn")


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
        ]
        row_headers = []
        for index, word in enumerate(row_words):
            right_edge = (
                row_words[index + 1].x0 - HEADER_SKU_TEXT_INSET
                if index + 1 < len(row_words)
                else page.width
            )
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
        if bottom <= header["top"] + 18:
            continue
        box = {
            "x": clamp(header["left"] - box_padding, 0, page.width),
            "y": clamp(header["top"] - box_padding, 0, page.height),
            "width": clamp(header["right"] - header["left"] + box_padding * 2, 8, page.width - header["left"]),
            "height": clamp(bottom - header["top"] + box_padding * 2, 8, page.height - header["top"]),
        }
        price_text = header["price_word"].text
        detections.append(
            _detection(
                page,
                header["word"].text,
                "item",
                box,
                0.94,
                price_text,
                brochure_price_to_decimal(price_text),
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
    prices = []
    for word in page.words:
        if not is_header_price_word(word, sku_word, right_edge):
            continue
        prices.append(word)
    prices.extend(find_split_header_price_words(page, sku_word, right_edge))
    if not prices:
        return None
    return sorted(prices, key=lambda item: (item.x0, item.top))[0]


def is_header_price_word(word: Word, sku_word: Word, right_edge: float) -> bool:
    if not word.text.isdigit():
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
    digits = [word for word in page.words if word.text.isdigit()]
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
        if word.text.isdigit()
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


def dedupe_detections(detections: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    clean: list[dict] = []
    for item in sorted(detections, key=lambda d: (d["page"], d["box"]["y"], d["box"]["x"], d["sku"])):
        key = (item["page"], item["sku"], round(item["box"]["x"]), round(item["box"]["y"]))
        if key in seen:
            continue
        seen.add(key)
        clean.append(item)
    return clean


def is_sku(text: str, min_digits: int, max_digits: int) -> bool:
    return text.isdigit() and min_digits <= len(text) <= max_digits


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
        if first.isdigit() and min_digits <= len(first) <= max_digits:
            return True
    return False


def same_line_words(page: PageText, word: Word, tolerance: float) -> list[Word]:
    return sorted([other for other in page.words if abs(other.mid_y - word.mid_y) <= tolerance], key=lambda item: item.x0)


def find_brochure_price(page: PageText, box: dict, sku_word: Word) -> tuple[str, float | None]:
    top_limit = box["y"] - 2
    bottom_limit = min(box["y"] + 78, sku_word.bottom + 18)
    left = box["x"]
    right = box["x"] + box["width"]
    candidates: list[Word] = []

    for word in page.words:
        if not word.text.isdigit():
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
        return "", None

    candidates.sort(key=lambda item: (abs(item.top - sku_word.top), -item.height))
    raw = candidates[0].text
    return raw, brochure_price_to_decimal(raw)


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
    if not raw.isdigit():
        return None
    return round(int(raw) / 100, 2)


def parse_table_price_word(raw: str) -> float | None:
    text = str(raw or "").replace("\xa0", "").replace(" ", "")
    match = re.fullmatch(r"([0-9]{1,5})[,.]([0-9]{1,2})", text)
    if not match:
        return None
    return round(float(match.group(1)) + int(match.group(2).ljust(2, "0")) / 100, 2)


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
        if brochure_price is None:
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
        if excel_item:
            item["excel_price"] = excel_item["excel_price"]
            item["excel_row"] = excel_item.get("excel_row")
        else:
            item["excel_price"] = None

        status, message = compare_two_prices(
            item.get("brochure_price"),
            item.get("excel_price"),
            "Excel",
        )
        item["excel_status"] = status
        item["excel_message"] = message


def attach_triple_comparisons(detections: list[dict]) -> None:
    for item in detections:
        brochure_price = item.get("brochure_price")
        website_price = item.get("website_price") or None
        excel_price = item.get("excel_price")

        if brochure_price is None:
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
            "status": "price_only",
            "message": "Excel price check mode does not place links.",
        }


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
) -> dict:
    return {
        "page": page.page_number,
        "sku": sku,
        "box_type": box_type,
        "box": box,
        "confidence": confidence,
        "brochure_price_text": brochure_price_text,
        "brochure_price": brochure_price,
        "linkable": linkable,
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
