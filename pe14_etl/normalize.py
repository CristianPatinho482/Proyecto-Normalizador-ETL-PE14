"""Núcleo de normalización y desdinamización (unpivot) del PE-14.

El módulo implementa el operador formal::

    U(t) = {(UUID(t), periodo, ficha, categoria, cantidad, horas)
            | categoria es una columna Total_Aprendices ∧ cantidad > 0}

Las columnas ``Masc/Fem/NoBin`` no se desdinamizan para no cuadruplicar los
aprendices: se usan como control de consistencia contra la columna total.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from .categories import CategoryDefinition, resolve_category
from .contracts import (
    FACT_COLUMNS,
    MIN_POPULATION_CATEGORIES,
    REQUIRED_KEYS,
    UUID_NAMESPACE,
    invariant,
    postcondition,
    precondition,
)
from .io import ExtractedTable, canonical_key

TWO_PLACES = Decimal("0.01")
ZERO = Decimal("0.00")
MAX_REASONABLE_HOURS = Decimal("10000.00")
EXCEL_EPOCH = datetime(1899, 12, 31)


@dataclass(frozen=True)
class HoursParse:
    """Resultado del reconocedor polimórfico de ``TOTAL_HORAS``."""

    value: Decimal
    raw: str
    kind: str
    issue: str | None = None
    review: bool = False


@dataclass
class IntParse:
    value: int
    raw: str
    issue: str | None = None
    empty: bool = False


@dataclass(frozen=True)
class PopulationColumns:
    """Columnas físicas asociadas a una categoría poblacional."""

    category: CategoryDefinition
    total_index: int | None
    masc_index: int | None
    fem_index: int | None
    nobin_index: int | None
    header: str


@dataclass
class PopulationSchema:
    """Esquema dinámico detectado en una cabecera PE-14."""

    header: list[str]
    keys: list[str]
    categories: list[PopulationColumns]
    gran_total_index: int | None
    total_horas_index: int | None
    warnings: list[str] = field(default_factory=list)

    @property
    def category_by_code(self) -> dict[str, PopulationColumns]:
        return {item.category.code: item for item in self.categories}

    @property
    def required_columns_present(self) -> bool:
        present = {key for key in self.keys if key}
        return all(key in present for key in REQUIRED_KEYS)


@dataclass
class ParsedRow:
    source_row: int
    codigo_ficha: int
    dim: dict[str, Any]
    counts: dict[str, int]
    gender: dict[str, tuple[int, int, int]]
    total_hours: Decimal
    hours_raw: str
    hours_kind: str
    gran_total: int | None
    issues: list[str] = field(default_factory=list)
    status: str = "OK"
    fact_count: int = 0
    audit_status: str = "OK"
    merged_into: int | None = None
    merged_sources: list[int] = field(default_factory=list)

    @property
    def population_sum(self) -> int:
        return sum(self.counts.values())

    @property
    def positive_categories(self) -> int:
        return sum(1 for value in self.counts.values() if value > 0)

    def recompute_status(self) -> None:
        unique_issues = list(dict.fromkeys(issue for issue in self.issues if issue))
        self.issues = unique_issues
        self.status = "REVISAR" if unique_issues else "OK"


@dataclass
class NormalizationResult:
    fact: list[dict[str, Any]]
    dim_ficha: list[dict[str, Any]]
    dim_centro: list[dict[str, Any]]
    dim_poblacion: list[dict[str, Any]]
    dim_periodo: list[dict[str, Any]]
    audit: list[dict[str, Any]]
    rejected: list[dict[str, Any]]
    schema: PopulationSchema
    period: int
    source_rows_nonblank: int = 0
    rows_accepted: int = 0
    rows_rejected: int = 0
    rows_duplicate: int = 0
    rows_in_review: int = 0
    rows_without_facts: int = 0
    unique_fichas: int = 0
    total_apprentices: int = 0
    total_hours: Decimal = ZERO
    total_hours_fact: Decimal = ZERO
    warnings: list[str] = field(default_factory=list)
    data_fingerprint: str = ""

    @property
    def fact_rows(self) -> int:
        return len(self.fact)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, (time, timedelta)):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return str(value)
        if value.is_integer():
            return str(int(value))
        return format(value, ".15g")
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value).strip()


def _raw_repr(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value)


def _strip_numeric_text(text: str) -> str:
    return (
        text.replace("\xa0", "")
        .replace(" ", "")
        .replace("\u202f", "")
        .replace("'", "")
        .strip()
    )


def _parse_localized_decimal(text: str) -> Decimal | None:
    """Convierte un decimal localizado (coma o punto) a ``Decimal``."""

    value = _strip_numeric_text(text)
    if not value:
        return None
    if value.count(",") and value.count("."):
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif value.count(","):
        if re.fullmatch(r"\d{1,3}(,\d{3})+", value):
            value = value.replace(",", "")
        else:
            value = value.replace(",", ".")
    elif value.count("."):
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", value):
            value = value.replace(".", "")
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def clean_int(value: Any) -> IntParse:
    """Coerción segura a entero no negativo para cantidades de aprendices.

    Los valores vacíos son cero válido. Los valores no numéricos producen cero
    y una incidencia; los decimales se redondean a la unidad más cercana.
    """

    if value is None:
        return IntParse(0, "", empty=True)
    if isinstance(value, bool):
        return IntParse(int(value), str(value))
    if isinstance(value, int):
        return IntParse(value, str(value))
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return IntParse(0, str(value), issue="Valor no finito")
        dec = Decimal(str(value))
        return _decimal_to_int(dec, str(value))
    if isinstance(value, Decimal):
        return _decimal_to_int(value, str(value))
    text = str(value).strip()
    if not text:
        return IntParse(0, text, empty=True)
    dec = _parse_localized_decimal(text)
    if dec is None:
        return IntParse(0, text, issue="Valor no numérico")
    return _decimal_to_int(dec, text)


def _decimal_to_int(value: Decimal, raw: str) -> IntParse:
    if value != value.to_integral_value():
        rounded = int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        return IntParse(rounded, raw, issue="Valor decimal redondeado a entero")
    return IntParse(int(value), raw)


def _from_datetime(value: datetime) -> Decimal:
    if value.tzinfo is not None:
        value = value.replace(tzinfo=None)
    return Decimal(str((value - EXCEL_EPOCH).total_seconds())) / Decimal(3600)


def parse_total_hours(
    value: Any,
    *,
    numeric_mode: str = "hours",
) -> HoursParse:
    """Parser polimórfico de ``TOTAL_HORAS``.

    Reconoce, en este orden: duración ``timedelta``, hora ``time``, fecha/hora
    serial de Excel, ``HH:MM[:SS]``, decimal localizado y número. El fallback
    seguro es ``0.00`` con ``review=True``.
    """

    precondition(numeric_mode in {"hours", "days"}, f"Modo numérico inválido: {numeric_mode}")
    raw = _raw_repr(value)
    if value is None or (isinstance(value, str) and not value.strip()):
        return HoursParse(ZERO, raw, "vacio", "TOTAL_HORAS vacío", True)

    hours: Decimal | None = None
    kind = ""
    issue: str | None = None
    review = False

    if isinstance(value, timedelta):
        hours = Decimal(str(value.total_seconds())) / Decimal(3600)
        kind = "timedelta"
    elif isinstance(value, time):
        hours = (
            Decimal(value.hour)
            + Decimal(value.minute) / Decimal(60)
            + Decimal(value.second) / Decimal(3600)
            + Decimal(value.microsecond) / Decimal(3600000000)
        )
        kind = "hora"
    elif isinstance(value, datetime):
        hours = _from_datetime(value)
        kind = "fecha_serial"
    elif isinstance(value, date):
        hours = Decimal(str((value - EXCEL_EPOCH.date()).days * 24))
        kind = "fecha_serial"
    elif isinstance(value, bool):
        hours = Decimal(int(value))
        kind = "booleano"
        issue = "Booleano interpretado como horas"
        review = True
    elif isinstance(value, (int, float, Decimal)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return HoursParse(ZERO, raw, "invalido", "Valor no finito", True)
        if isinstance(value, Decimal) and not value.is_finite():
            return HoursParse(ZERO, raw, "invalido", "Valor no finito", True)
        try:
            hours = Decimal(str(value))
        except InvalidOperation:
            hours = None
        kind = "numero_dias" if numeric_mode == "days" else "numero_horas"
        if numeric_mode == "days" and hours is not None:
            hours *= Decimal(24)
    else:
        text = _strip_numeric_text(str(value))
        if not text:
            return HoursParse(ZERO, raw, "vacio", "TOTAL_HORAS vacío", True)

        duration = re.fullmatch(
            r"([+-]?\d{1,7}):(\d{1,2})(?::(\d{1,2}(?:[.,]\d+)?))?", text
        )
        if duration:
            hours_value = Decimal(duration.group(1))
            minutes = Decimal(duration.group(2))
            seconds = Decimal((duration.group(3) or "0").replace(",", "."))
            if minutes >= 60 or seconds >= 60:
                issue = "Componentes de tiempo fuera de rango (MM/SS >= 60)"
                review = True
            hours = hours_value + minutes / 60 + seconds / 3600
            kind = "texto_hhmm"
        else:
            datetime_value: datetime | None = None
            try:
                datetime_value = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                datetime_value = None
            for fmt in (
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y-%m-%d",
                "%d/%m/%Y %H:%M:%S",
                "%d/%m/%Y",
            ):
                try:
                    datetime_value = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            if datetime_value is not None:
                hours = _from_datetime(datetime_value)
                kind = "fecha_serial"
            else:
                dec = _parse_localized_decimal(text)
                if dec is not None:
                    hours = dec * 24 if numeric_mode == "days" else dec
                    kind = "decimal_dias" if numeric_mode == "days" else "decimal_horas"
                else:
                    return HoursParse(
                        ZERO,
                        raw,
                        "invalido",
                        "No fue posible interpretar TOTAL_HORAS",
                        True,
                    )

    if hours is None:
        return HoursParse(ZERO, raw, "invalido", "No fue posible interpretar TOTAL_HORAS", True)
    if not hours.is_finite():
        return HoursParse(ZERO, raw, "invalido", "Valor no finito", True)
    if hours < 0:
        return HoursParse(ZERO, raw, kind, "TOTAL_HORAS negativo; se normaliza a 0", True)

    quantized = hours.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    if quantized > MAX_REASONABLE_HOURS:
        issue = "TOTAL_HORAS fuera del rango razonable (> 10.000 h)"
        review = True
    return HoursParse(quantized, raw, kind, issue, review)


def build_population_schema(header: list[str]) -> PopulationSchema:
    """Descubre las M categorías poblacionales y sus columnas de control."""

    keys = [canonical_key(value) for value in header]
    warnings: list[str] = []
    key_to_index: dict[str, int] = {}
    for index, key in enumerate(keys):
        if not key:
            continue
        if key in key_to_index:
            warnings.append(f"Cabecera duplicada ignorada: {key}")
            continue
        key_to_index[key] = index

    suffixes = {
        "total": "_TOTAL_APRENDICES",
        "masc": "_APRENDICES_MASC",
        "fem": "_APRENDICES_FEM",
        "nobin": "_APRENDICES_NOBIN",
    }
    bases: dict[str, dict[str, int]] = {}
    for index, key in enumerate(keys):
        if not key:
            continue
        for metric, suffix in suffixes.items():
            if not key.endswith(suffix):
                continue
            base = key[: -len(suffix)].strip("_")
            if not base or (metric == "total" and base == "GRAN"):
                continue
            bases.setdefault(base, {})[metric] = index

    categories: list[PopulationColumns] = []
    seen_codes: set[str] = set()
    for base, metrics in bases.items():
        if not any(metric in metrics for metric in suffixes):
            continue
        category = resolve_category(base, order=len(categories) + 1)
        if category.code in seen_codes:
            warnings.append(f"Categoría duplicada ignorada: {category.name}")
            continue
        seen_codes.add(category.code)
        if "total" not in metrics:
            warnings.append(
                f"Categoría {category.name}: sin columna Total_Aprendices; "
                "se usará la suma de Masc/Fem/NoBin."
            )
        if not category.cataloged:
            warnings.append(
                f"Categoría no catalogada, se conserva con nombre derivado: {category.key}"
            )
        total_index = metrics.get("total")
        categories.append(
            PopulationColumns(
                category=category,
                total_index=total_index,
                masc_index=metrics.get("masc"),
                fem_index=metrics.get("fem"),
                nobin_index=metrics.get("nobin"),
                header=header[total_index] if total_index is not None else base,
            )
        )

    categories.sort(key=lambda item: item.category.order)
    precondition(
        len(categories) >= MIN_POPULATION_CATEGORIES,
        f"M >= {MIN_POPULATION_CATEGORIES} violado: se detectaron "
        f"{len(categories)} categorías poblacionales.",
    )
    missing_required = [key for key in REQUIRED_KEYS if key not in key_to_index]
    precondition(
        not missing_required,
        "P_claves_fijas violada: faltan columnas "
        + ", ".join(missing_required),
    )

    if "TOTAL_HORAS" not in key_to_index:
        warnings.append("No se detectó TOTAL_HORAS; se normalizará a 0.00 y se marcará REVISAR.")
    if "GRAN_TOTAL_APRENDICES" not in key_to_index:
        warnings.append("No se detectó GRAN_TOTAL_APRENDICES; no se podrá contrastar la suma.")

    return PopulationSchema(
        header=header,
        keys=keys,
        categories=categories,
        gran_total_index=key_to_index.get("GRAN_TOTAL_APRENDICES"),
        total_horas_index=key_to_index.get("TOTAL_HORAS"),
        warnings=warnings,
    )


def _cell(row: list[Any], index: int | None) -> Any:
    if index is None or index < 0 or index >= len(row):
        return None
    return row[index]


def _make_center_key(codigo_regional: str, codigo_centro: str) -> str:
    return f"{codigo_regional or 'SIN_REGIONAL'}|{codigo_centro or 'SIN_CENTRO'}"


def _make_ficha_key(period: int, codigo_ficha: int) -> str:
    return f"FICHA|{period}|{codigo_ficha}"


def _id_ficha_sk(period: int, codigo_ficha: int) -> str:
    return str(uuid.uuid5(UUID_NAMESPACE, _make_ficha_key(period, codigo_ficha)))


def _id_centro_sk(center_key: str) -> str:
    return str(uuid.uuid5(UUID_NAMESPACE, f"CENTRO|{center_key}"))


def _id_hecho(period: int, codigo_ficha: int, category_code: str) -> str:
    return str(
        uuid.uuid5(
            UUID_NAMESPACE,
            f"PE14|{period}|{codigo_ficha}|{category_code}",
        )
    )


def _category_values(
    row: list[Any],
    schema: PopulationSchema,
    issues: list[str],
    rejected: list[dict[str, Any]],
    *,
    source_row: int,
    period: int,
    codigo_ficha: int,
    codigo_programa: str,
    codigo_centro: str,
) -> tuple[dict[str, int], dict[str, tuple[int, int, int]]]:
    counts: dict[str, int] = {}
    genders: dict[str, tuple[int, int, int]] = {}

    for population in schema.categories:
        category = population.category
        total_parse = clean_int(_cell(row, population.total_index))
        masc_parse = clean_int(_cell(row, population.masc_index))
        fem_parse = clean_int(_cell(row, population.fem_index))
        nobin_parse = clean_int(_cell(row, population.nobin_index))
        raw_total = _raw_repr(_cell(row, population.total_index))

        if total_parse.issue:
            issues.append(f"{category.name}: {total_parse.issue}")
            rejected.append(
                {
                    "FILA_ORIGEN": source_row,
                    "ID_PERIODO": period,
                    "IDENTIFICADOR_FICHA": codigo_ficha,
                    "CODIGO_PROGRAMA": codigo_programa,
                    "CODIGO_CENTRO": codigo_centro,
                    "CATEGORIA_POBLACION": category.name,
                    "VALOR_CRUDO": raw_total,
                    "MOTIVO": total_parse.issue,
                    "ESTADO_CALIDAD": "RECHAZADO",
                }
            )
        if masc_parse.issue:
            issues.append(f"{category.name} (Masc): {masc_parse.issue}")
        if fem_parse.issue:
            issues.append(f"{category.name} (Fem): {fem_parse.issue}")
        if nobin_parse.issue:
            issues.append(f"{category.name} (NoBin): {nobin_parse.issue}")

        if population.total_index is None:
            total = masc_parse.value + fem_parse.value + nobin_parse.value
            issues.append(f"{category.name}: total sintetizado desde columnas de género")
        else:
            total = total_parse.value

        gender_sum = masc_parse.value + fem_parse.value + nobin_parse.value
        if (
            population.total_index is not None
            and any(
                index is not None
                for index in (
                    population.masc_index,
                    population.fem_index,
                    population.nobin_index,
                )
            )
            and total != gender_sum
        ):
            issues.append(
                f"{category.name}: Total={total} no coincide con Masc+Fem+NoBin={gender_sum}"
            )

        if total < 0 or gender_sum < 0:
            issues.append(f"{category.name}: cantidad negativa; no se genera hecho")
            rejected.append(
                {
                    "FILA_ORIGEN": source_row,
                    "ID_PERIODO": period,
                    "IDENTIFICADOR_FICHA": codigo_ficha,
                    "CODIGO_PROGRAMA": codigo_programa,
                    "CODIGO_CENTRO": codigo_centro,
                    "CATEGORIA_POBLACION": category.name,
                    "VALOR_CRUDO": raw_total,
                    "MOTIVO": "Cantidad negativa",
                    "ESTADO_CALIDAD": "RECHAZADO",
                }
            )
            total = 0
        elif total == 0 and gender_sum > 0:
            issues.append(
                f"{category.name}: Total=0 con Masc+Fem+NoBin={gender_sum}; no se genera hecho"
            )
            rejected.append(
                {
                    "FILA_ORIGEN": source_row,
                    "ID_PERIODO": period,
                    "IDENTIFICADOR_FICHA": codigo_ficha,
                    "CODIGO_PROGRAMA": codigo_programa,
                    "CODIGO_CENTRO": codigo_centro,
                    "CATEGORIA_POBLACION": category.name,
                    "VALOR_CRUDO": raw_total,
                    "MOTIVO": "Total cero con géneros positivos",
                    "ESTADO_CALIDAD": "RECHAZADO",
                }
            )

        counts[category.key] = total
        genders[category.key] = (masc_parse.value, fem_parse.value, nobin_parse.value)

    return counts, genders


def _parse_row(
    row: list[Any],
    schema: PopulationSchema,
    *,
    source_row: int,
    period: int,
    numeric_mode: str,
    rejected: list[dict[str, Any]],
) -> ParsedRow | None:
    key_to_index = {key: index for index, key in enumerate(schema.keys) if key}
    issues: list[str] = []

    ficha_parse = clean_int(_cell(row, key_to_index.get("IDENTIFICADOR_FICHA")))
    if ficha_parse.empty or ficha_parse.value <= 0 or ficha_parse.value > 999_999_999:
        rejected.append(
            {
                "FILA_ORIGEN": source_row,
                "ID_PERIODO": period,
                "IDENTIFICADOR_FICHA": ficha_parse.raw,
                "CODIGO_PROGRAMA": _text(_cell(row, key_to_index.get("CODIGO_PROGRAMA"))),
                "CODIGO_CENTRO": _text(_cell(row, key_to_index.get("CODIGO_CENTRO"))),
                "CATEGORIA_POBLACION": "",
                "VALOR_CRUDO": ficha_parse.raw,
                "MOTIVO": "IDENTIFICADOR_FICHA ausente o inválido",
                "ESTADO_CALIDAD": "RECHAZADO",
            }
        )
        return None

    codigo_ficha = ficha_parse.value
    codigo_programa = _text(_cell(row, key_to_index.get("CODIGO_PROGRAMA")))
    codigo_centro = _text(_cell(row, key_to_index.get("CODIGO_CENTRO")))
    codigo_regional = _text(_cell(row, key_to_index.get("CODIGO_REGIONAL")))
    if not codigo_programa:
        issues.append("CODIGO_PROGRAMA vacío")
    if not codigo_centro:
        issues.append("CODIGO_CENTRO vacío")
        codigo_centro = "SIN_CENTRO"
    if not codigo_regional:
        issues.append("CODIGO_REGIONAL vacío")
        codigo_regional = "SIN_REGIONAL"

    hours_index = schema.total_horas_index
    if hours_index is None:
        hours = HoursParse(ZERO, "", "vacio", "Columna TOTAL_HORAS ausente", True)
    else:
        hours = parse_total_hours(_cell(row, hours_index), numeric_mode=numeric_mode)
    if hours.review:
        issues.append(hours.issue or "TOTAL_HORAS marcado para revisión")

    gran_total: int | None = None
    if schema.gran_total_index is not None:
        gran_parse = clean_int(_cell(row, schema.gran_total_index))
        if gran_parse.issue:
            issues.append(f"GRAN_TOTAL_APRENDICES: {gran_parse.issue}")
        elif not gran_parse.empty:
            gran_total = gran_parse.value
            if gran_total < 0:
                issues.append("GRAN_TOTAL_APRENDICES negativo")
                gran_total = None

    counts, genders = _category_values(
        row,
        schema,
        issues,
        rejected,
        source_row=source_row,
        period=period,
        codigo_ficha=codigo_ficha,
        codigo_programa=codigo_programa,
        codigo_centro=codigo_centro,
    )
    population_sum = sum(counts.values())
    if gran_total is not None and population_sum != gran_total:
        issues.append(
            f"Suma de categorías={population_sum} no coincide con "
            f"GRAN_TOTAL_APRENDICES={gran_total}"
        )

    center_key = _make_center_key(codigo_regional, codigo_centro)
    ficha_sk = _id_ficha_sk(period, codigo_ficha)
    center_sk = _id_centro_sk(center_key)
    dim = {
        "ID_FICHA_SK": ficha_sk,
        "ID_PERIODO": period,
        "CODIGO_FICHA": codigo_ficha,
        "IDENTIFICADOR_UNICO_FICHA": _text(
            _cell(row, key_to_index.get("IDENTIFICADOR_UNICO_FICHA"))
        ),
        "CODIGO_PROGRAMA": codigo_programa,
        "VERSION_PROGRAMA": _text(_cell(row, key_to_index.get("VERSION_PROGRAMA"))),
        "NOMBRE_PROGRAMA_FORMACION": _text(
            _cell(row, key_to_index.get("NOMBRE_PROGRAMA_FORMACION"))
        ),
        "MODALIDAD_FORMACION": _text(
            _cell(row, key_to_index.get("MODALIDAD_FORMACION"))
        ),
        "NIVEL_FORMACION": _text(_cell(row, key_to_index.get("NIVEL_FORMACION"))),
        "NUMERO_CURSOS": _text(_cell(row, key_to_index.get("NUMERO_CURSOS"))),
        "CODIGO_PROGRAMA_ESPECIAL": _text(
            _cell(row, key_to_index.get("CODIGO_PROGRAMA_ESPECIAL"))
        ),
        "NOMBRE_PROGRAMA_ESPECIAL": _text(
            _cell(row, key_to_index.get("NOMBRE_PROGRAMA_ESPECIAL"))
        ),
        "ID_CENTRO_SK": center_sk,
        "CODIGO_CENTRO": codigo_centro,
        "NOMBRE_CENTRO": _text(_cell(row, key_to_index.get("NOMBRE_CENTRO"))),
        "CODIGO_REGIONAL": codigo_regional,
        "NOMBRE_REGIONAL": _text(_cell(row, key_to_index.get("NOMBRE_REGIONAL"))),
        "CODIGO_DEPARTAMENTO_CURSO": _text(
            _cell(row, key_to_index.get("CODIGO_DEPARTAMENTO_CURSO"))
        ),
        "NOMBRE_DEPARTAMENTO_CURSO": _text(
            _cell(row, key_to_index.get("NOMBRE_DEPARTAMENTO_CURSO"))
        ),
        "CODIGO_MUNICIPIO_CURSO": _text(
            _cell(row, key_to_index.get("CODIGO_MUNICIPIO_CURSO"))
        ),
        "NOMBRE_MUNICIPIO_CURSO": _text(
            _cell(row, key_to_index.get("NOMBRE_MUNICIPIO_CURSO"))
        ),
        "CODIGO_PAIS_CURSO": _text(_cell(row, key_to_index.get("CODIGO_PAIS_CURSO"))),
        "NOMBRE_PAIS_CURSO": _text(
            _cell(row, key_to_index.get("NOMBRE_PAIS_CURSO"))
        ),
        "CODIGO_CONVENIO": _text(_cell(row, key_to_index.get("CODIGO_CONVENIO"))),
        "NOMBRE_CONVENIO": _text(_cell(row, key_to_index.get("NOMBRE_CONVENIO"))),
        "AMPLIACION_COBERTURA": _text(
            _cell(row, key_to_index.get("AMPLIACION_COBERTURA"))
        ),
        "GRAN_TOTAL_APRENDICES": gran_total,
        "TOTAL_HORAS": hours.value,
        "ESTADO_CALIDAD": "OK",
    }

    parsed = ParsedRow(
        source_row=source_row,
        codigo_ficha=codigo_ficha,
        dim=dim,
        counts=counts,
        gender=genders,
        total_hours=hours.value,
        hours_raw=hours.raw,
        hours_kind=hours.kind,
        gran_total=gran_total,
        issues=issues,
    )
    parsed.recompute_status()
    return parsed


def _same_row(left: ParsedRow, right: ParsedRow) -> bool:
    return (
        left.dim == right.dim
        and left.counts == right.counts
        and left.total_hours == right.total_hours
        and left.gran_total == right.gran_total
    )


def _merge_duplicates(
    rows: list[ParsedRow],
    warnings: list[str],
) -> tuple[list[ParsedRow], int]:
    grouped: dict[int, list[ParsedRow]] = {}
    order: list[int] = []
    for row in rows:
        if row.codigo_ficha not in grouped:
            grouped[row.codigo_ficha] = []
            order.append(row.codigo_ficha)
        grouped[row.codigo_ficha].append(row)

    kept: list[ParsedRow] = []
    duplicate_count = 0
    for ficha in order:
        group = grouped[ficha]
        keeper = group[0]
        for duplicate in group[1:]:
            duplicate_count += 1
            duplicate.audit_status = "DUPLICADO"
            duplicate.merged_into = keeper.source_row
            duplicate.fact_count = 0
            if _same_row(keeper, duplicate):
                duplicate.issues.append(
                    f"Fila duplicada exacta de la fila {keeper.source_row}"
                )
                warnings.append(
                    f"Ficha {ficha}: fila {duplicate.source_row} duplicada exacta "
                    f"de la fila {keeper.source_row}; se conserva una sola."
                )
                continue

            duplicate.audit_status = "REVISAR"
            duplicate.issues.append(
                f"Fila divergente fusionada en la fila {keeper.source_row}"
            )
            for key, value in duplicate.counts.items():
                keeper.counts[key] = keeper.counts.get(key, 0) + value
            for key, value in duplicate.gender.items():
                previous = keeper.gender.get(key, (0, 0, 0))
                keeper.gender[key] = tuple(
                    previous[index] + value[index] for index in range(3)
                )  # type: ignore[assignment]
            if keeper.gran_total is not None and duplicate.gran_total is not None:
                keeper.gran_total += duplicate.gran_total
            else:
                keeper.gran_total = None
            if keeper.total_hours != duplicate.total_hours:
                keeper.issues.append(
                    f"Ficha duplicada con TOTAL_HORAS divergente "
                    f"({keeper.total_hours} vs {duplicate.total_hours}); se usa el máximo."
                )
                keeper.total_hours = max(keeper.total_hours, duplicate.total_hours)
            keeper.issues.append(
                f"Ficha duplicada con datos divergentes; filas "
                f"{keeper.source_row} y {duplicate.source_row} fusionadas"
            )
            warnings.append(
                f"Ficha {ficha}: filas {keeper.source_row} y {duplicate.source_row} "
                "divergentes; conteos fusionados y fila marcada REVISAR."
            )
        keeper.recompute_status()
        kept.append(keeper)
    return kept, duplicate_count


def _audit_record(
    row: ParsedRow,
    *,
    period: int,
    fact_count: int,
    status: str | None = None,
    extra_issues: list[str] | None = None,
) -> dict[str, Any]:
    issues = list(row.issues)
    if extra_issues:
        issues.extend(extra_issues)
    difference = (
        row.population_sum - row.gran_total if row.gran_total is not None else None
    )
    return {
        "FILA_ORIGEN": row.source_row,
        "ID_PERIODO": period,
        "CODIGO_FICHA": row.codigo_ficha,
        "GRAN_TOTAL_APRENDICES": row.gran_total if row.gran_total is not None else "",
        "SUMA_CATEGORIAS": row.population_sum,
        "DIFERENCIA": difference if difference is not None else "",
        "TOTAL_HORAS_CRUDO": row.hours_raw,
        "TOTAL_HORAS_NORMALIZADO": format(row.total_hours, ".2f"),
        "CATEGORIAS_POSITIVAS": row.positive_categories,
        "HECHOS_GENERADOS": fact_count,
        "ESTADO_CALIDAD": status or row.audit_status,
        "OBSERVACIONES": " | ".join(dict.fromkeys(issue for issue in issues if issue)),
    }


def _rejected_audit_record(
    *,
    source_row: int,
    period: int,
    ficha_raw: str,
    issue: str,
) -> dict[str, Any]:
    return {
        "FILA_ORIGEN": source_row,
        "ID_PERIODO": period,
        "CODIGO_FICHA": ficha_raw,
        "GRAN_TOTAL_APRENDICES": "",
        "SUMA_CATEGORIAS": "",
        "DIFERENCIA": "",
        "TOTAL_HORAS_CRUDO": "",
        "TOTAL_HORAS_NORMALIZADO": "",
        "CATEGORIAS_POSITIVAS": 0,
        "HECHOS_GENERADOS": 0,
        "ESTADO_CALIDAD": "RECHAZADO",
        "OBSERVACIONES": issue,
    }


def _period_dim(period: int) -> dict[str, Any]:
    year, month = divmod(period, 100)
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    start = date(year, month, 1)
    end = date.fromordinal(next_month.toordinal() - 1)
    month_names = (
        "",
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "octubre",
        "noviembre",
        "diciembre",
    )
    return {
        "ID_PERIODO": period,
        "ANIO": year,
        "MES": month,
        "NOMBRE_MES": month_names[month].capitalize(),
        "FECHA_INICIO": start.isoformat(),
        "FECHA_FIN": end.isoformat(),
    }


def _fingerprint(
    fact: list[dict[str, Any]],
    dim_ficha: list[dict[str, Any]],
    dim_centro: list[dict[str, Any]],
    dim_poblacion: list[dict[str, Any]],
    dim_periodo: list[dict[str, Any]],
) -> str:
    payload = {
        "fact": fact,
        "dim_ficha": dim_ficha,
        "dim_centro": dim_centro,
        "dim_poblacion": dim_poblacion,
        "dim_periodo": dim_periodo,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def verify_postconditions(
    fact: list[dict[str, Any]],
    dim_ficha: list[dict[str, Any]],
    dim_centro: list[dict[str, Any]],
    dim_poblacion: list[dict[str, Any]],
    period: int,
) -> None:
    """Verifica ``Q_1fn``, ``Q_unicidad``, ``Q_no_pivote`` y ``Q_integridad``."""

    postcondition(bool(dim_poblacion), "Q_integridad: no se generó Dim_Poblacion_Especial.")

    ficha_keys = {row["ID_FICHA_SK"] for row in dim_ficha}
    centro_keys = {row["ID_CENTRO_SK"] for row in dim_centro}
    poblacion_keys = {row["CODIGO_CATEGORIA"] for row in dim_poblacion}

    postcondition(
        len(ficha_keys) == len(dim_ficha),
        "Q_unicidad: ID_FICHA_SK repetido en Dim_Ficha_Programa.",
    )
    postcondition(
        len(centro_keys) == len(dim_centro),
        "Q_unicidad: ID_CENTRO_SK repetido en Dim_Centro_Sede.",
    )
    postcondition(
        len(poblacion_keys) == len(dim_poblacion),
        "Q_unicidad: CODIGO_CATEGORIA repetido en Dim_Poblacion_Especial.",
    )

    seen: set[tuple[int, int, str]] = set()
    seen_uuid: set[str] = set()
    for row in fact:
        postcondition(
            set(row) <= set(FACT_COLUMNS),
            f"Q_no_pivote: columnas no canónicas en el hecho: {set(row) - set(FACT_COLUMNS)}",
        )
        postcondition(
            isinstance(row["CANTIDAD_APRENDICES"], int) and row["CANTIDAD_APRENDICES"] >= 0,
            "Q_integridad: CANTIDAD_APRENDICES debe ser entero >= 0.",
        )
        hours = Decimal(str(row["TOTAL_HORAS"]))
        postcondition(hours >= ZERO, "Q_integridad: TOTAL_HORAS debe ser >= 0.00.")
        postcondition(
            isinstance(row["CATEGORIA_POBLACION"], str) and bool(row["CATEGORIA_POBLACION"]),
            "Q_1fn: CATEGORIA_POBLACION debe ser atómica y no vacía.",
        )
        postcondition(
            row["ESTADO_CALIDAD"] in {"OK", "REVISAR"},
            "Q_integridad: ESTADO_CALIDAD fuera del enum {OK, REVISAR}.",
        )
        key = (
            int(row["ID_PERIODO"]),
            int(row["CODIGO_FICHA"]),
            str(row["CODIGO_CATEGORIA"]),
        )
        postcondition(
            key not in seen,
            f"Q_unicidad: hecho duplicado para {key}.",
        )
        seen.add(key)
        unique_id = str(row["ID_HECHO_UUID"])
        postcondition(unique_id not in seen_uuid, "Q_unicidad: ID_HECHO_UUID repetido.")
        seen_uuid.add(unique_id)
        postcondition(
            str(row["ID_FICHA_SK"]) in ficha_keys,
            f"Q_integridad: hecho sin Dim_Ficha_Programa para ficha {row['CODIGO_FICHA']}.",
        )
        postcondition(
            str(row["CODIGO_CATEGORIA"]) in poblacion_keys,
            f"Q_integridad: categoría sin dimensión: {row['CODIGO_CATEGORIA']}.",
        )
        postcondition(
            int(row["ID_PERIODO"]) == period,
            "Q_integridad: ID_PERIODO inconsistente en el hecho.",
        )

    for row in dim_ficha:
        postcondition(
            str(row["ID_CENTRO_SK"]) in centro_keys,
            f"Q_integridad: ficha {row['CODIGO_FICHA']} sin Dim_Centro_Sede.",
        )
        postcondition(
            int(row["ID_PERIODO"]) == period,
            "Q_integridad: ID_PERIODO inconsistente en Dim_Ficha_Programa.",
        )


def normalize_table(
    table: ExtractedTable,
    schema: PopulationSchema,
    period: int,
    *,
    numeric_mode: str = "hours",
) -> NormalizationResult:
    """Ejecuta el unpivot y construye hechos, dimensiones y auditoría."""

    precondition(period > 0, f"Periodo inválido: {period!r}")
    rejected: list[dict[str, Any]] = []
    parsed_rows: list[ParsedRow] = []
    rejected_audit: list[dict[str, Any]] = []

    for source_row, row in table.rows:
        parsed = _parse_row(
            row,
            schema,
            source_row=source_row,
            period=period,
            numeric_mode=numeric_mode,
            rejected=rejected,
        )
        if parsed is None:
            ficha_raw = _raw_repr(
                _cell(row, {key: index for index, key in enumerate(schema.keys) if key}.get("IDENTIFICADOR_FICHA"))
            )
            rejected_audit.append(
                _rejected_audit_record(
                    source_row=source_row,
                    period=period,
                    ficha_raw=ficha_raw,
                    issue="Fila rechazada: IDENTIFICADOR_FICHA ausente o inválido",
                )
            )
        else:
            parsed_rows.append(parsed)

    warnings = list(schema.warnings)
    unique_rows, duplicate_count = _merge_duplicates(parsed_rows, warnings)

    fact: list[dict[str, Any]] = []
    dim_ficha: list[dict[str, Any]] = []
    dim_centro: dict[str, dict[str, Any]] = {}
    category_lookup = {item.category.key: item for item in schema.categories}
    category_order = {item.category.key: item.category.order for item in schema.categories}

    for row in unique_rows:
        fact_count = 0
        for key, count in row.counts.items():
            if count <= 0:
                continue
            population = category_lookup[key]
            fact.append(
                {
                    "ID_HECHO_UUID": _id_hecho(period, row.codigo_ficha, population.category.code),
                    "ID_PERIODO": period,
                    "ID_FICHA_SK": row.dim["ID_FICHA_SK"],
                    "CODIGO_FICHA": row.codigo_ficha,
                    "CODIGO_CATEGORIA": population.category.code,
                    "CATEGORIA_POBLACION": population.category.name,
                    "CANTIDAD_APRENDICES": int(count),
                    "TOTAL_HORAS": row.total_hours,
                    "ESTADO_CALIDAD": row.status,
                }
            )
            fact_count += 1
        row.fact_count = fact_count
        dim = dict(row.dim)
        dim["ESTADO_CALIDAD"] = row.status
        dim_ficha.append(dim)
        center_key = _make_center_key(
            str(dim["CODIGO_REGIONAL"]), str(dim["CODIGO_CENTRO"])
        )
        dim_centro.setdefault(
            center_key,
            {
                "ID_CENTRO_SK": dim["ID_CENTRO_SK"],
                "CODIGO_REGIONAL": dim["CODIGO_REGIONAL"],
                "NOMBRE_REGIONAL": dim["NOMBRE_REGIONAL"],
                "CODIGO_CENTRO": dim["CODIGO_CENTRO"],
                "NOMBRE_CENTRO": dim["NOMBRE_CENTRO"],
                "CODIGO_PAIS_CURSO": dim["CODIGO_PAIS_CURSO"],
                "NOMBRE_PAIS_CURSO": dim["NOMBRE_PAIS_CURSO"],
            },
        )

    fact.sort(
        key=lambda item: (
            int(item["ID_PERIODO"]),
            int(item["CODIGO_FICHA"]),
            category_order.get(str(item["CODIGO_CATEGORIA"]), 10_000),
        )
    )
    dim_ficha.sort(key=lambda item: (int(item["ID_PERIODO"]), int(item["CODIGO_FICHA"])))
    dim_centro_rows = sorted(
        dim_centro.values(),
        key=lambda item: (
            str(item["CODIGO_REGIONAL"]),
            str(item["CODIGO_CENTRO"]),
        ),
    )
    dim_poblacion = [
        {
            "CODIGO_CATEGORIA": population.category.code,
            "NOMBRE_CATEGORIA": population.category.name,
            "ORDEN": population.category.order,
        }
        for population in schema.categories
    ]
    dim_poblacion.sort(key=lambda item: int(item["ORDEN"]))
    dim_periodo = [_period_dim(period)]

    # La auditoría de filas válidas conserva una entrada por fila original.
    fact_by_source: dict[int, int] = {
        row.source_row: row.fact_count for row in unique_rows
    }
    audit: list[dict[str, Any]] = []
    for row in parsed_rows:
        if row.audit_status == "DUPLICADO":
            audit.append(
                _audit_record(
                    row,
                    period=period,
                    fact_count=0,
                    status="DUPLICADO",
                    extra_issues=[
                        f"Fila fusionada en {row.merged_into}"
                        if row.merged_into is not None
                        else "Fila duplicada"
                    ],
                )
            )
        elif row.merged_into is not None:
            audit.append(
                _audit_record(
                    row,
                    period=period,
                    fact_count=0,
                    status="REVISAR",
                    extra_issues=[f"Fila fusionada en {row.merged_into}"],
                )
            )
        else:
            audit.append(
                _audit_record(
                    row,
                    period=period,
                    fact_count=fact_by_source.get(row.source_row, 0),
                )
            )
    audit.extend(rejected_audit)
    audit.sort(key=lambda item: int(item["FILA_ORIGEN"]))

    total_apprentices = sum(int(item["CANTIDAD_APRENDICES"]) for item in fact)
    total_hours = sum((row.total_hours for row in unique_rows), ZERO)
    total_hours_fact = sum((Decimal(str(item["TOTAL_HORAS"])) for item in fact), ZERO)
    rows_in_review = sum(1 for row in unique_rows if row.status == "REVISAR")
    rows_without_facts = sum(1 for row in unique_rows if row.fact_count == 0)
    unique_fichas = len({row.codigo_ficha for row in unique_rows})

    result = NormalizationResult(
        fact=fact,
        dim_ficha=dim_ficha,
        dim_centro=dim_centro_rows,
        dim_poblacion=dim_poblacion,
        dim_periodo=dim_periodo,
        audit=audit,
        rejected=rejected,
        schema=schema,
        period=period,
        source_rows_nonblank=table.source_rows_nonblank,
        rows_accepted=len(unique_rows),
        rows_rejected=len(rejected_audit),
        rows_duplicate=duplicate_count,
        rows_in_review=rows_in_review,
        rows_without_facts=rows_without_facts,
        unique_fichas=unique_fichas,
        total_apprentices=total_apprentices,
        total_hours=total_hours.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
        total_hours_fact=total_hours_fact.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
        warnings=warnings,
    )
    result.data_fingerprint = _fingerprint(
        fact, dim_ficha, dim_centro_rows, dim_poblacion, dim_periodo
    )

    invariant(
        all(row.status in {"OK", "REVISAR"} for row in unique_rows),
        "I_aislamiento: estado de fila fuera del enum permitido.",
    )
    invariant(
        all(
            int(item["CANTIDAD_APRENDICES"]) >= 0 for item in fact
        ),
        "I_no_negatividad: cantidad negativa en el hecho.",
    )
    verify_postconditions(fact, dim_ficha, dim_centro_rows, dim_poblacion, period)
    return result
