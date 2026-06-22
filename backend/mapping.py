import csv
import io
import json
import re
from typing import Dict


def normalize_sku(value: object) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits


def normalize_url(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("http://") or text.startswith("https://"):
        return text
    if text.startswith("/"):
        return "https://praktis.bg" + text
    return "https://" + text


def parse_mapping(data: bytes | None, filename: str = "") -> Dict[str, dict]:
    if not data:
        return {}

    text = data.decode("utf-8-sig", errors="replace").strip()
    if not text:
        return {}

    if filename.lower().endswith(".json") or text[0] in "[{":
        return _parse_json_mapping(text)
    return _parse_csv_mapping(text)


def _parse_json_mapping(text: str) -> Dict[str, dict]:
    payload = json.loads(text)
    mapping: Dict[str, dict] = {}

    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            sku = normalize_sku(item.get("sku") or item.get("code") or item.get("product_code"))
            url = normalize_url(item.get("url") or item.get("link"))
            if sku and url:
                mapping[sku] = {
                    "sku": sku,
                    "url": url,
                    "title": str(item.get("title") or item.get("name") or ""),
                    "source": "mapping",
                }
        return mapping

    if isinstance(payload, dict):
        for raw_sku, raw_value in payload.items():
            sku = normalize_sku(raw_sku)
            value = {"url": raw_value} if isinstance(raw_value, str) else raw_value
            if not isinstance(value, dict):
                continue
            url = normalize_url(value.get("url") or value.get("link"))
            if sku and url:
                mapping[sku] = {
                    "sku": sku,
                    "url": url,
                    "title": str(value.get("title") or value.get("name") or ""),
                    "source": "mapping",
                }

    return mapping


def _parse_csv_mapping(text: str) -> Dict[str, dict]:
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return {}

    first = [cell.strip().lower() for cell in rows[0]]
    has_header = any(cell in {"sku", "code", "product_code", "url", "link", "title", "name"} for cell in first)
    headers = first if has_header else ["sku", "url", "title"]
    data_rows = rows[1:] if has_header else rows
    mapping: Dict[str, dict] = {}

    for row in data_rows:
        record = {headers[index]: value.strip() for index, value in enumerate(row) if index < len(headers)}
        sku = normalize_sku(record.get("sku") or record.get("code") or record.get("product_code") or (row[0] if row else ""))
        url = normalize_url(record.get("url") or record.get("link") or (row[1] if len(row) > 1 else ""))
        title = record.get("title") or record.get("name") or (row[2] if len(row) > 2 else "")
        if sku and url:
            mapping[sku] = {"sku": sku, "url": url, "title": title, "source": "mapping"}

    return mapping
