from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from openpyxl import load_workbook


_THICKNESS_RE = re.compile(r"\bGR\.?\s*(\d+(?:[.,]\d+)?)\s*MM\b", re.IGNORECASE)
_BOARD_RE = re.compile(r"\bP[ŁL]YTA\b", re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")
_BLACK_MASCULINE_RE = re.compile(r"\bCZARNY\b", re.IGNORECASE)
_BLACK_ANTISTATIC_RE = re.compile(
    r"\bCZARN(?:A|Y)?\s*[-/]?\s*(?:ANTYSTATYCZNA|ANTYSTATYK|ANTYST|ANT|AST)\b",
    re.IGNORECASE,
)
_ANTISTATIC_SHORT_RE = re.compile(r"\b(?:ANTYSTATYK|ANTYST|ANT|AST)\b", re.IGNORECASE)
_BLUE_SHORT_RE = re.compile(r"\bNIEB(?:IESKI)?\b", re.IGNORECASE)
_NATURAL_SHORT_RE = re.compile(r"\bNATUR\b", re.IGNORECASE)
_NATURAL_DEFAULT_MATERIALS = {"PA6", "POM H"}
_FORMAT_RE = re.compile(
    r"^\s*(\d+(?:[.,]\d+)?)\s*[x×]\s*(\d+(?:[.,]\d+)?)\s*(?:mm)?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MaterialCatalogEntry:
    material: str
    thickness: float
    net_price_m2: float
    gross_price_m2: float
    product_name: str
    source_sheet: str = ""
    width: float = 0.0
    height: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _header_key(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").strip().casefold())
    # Supplier workbooks freely mix Polish and ASCII headers.  ``ł`` does not
    # decompose under NFKD, so normalize it explicitly for header discovery.
    text = text.replace("ł", "l")
    text = "".join(char for char in text if not unicodedata.combining(char))
    return _SPACE_RE.sub(" ", text)


def _number(value: object) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("\u00a0", " ").strip()
    # Some supplier cells include a currency/unit (e.g. "123,45 zł/m²").
    # Extract the numeric price instead of classifying that board as absent.
    match = re.search(r"[-+]?\d[\d .,'\u00a0]*", text)
    if not match:
        return 0.0
    number = match.group(0).replace("\u00a0", "").replace(" ", "").replace("'", "")
    try:
        if "," in number and "." in number:
            if number.rfind(",") > number.rfind("."):
                number = number.replace(".", "").replace(",", ".")
            else:
                number = number.replace(",", "")
        else:
            number = number.replace(",", ".")
        return float(number)
    except (TypeError, ValueError):
        return 0.0


def _format_dimensions(value: object) -> tuple[float, float] | None:
    """Parse supplier format headers such as ``1000x2000`` or ``1000 × 2000 mm``."""
    match = _FORMAT_RE.match(str(value or ""))
    if not match:
        return None
    width = _number(match.group(1))
    height = _number(match.group(2))
    return (width, height) if width > 0 and height > 0 else None


def _normalize_color(value: object) -> str:
    color = _SPACE_RE.sub(" ", str(value or "").strip())
    if not color:
        return ""
    # The Boral sheet currently contains a supplier typo ("natual"). Keep
    # normalization deliberately broad so future natural variants stay grouped.
    if re.search(r"\bnat(?:u|ua|a)?l", color, re.IGNORECASE):
        return "NATURALNA"
    if re.search(r"\bnatural", color, re.IGNORECASE):
        return "NATURALNA"
    if re.search(r"\bczarn", color, re.IGNORECASE):
        return "CZARNA"
    return color.upper()


def _matrix_catalog_entries(sheet) -> list[MaterialCatalogEntry]:
    """Read a matrix price list: rows are thickness/color, columns are formats.

    The layout is intentionally discovered from labels instead of fixed cell
    addresses. A supplier can append formats (columns), variants (rows), or a
    whole material worksheet without changing the application.
    """
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        return []

    header_row = None
    thickness_col = color_col = None
    for row_index, row in enumerate(rows[:20]):
        headers = {_header_key(value): col for col, value in enumerate(row)}
        thickness_col = next((col for key, col in headers.items() if "grubo" in key), None)
        color_col = next((col for key, col in headers.items() if "kolor" in key or "barw" in key), None)
        if thickness_col is not None and color_col is not None:
            header_row = row_index
            break
    if header_row is None or thickness_col is None or color_col is None:
        return []

    format_columns: dict[int, tuple[float, float]] = {}
    first_data_row = header_row + 1
    # Format headers may be placed in the next row under a merged "Format"
    # cell (as in Boral), or directly alongside the labels.
    for row_index in range(header_row, min(len(rows), header_row + 5)):
        for col, value in enumerate(rows[row_index]):
            dimensions = _format_dimensions(value)
            if dimensions is not None:
                format_columns[col] = dimensions
                first_data_row = max(first_data_row, row_index + 1)
    if not format_columns:
        return []

    entries: list[MaterialCatalogEntry] = []
    for row in rows[first_data_row:]:
        thickness = _number(row[thickness_col] if thickness_col < len(row) else None)
        if thickness <= 0:
            continue
        color = _normalize_color(row[color_col] if color_col < len(row) else None)
        product = f"{sheet.title.strip()} PŁYTA{f' {color}' if color else ''}".strip()
        for col, (width, height) in format_columns.items():
            price = _number(row[col] if col < len(row) else None)
            # A blank price is not a zero-priced board: that variant simply is
            # not supplied in this color/thickness/format.
            if price <= 0:
                continue
            entries.append(
                MaterialCatalogEntry(
                    material=sheet.title.strip(),
                    thickness=thickness,
                    net_price_m2=price,
                    gross_price_m2=price,
                    product_name=product,
                    source_sheet=sheet.title,
                    width=width,
                    height=height,
                )
            )
    return entries


def _material_name(product_name: str, sheet_name: str) -> str:
    name = _BOARD_RE.sub(" ", product_name)
    name = _THICKNESS_RE.sub(" ", name)
    name = _SPACE_RE.sub(" ", name).strip(" -.,")
    return name or sheet_name.strip() or "Płyta"


def catalog_family_label(entry: MaterialCatalogEntry) -> str:
    """Return the complete board family without the variable thickness.

    Supplier workbooks often switch grammatical gender between ``CZARNY`` and
    ``CZARNA`` for the same board.  A board is displayed consistently as
    ``CZARNA`` so those rows become one searchable family in the selector.
    """
    label = _THICKNESS_RE.sub(" ", entry.product_name)
    label = _BLACK_ANTISTATIC_RE.sub("CZARNA ANTYSTATYCZNA", label)
    label = _BLACK_MASCULINE_RE.sub("CZARNA", label)
    label = _ANTISTATIC_SHORT_RE.sub("ANTYSTATYCZNA", label)
    label = _BLUE_SHORT_RE.sub("NIEBIESKA", label)
    label = _NATURAL_SHORT_RE.sub("NATURALNA", label)
    if (
        entry.material.strip().upper() in _NATURAL_DEFAULT_MATERIALS
        and "NATURAL" not in label.upper()
        and not any(color in label.upper() for color in ("CZARNA", "NIEBIESKA", "ZIELONA"))
    ):
        label = f"{label} NATURALNA"
    return _SPACE_RE.sub(" ", label).strip(" -.,")


def read_material_catalog(path: str | Path) -> list[MaterialCatalogEntry]:
    """Read board prices from a supplier workbook.

    Only square-metre board products are accepted. Pipes, rods and profiles
    normally use ``mb`` and are deliberately ignored. Column order and sheet
    names may change; columns are located by their normalized headers.
    """
    workbook = load_workbook(path, data_only=True, read_only=True)
    entries: list[MaterialCatalogEntry] = []
    seen: set[tuple[str, float, float, float, float, float]] = set()

    for sheet in workbook.worksheets:
        matrix_entries = _matrix_catalog_entries(sheet)
        if matrix_entries:
            for entry in matrix_entries:
                signature = (
                    catalog_family_label(entry).casefold(),
                    entry.thickness,
                    entry.width,
                    entry.height,
                    entry.gross_price_m2,
                    entry.net_price_m2,
                )
                if signature not in seen:
                    seen.add(signature)
                    entries.append(entry)
            continue
        rows = sheet.iter_rows(values_only=True)
        try:
            header = next(rows)
        except StopIteration:
            continue
        columns = {_header_key(value): index for index, value in enumerate(header)}
        name_col = next((index for key, index in columns.items() if key in {"nazwa", "produkt", "opis"}), None)
        unit_col = next((index for key, index in columns.items() if key in {"j.m.", "jm", "jednostka", "j.m"}), None)
        net_col = next((index for key, index in columns.items() if "netto" in key), None)
        gross_col = next((index for key, index in columns.items() if "brutto" in key), None)
        if name_col is None or unit_col is None:
            continue

        for row in rows:
            product = str(row[name_col] or "").strip() if name_col < len(row) else ""
            unit = _header_key(row[unit_col] if unit_col < len(row) else "")
            match = _THICKNESS_RE.search(product)
            if unit not in {"m2", "m²"} or not match or not _BOARD_RE.search(product):
                continue
            thickness = _number(match.group(1))
            if thickness <= 0:
                continue
            net = _number(row[net_col]) if net_col is not None and net_col < len(row) else 0.0
            gross = _number(row[gross_col]) if gross_col is not None and gross_col < len(row) else 0.0
            material = _material_name(product, sheet.title)
            signature = (material.casefold(), thickness, 0.0, 0.0, net, gross)
            if signature in seen:
                continue
            seen.add(signature)
            entries.append(MaterialCatalogEntry(material, thickness, net, gross, product, sheet.title))

    workbook.close()
    return sorted(
        entries,
        key=lambda item: (
            catalog_family_label(item).casefold(),
            item.thickness,
            item.width,
            item.height,
            item.net_price_m2,
        ),
    )


def catalog_from_dicts(items: Iterable[dict[str, object]]) -> list[MaterialCatalogEntry]:
    from core.validation import safeNumber

    def number(item: dict[str, object], key: str) -> float:
        value = safeNumber(item.get(key, 0) or 0)
        if value is None or value < 0:
            raise ValueError(f"Nieprawidłowa wartość katalogu: {key}")
        return value

    result: list[MaterialCatalogEntry] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            result.append(MaterialCatalogEntry(
                material=str(item.get("material", "")).strip(),
                thickness=number(item, "thickness"),
                net_price_m2=number(item, "net_price_m2"),
                gross_price_m2=number(item, "gross_price_m2"),
                product_name=str(item.get("product_name", "")).strip(),
                source_sheet=str(item.get("source_sheet", "")).strip(),
                width=number(item, "width"),
                height=number(item, "height"),
            ))
        except (TypeError, ValueError):
            continue
    return [item for item in result if item.material and item.thickness > 0]


def catalog_revision(path: str | Path) -> str:
    """Return a stable fingerprint for a bundled price-list file."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def merge_bundled_catalog(
    local_entries: Iterable[MaterialCatalogEntry],
    bundled_entries: Iterable[MaterialCatalogEntry],
) -> list[MaterialCatalogEntry]:
    """Update catalog positions shipped with the app without dropping local extras.

    A material family and its thickness identify a commercial position.  The
    bundled value wins for that position (new centrally released prices and
    labels), while a local position not present in the bundled catalogue stays
    available to the operator.
    """
    def key(entry: MaterialCatalogEntry) -> tuple[str, float, float, float]:
        return (
            catalog_family_label(entry).casefold(),
            round(float(entry.thickness), 3),
            round(float(entry.width), 3),
            round(float(entry.height), 3),
        )

    merged = {key(entry): entry for entry in local_entries}
    merged.update({key(entry): entry for entry in bundled_entries})
    return sorted(
        merged.values(),
        key=lambda entry: (catalog_family_label(entry).casefold(), entry.thickness, entry.product_name.casefold()),
    )
