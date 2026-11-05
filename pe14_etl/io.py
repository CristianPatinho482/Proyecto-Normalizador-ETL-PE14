"""Capa de extracción: XLSX, CSV y SpreadsheetML 2003.

La capa no interpreta el dominio PE-14; solo entrega una matriz cruda, la fila
de cabecera y el periodo declarado. Toda decisión de negocio queda en
``normalize.py`` para preservar la separación de responsabilidades.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

from openpyxl import load_workbook

from .contracts import (
    MIN_SOURCE_WIDTH,
    REQUIRED_KEYS,
    ContractViolation,
    precondition,
)

SUPPORTED_SUFFIXES = {".xlsx", ".xlsm", ".csv", ".xml"}

_SPANISH_MONTHS: dict[str, int] = {
    "ENERO": 1,
    "ENE": 1,
    "FEBRERO": 2,
    "FEB": 2,
    "MARZO": 3,
    "MAR": 3,
    "ABRIL": 4,
    "ABR": 4,
    "MAYO": 5,
    "MAY": 5,
    "JUNIO": 6,
    "JUN": 6,
    "JULIO": 7,
    "JUL": 7,
    "AGOSTO": 8,
    "AGO": 8,
    "SEPTIEMBRE": 9,
    "SETIEMBRE": 9,
    "SEP": 9,
    "SET": 9,
    "OCTUBRE": 10,
    "OCT": 10,
    "NOVIEMBRE": 11,
    "NOV": 11,
    "DICIEMBRE": 12,
    "DIC": 12,
}

_MONTH_PATTERN = "|".join(
    re.escape(token) for token in sorted(_SPANISH_MONTHS, key=len, reverse=True)
)


@dataclass
class RawTable:
    """Matriz cruda leída de una fuente, sin interpretación de dominio."""

    path: Path
    kind: str
    rows: list[list[Any]]
    encoding: str | None = None
    delimiter: str | None = None
    sheet_name: str | None = None
    sha256: str = ""
    size_bytes: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class ExtractedTable:
    """Cabecera detectada y filas de datos con su número de fila original."""

    header: list[str]
    rows: list[tuple[int, list[Any]]]
    header_row: int
    width: int
    blank_rows_skipped: int = 0
    source_rows_total: int = 0

    @property
    def source_rows_nonblank(self) -> int:
        return len(self.rows)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clean_header(value: Any) -> str:
    """Normaliza una celda de cabecera preservando su semántica.

    Se eliminan BOM, saltos de línea y espacios repetidos. No se eliminan los
    guiones bajos porque distinguen las métricas ``Masc/Fem/NoBin/Total``.
    """

    if value is None:
        return ""
    text = str(value).replace("\ufeff", "").replace("\xa0", " ")
    text = text.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def canonical_key(value: Any) -> str:
    """Clave estable para comparar cabeceras PE-14."""

    text = clean_header(value).upper()
    text = text.replace(" ", "_")
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def _is_blank_row(row: Iterable[Any]) -> bool:
    return all(value is None or (isinstance(value, str) and not value.strip()) for value in row)


def read_raw_table(path: str | Path) -> RawTable:
    """Lee una fuente soportada y calcula su huella SHA-256.

    ``{P}``: la ruta existe, es un archivo regular, tiene tamaño positivo y su
    extensión está soportada.
    """

    source = Path(path)
    precondition(source.exists(), f"La fuente no existe: {source}")
    precondition(source.is_file(), f"La fuente no es un archivo: {source}")
    data = source.read_bytes()
    precondition(len(data) > 0, f"La fuente está vacía: {source}")
    suffix = source.suffix.lower()
    precondition(
        suffix in SUPPORTED_SUFFIXES,
        f"Extensión no soportada: {suffix or '(sin extensión)'}. "
        f"Use {', '.join(sorted(SUPPORTED_SUFFIXES))}.",
    )

    if suffix in {".xlsx", ".xlsm"}:
        raw = _read_xlsx(source)
    elif suffix == ".csv":
        raw = _read_csv(source, data)
    else:
        raw = _read_spreadsheetml(source, data)

    raw.sha256 = _sha256_bytes(data)
    raw.size_bytes = len(data)
    return raw


def _read_xlsx(path: Path) -> RawTable:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        candidates: list[tuple[int, str, list[list[Any]]]] = []
        for worksheet in workbook.worksheets:
            rows = [list(row) for row in worksheet.iter_rows(values_only=True)]
            nonblank = sum(1 for row in rows if not _is_blank_row(row))
            candidates.append((nonblank, worksheet.title, rows))
        if not candidates or max(item[0] for item in candidates) == 0:
            raise ContractViolation("P", f"El libro no contiene filas con datos: {path}")
        candidates.sort(key=lambda item: item[0], reverse=True)
        _, sheet_name, rows = candidates[0]
    finally:
        workbook.close()
    return RawTable(path=path, kind="xlsx", rows=rows, sheet_name=sheet_name)


def _decode_text(data: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ContractViolation("P", "No fue posible decodificar el archivo como UTF-8/CP1252.")


def _detect_delimiter(text: str) -> str:
    sample = "\n".join(text.splitlines()[:30])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
        return dialect.delimiter
    except csv.Error:
        pass
    # Fallback determinista: en PE-14 el separador institucional es ``;``.
    semicolons = sample.count(";")
    commas = sample.count(",")
    tabs = sample.count("\t")
    if semicolons >= commas and semicolons >= tabs and semicolons > 0:
        return ";"
    if tabs > semicolons and tabs > commas:
        return "\t"
    return ","


def _read_csv(path: Path, data: bytes) -> RawTable:
    text, encoding = _decode_text(data)
    delimiter = _detect_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = [list(row) for row in reader]
    return RawTable(
        path=path,
        kind="csv",
        rows=rows,
        encoding=encoding,
        delimiter=delimiter,
    )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_attribute(element: ET.Element, name: str) -> str | None:
    for key, value in element.attrib.items():
        if _local_name(key) == name:
            return value
    return None


def _xml_cell_value(cell: ET.Element) -> Any:
    data = next((child for child in cell if _local_name(child.tag) == "Data"), None)
    if data is None or data.text is None or data.text == "":
        return None
    value = data.text
    data_type = (_xml_attribute(data, "Type") or "String").strip()
    if data_type == "DateTime":
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if data_type == "Number":
        try:
            number = float(value)
        except ValueError:
            return value
        if math.isnan(number) or math.isinf(number):
            return value
        return int(number) if number.is_integer() else number
    if data_type == "Boolean":
        return value.strip().lower() in {"1", "true", "verdadero"}
    return value


def _read_spreadsheetml(path: Path, data: bytes) -> RawTable:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ContractViolation("P", f"XML SpreadsheetML inválido: {path}: {exc}") from exc

    best_rows: list[list[Any]] = []
    best_name: str | None = None
    for worksheet in (node for node in root.iter() if _local_name(node.tag) == "Worksheet"):
        name = _xml_attribute(worksheet, "Name") or "Worksheet"
        table = next(
            (node for node in worksheet.iter() if _local_name(node.tag) == "Table"),
            None,
        )
        if table is None:
            continue
        rows: list[list[Any]] = []
        for row_element in (node for node in table if _local_name(node.tag) == "Row"):
            row: list[Any] = []
            for cell in (node for node in row_element if _local_name(node.tag) == "Cell"):
                explicit_index = _xml_attribute(cell, "Index")
                if explicit_index is not None:
                    try:
                        target = max(int(explicit_index) - 1, len(row))
                    except ValueError:
                        target = len(row)
                    row.extend([None] * (target - len(row)))
                row.append(_xml_cell_value(cell))
            if not _is_blank_row(row):
                rows.append(row)
        if len(rows) > len(best_rows):
            best_rows, best_name = rows, name

    if not best_rows:
        raise ContractViolation("P", f"El XML no contiene una tabla con datos: {path}")
    return RawTable(path=path, kind="xml", rows=best_rows, sheet_name=best_name)


def detect_header(rows: list[list[Any]], max_scan: int = 10) -> int:
    """Localiza la fila de cabecera real (0-based) en las primeras filas.

    ``{P}``: existe una fila, dentro de las primeras ``max_scan``, que contiene
    la clave institucional ``IDENTIFICADOR_FICHA``.
    """

    precondition(bool(rows), "La matriz de origen está vacía.")
    for index, row in enumerate(rows[:max_scan]):
        keys = {canonical_key(value) for value in row if clean_header(value)}
        if "IDENTIFICADOR_FICHA" in keys:
            return index
    raise ContractViolation(
        "P",
        "Cabecera PE-14 no localizada: no se encontró IDENTIFICADOR_FICHA "
        f"en las primeras {max_scan} filas.",
    )


def extract_table(raw: RawTable, header_row: int | None = None) -> ExtractedTable:
    """Construye la tabla lógica, valida ``P_anchura`` y ``P_claves_fijas``."""

    header_index = detect_header(raw.rows) if header_row is None else header_row
    precondition(
        0 <= header_index < len(raw.rows),
        f"Fila de cabecera fuera de rango: {header_index}",
    )
    raw_header = raw.rows[header_index]
    last_nonempty = 0
    for index, value in enumerate(raw_header, start=1):
        if clean_header(value):
            last_nonempty = index
    header = [clean_header(value) for value in raw_header[:last_nonempty]]
    precondition(
        len(header) >= MIN_SOURCE_WIDTH,
        f"P_anchura violada: se detectaron {len(header)} columnas; "
        f"el mínimo es {MIN_SOURCE_WIDTH}.",
    )

    keys = {canonical_key(value) for value in header if canonical_key(value)}
    missing = [key for key in REQUIRED_KEYS if key not in keys]
    precondition(
        not missing,
        "P_claves_fijas violada: faltan columnas obligatorias "
        + ", ".join(missing),
    )

    rows: list[tuple[int, list[Any]]] = []
    blank = 0
    for offset, row in enumerate(raw.rows[header_index + 1 :], start=header_index + 2):
        if _is_blank_row(row):
            blank += 1
            continue
        padded = list(row[: len(header)])
        padded.extend([None] * (len(header) - len(padded)))
        rows.append((offset, padded))

    return ExtractedTable(
        header=header,
        rows=rows,
        header_row=header_index,
        width=len(header),
        blank_rows_skipped=blank,
        source_rows_total=len(raw.rows),
    )


def parse_period(value: int | str | None) -> int | None:
    """Convierte un periodo explícito a ``YYYYMM``; ``None`` si no es válido."""

    if value is None:
        return None
    text = str(value).strip().replace("-", "").replace("_", "").replace("/", "")
    if not re.fullmatch(r"20\d{4}", text):
        return None
    year = int(text[:4])
    month = int(text[4:])
    if not 1 <= month <= 12:
        return None
    return year * 100 + month


def _normalize_filename(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    return "".join(char for char in normalized if not unicodedata.combining(char)).upper()


def period_from_filename(name: str) -> int | None:
    """Infiere el periodo desde el nombre del archivo.

    Prioriza un periodo explícito ``YYYYMM``; después una fecha ``YYYY-MM``;
    después un mes español (completo o abreviado) acompañado de un año.
    """

    base = _normalize_filename(Path(name).stem)

    match = re.search(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(?!\d)", base)
    if match:
        return int(match.group(1)) * 100 + int(match.group(2))

    match = re.search(r"(?<!\d)(20\d{2})[-_/](0?[1-9]|1[0-2])(?!\d)", base)
    if match:
        return int(match.group(1)) * 100 + int(match.group(2))

    month_match = re.search(rf"\b({_MONTH_PATTERN})\b", base)
    if month_match:
        month = _SPANISH_MONTHS[month_match.group(1)]
        years = [int(year) for year in re.findall(r"(?<!\d)(20\d{2})(?!\d)", base)]
        if years:
            # Si hay varias fechas, se prefiere el año que aparece después del mes.
            after = re.findall(
                r"(?<!\d)(20\d{2})(?!\d)", base[month_match.end() :]
            )
            year = int(after[0]) if after else years[0]
            return year * 100 + month

    # Fecha ISO o día-mes-año cuando no hay nombre de mes.
    match = re.search(r"(?<!\d)(20\d{2})[-_/](\d{1,2})[-_/](\d{1,2})(?!\d)", base)
    if match:
        month = int(match.group(2))
        if 1 <= month <= 12:
            return int(match.group(1)) * 100 + month
    match = re.search(r"(?<!\d)(\d{1,2})[-_/](\d{1,2})[-_/](20\d{2})(?!\d)", base)
    if match:
        month = int(match.group(2))
        if 1 <= month <= 12:
            return int(match.group(3)) * 100 + month
    return None


def period_from_preamble(
    rows: list[list[Any]], header_row: int, max_rows: int = 6
) -> int | None:
    """Busca un periodo ``YYYYMM`` en las filas institucionales previas."""

    limit = min(max(header_row, 0), max_rows, len(rows))
    text = ";".join(
        " ".join(str(value) for value in row if value is not None)
        for row in rows[:limit]
    )
    match = re.search(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(?!\d)", text)
    if match:
        return int(match.group(1)) * 100 + int(match.group(2))
    return None


def discover_sources(
    source: str | Path,
    *,
    recursive: bool = True,
    exclude_dirs: Iterable[str | Path] = (),
) -> list[Path]:
    """Descubre archivos PE-14 válidos, ignorando temporales y salidas.

    Se excluyen ``~$*``, ``~*``, ``*.tmp`` y cualquier archivo cuyo nombre
    empiece por ``pe14_`` (artefacto de una ejecución anterior). El
    descubrimiento es determinista para que el manifiesto sea reproducible.
    """

    path = Path(source)
    if path.is_file():
        return [path] if path.suffix.lower() in SUPPORTED_SUFFIXES else []

    excluded = {Path(item).resolve() for item in exclude_dirs}
    iterator = path.rglob("*") if recursive else path.glob("*")
    files: list[Path] = []
    for item in iterator:
        if not item.is_file():
            continue
        if item.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        name = item.name
        if name.startswith("~$") or name.startswith("~") or name.startswith("."):
            continue
        if name.lower().endswith(".tmp") or name.lower().startswith("pe14_"):
            continue
        try:
            if item.resolve().parent in excluded or any(
                parent in excluded for parent in item.resolve().parents
            ):
                continue
        except OSError:
            continue
        files.append(item)
    return sorted(files, key=lambda item: (str(item.parent).casefold(), item.name.casefold()))


def zip_has_sheet(path: str | Path) -> bool:
    """Utilidad de diagnóstico: verifica que un XLSX sea un ZIP válido."""

    try:
        with zipfile.ZipFile(path) as archive:
            return any(name.startswith("xl/worksheets/") for name in archive.namelist())
    except zipfile.BadZipFile:
        return False
