from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import math
import re
from typing import Iterable

import pdfplumber
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Link
from reportlab.pdfgen import canvas

from .mapping import parse_mapping
from .resolver import compare_website_price, resolve_skus


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


SKU_RE = re.compile(r"\d{5,12}")


def process_brochure(
    pdf_bytes: bytes,
    pdf_name: str,
    mapping_bytes: bytes | None,
    mapping_name: str,
    options: dict,
) -> dict:
    min_digits = _int_option(options, "minDigits", 5, 5, 12)
    max_digits = _int_option(options, "maxDigits", 12, min_digits, 12)
    box_padding = _float_option(options, "boxPadding", 0, 0, 16)
    live_lookup = bool(options.get("liveLookup"))
    fallback_search = bool(options.get("fallbackSearch"))
    debug_boxes = bool(options.get("debugBoxes"))
    compare_prices = bool(options.get("comparePrices"))

    pages = extract_text_pages(pdf_bytes, min_digits, max_digits)
    detections = detect_boxes(pages, min_digits, max_digits, box_padding)
    skus = sorted({item["sku"] for item in detections})
    mapping = parse_mapping(mapping_bytes, mapping_name)
    resolved = resolve_skus(skus, mapping, live_lookup, fallback_search)
    if compare_prices:
        attach_price_comparisons(detections, resolved)
    linked_pdf, linked_count = write_links(pdf_bytes, pages, detections, resolved, debug_boxes)

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
                "confidence": item["confidence"],
                "box": item["box"],
            }
        )

    linked_skus = {row["sku"] for row in rows if row["url"]}
    blocked = sum(1 for sku in skus if resolved.get(sku, {}).get("status") == "blocked")
    price_compared = sum(1 for row in rows if row.get("price_status") in {"match", "different"})
    price_matched = sum(1 for row in rows if row.get("price_status") == "match")
    price_different = sum(1 for row in rows if row.get("price_status") == "different")

    return {
        "outputFileName": output_name(pdf_name),
        "pdfBase64": _to_base64(linked_pdf),
        "summary": {
            "pages": len(pages),
            "detections": len(detections),
            "uniqueSkus": len(skus),
            "linkedSkus": len(linked_skus),
            "linkedAnnotations": linked_count,
            "unresolvedSkus": len(skus) - len(linked_skus),
            "blockedLookups": blocked,
            "priceCompared": price_compared,
            "priceMatched": price_matched,
            "priceDifferent": price_different,
        },
        "rows": rows,
    }


def extract_text_pages(pdf_bytes: bytes, min_digits: int = 5, max_digits: int = 12) -> list[PageText]:
    pages: list[PageText] = []
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            raw_words = page.extract_words(x_tolerance=1, y_tolerance=3, keep_blank_chars=False, use_text_flow=False)
            words: list[Word] = []
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
                words.extend(expand_sku_fragments(base, min_digits, max_digits))
            pages.append(PageText(index, float(page.width), float(page.height), words))
    return pages


def expand_sku_fragments(word: Word, min_digits: int, max_digits: int) -> list[Word]:
    if "," in word.text:
        first = word.text.split(",", 1)[0].strip()
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


def detect_boxes(pages: list[PageText], min_digits: int, max_digits: int, box_padding: float) -> list[dict]:
    detections: list[dict] = []
    for page in pages:
        detections.extend(detect_page_boxes(page, min_digits, max_digits, box_padding))
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


def brochure_price_to_decimal(raw: str) -> float | None:
    if not raw.isdigit():
        return None
    return round(int(raw) / 100, 2)


def attach_price_comparisons(detections: list[dict], resolved: dict[str, dict]) -> None:
    checked: dict[str, dict] = {}
    for item in detections:
        sku = item["sku"]
        if sku not in checked:
            checked[sku] = compare_website_price(resolved.get(sku, {}))
            resolved.setdefault(sku, {}).update(checked[sku])

        brochure_price = item.get("brochure_price")
        website_price = resolved.get(sku, {}).get("website_price")
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


def write_links(
    pdf_bytes: bytes,
    pages: list[PageText],
    detections: list[dict],
    resolved: dict[str, dict],
    debug_boxes: bool,
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
) -> dict:
    return {
        "page": page.page_number,
        "sku": sku,
        "box_type": box_type,
        "box": box,
        "confidence": confidence,
        "brochure_price_text": brochure_price_text,
        "brochure_price": brochure_price,
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
