from __future__ import annotations

from io import BytesIO
import re
from zipfile import ZipFile
import xml.etree.ElementTree as ET


NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
SKU_RE = re.compile(r"\d{5,12}")


def parse_excel_prices(data: bytes | None, filename: str = "") -> dict[str, dict]:
    if not data:
        return {}
    if filename and not filename.lower().endswith(".xlsx"):
        raise ValueError("Upload an .xlsx Excel file for Excel price checks.")

    with ZipFile(BytesIO(data)) as archive:
        shared_strings = read_shared_strings(archive)
        sheet_name = first_sheet_name(archive)
        root = ET.fromstring(archive.read(sheet_name))

    prices: dict[str, dict] = {}
    for row in root.findall(".//x:row", NS):
        row_number = int(float(row.get("r", "0") or 0))
        values = {
            column_name(cell.get("r", "")): cell_value(cell, shared_strings)
            for cell in row.findall("x:c", NS)
        }
        sku = normalize_sku(values.get("M"))
        price = parse_price(values.get("U"))
        if not sku or price is None:
            continue
        prices[sku] = {
            "sku": sku,
            "excel_price": price,
            "excel_row": row_number,
        }

    return prices


def read_shared_strings(archive: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for item in root.findall("x:si", NS):
        strings.append("".join(node.text or "" for node in item.findall(".//x:t", NS)))
    return strings


def first_sheet_name(archive: ZipFile) -> str:
    names = [
        name
        for name in archive.namelist()
        if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
    ]
    if not names:
        raise ValueError("No worksheet was found in the Excel file.")
    return sorted(names)[0]


def cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    if cell.get("t") == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//x:t", NS)).strip()

    node = cell.find("x:v", NS)
    if node is None or node.text is None:
        return ""

    value = node.text.strip()
    if cell.get("t") == "s":
        try:
            return shared_strings[int(value)].strip()
        except (IndexError, ValueError):
            return ""
    return value


def column_name(reference: str) -> str:
    match = re.match(r"[A-Z]+", reference or "")
    return match.group(0) if match else ""


def normalize_sku(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".", 1)[0]
    match = SKU_RE.search(text.split(",", 1)[0].split("-", 1)[0])
    return match.group(0) if match else ""


def parse_price(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("\xa0", "").replace(" ", "").replace(",", ".")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return round(float(match.group(0)), 2)
    except ValueError:
        return None
