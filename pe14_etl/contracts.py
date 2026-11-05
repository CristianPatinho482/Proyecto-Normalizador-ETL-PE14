"""Contratos formales (Design-by-Contract) del módulo ETL PE-14.

La notación sigue a Hoare: ``{P} C {Q}``. Los invariantes ``I`` se comprueban
en las fronteras del cómputo y en la verificación final del pipeline.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

MODULE_ID = "ETL-PE14-UNPIVOT-NORMALIZER"
MODULE_VERSION = "1.0.0"

#: Ancho mínimo del reporte institucional (P_anchura).
MIN_SOURCE_WIDTH = 50

#: Número mínimo de categorías poblacionales (M >= 50 en la especificación).
MIN_POPULATION_CATEGORIES = 50

#: Espacio de nombres estable para UUID deterministas (idempotencia).
UUID_NAMESPACE = uuid.UUID("8f0b4d6e-4a1f-5c2d-9e3a-7b6c5d4e3f21")
UUID_STRATEGY = "uuid5-determinista"

#: Columnas obligatorias de la clave institucional (P_claves_fijas).
REQUIRED_KEYS: tuple[str, ...] = (
    "IDENTIFICADOR_FICHA",
    "CODIGO_PROGRAMA",
    "CODIGO_CENTRO",
)

#: Columnas canónicas del hecho, en el orden que consume Power BI.
FACT_COLUMNS: tuple[str, ...] = (
    "ID_HECHO_UUID",
    "ID_PERIODO",
    "ID_FICHA_SK",
    "CODIGO_FICHA",
    "CODIGO_CATEGORIA",
    "CATEGORIA_POBLACION",
    "CANTIDAD_APRENDICES",
    "TOTAL_HORAS",
    "ESTADO_CALIDAD",
)

FICHA_DIM_COLUMNS: tuple[str, ...] = (
    "ID_FICHA_SK",
    "ID_PERIODO",
    "CODIGO_FICHA",
    "IDENTIFICADOR_UNICO_FICHA",
    "CODIGO_PROGRAMA",
    "VERSION_PROGRAMA",
    "NOMBRE_PROGRAMA_FORMACION",
    "MODALIDAD_FORMACION",
    "NIVEL_FORMACION",
    "NUMERO_CURSOS",
    "CODIGO_PROGRAMA_ESPECIAL",
    "NOMBRE_PROGRAMA_ESPECIAL",
    "ID_CENTRO_SK",
    "CODIGO_CENTRO",
    "NOMBRE_CENTRO",
    "CODIGO_REGIONAL",
    "NOMBRE_REGIONAL",
    "CODIGO_DEPARTAMENTO_CURSO",
    "NOMBRE_DEPARTAMENTO_CURSO",
    "CODIGO_MUNICIPIO_CURSO",
    "NOMBRE_MUNICIPIO_CURSO",
    "CODIGO_PAIS_CURSO",
    "NOMBRE_PAIS_CURSO",
    "CODIGO_CONVENIO",
    "NOMBRE_CONVENIO",
    "AMPLIACION_COBERTURA",
    "GRAN_TOTAL_APRENDICES",
    "TOTAL_HORAS",
    "ESTADO_CALIDAD",
)

CENTRO_DIM_COLUMNS: tuple[str, ...] = (
    "ID_CENTRO_SK",
    "CODIGO_REGIONAL",
    "NOMBRE_REGIONAL",
    "CODIGO_CENTRO",
    "NOMBRE_CENTRO",
    "CODIGO_PAIS_CURSO",
    "NOMBRE_PAIS_CURSO",
)

POBLACION_DIM_COLUMNS: tuple[str, ...] = (
    "CODIGO_CATEGORIA",
    "NOMBRE_CATEGORIA",
    "ORDEN",
)

PERIODO_DIM_COLUMNS: tuple[str, ...] = (
    "ID_PERIODO",
    "ANIO",
    "MES",
    "NOMBRE_MES",
    "FECHA_INICIO",
    "FECHA_FIN",
)

AUDIT_COLUMNS: tuple[str, ...] = (
    "FILA_ORIGEN",
    "ID_PERIODO",
    "CODIGO_FICHA",
    "GRAN_TOTAL_APRENDICES",
    "SUMA_CATEGORIAS",
    "DIFERENCIA",
    "TOTAL_HORAS_CRUDO",
    "TOTAL_HORAS_NORMALIZADO",
    "CATEGORIAS_POSITIVAS",
    "HECHOS_GENERADOS",
    "ESTADO_CALIDAD",
    "OBSERVACIONES",
)

REJECTED_COLUMNS: tuple[str, ...] = (
    "FILA_ORIGEN",
    "ID_PERIODO",
    "IDENTIFICADOR_FICHA",
    "CODIGO_PROGRAMA",
    "CODIGO_CENTRO",
    "CATEGORIA_POBLACION",
    "VALOR_CRUDO",
    "MOTIVO",
    "ESTADO_CALIDAD",
)


class ContractViolation(AssertionError):
    """Violación de una cláusula ``{P}``, ``{Q}`` o ``{I}``."""

    def __init__(self, clause: str, message: str):
        self.clause = clause
        self.message = message
        super().__init__(f"[{clause}] {message}")


def precondition(condition: bool, message: str) -> None:
    if not condition:
        raise ContractViolation("P", message)


def postcondition(condition: bool, message: str) -> None:
    if not condition:
        raise ContractViolation("Q", message)


def invariant(condition: bool, message: str) -> None:
    if not condition:
        raise ContractViolation("I", message)


@dataclass
class ProcessingMetadata:
    """Trazabilidad completa de una ejecución del pipeline."""

    module_id: str = MODULE_ID
    module_version: str = MODULE_VERSION
    source_path: str = ""
    source_name: str = ""
    source_kind: str = ""
    source_sha256: str = ""
    source_bytes: int = 0
    encoding: str | None = None
    delimiter: str | None = None
    sheet_name: str | None = None
    header_row: int = -1
    source_rows_total: int = 0
    source_rows_nonblank: int = 0
    blank_rows_skipped: int = 0
    rows_accepted: int = 0
    rows_rejected: int = 0
    rows_duplicate: int = 0
    rows_in_review: int = 0
    rows_without_facts: int = 0
    unique_fichas: int = 0
    population_categories: int = 0
    fact_rows: int = 0
    total_apprentices: int = 0
    total_hours: float = 0.0
    total_hours_fact: float = 0.0
    columns_detected: list[str] = field(default_factory=list)
    categories_detected: list[str] = field(default_factory=list)
    required_columns_present: bool = False
    period: int | None = None
    period_source: str = ""
    uuid_strategy: str = UUID_STRATEGY
    uuid_namespace: str = str(UUID_NAMESPACE)
    hours_numeric_mode: str = "hours"
    warnings: list[str] = field(default_factory=list)
    output_dir: str = ""
    artifacts: dict[str, dict[str, Any]] = field(default_factory=dict)
    data_fingerprint: str = ""
    generated_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds")
    )

    def set_artifact(self, name: str, path: str, sha256: str = "", size_bytes: int = 0) -> None:
        self.artifacts[name] = {
            "path": path,
            "sha256": sha256,
            "bytes": size_bytes,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_id": self.module_id,
            "module_version": self.module_version,
            "source_path": self.source_path,
            "source_name": self.source_name,
            "source_kind": self.source_kind,
            "source_sha256": self.source_sha256,
            "source_bytes": self.source_bytes,
            "encoding": self.encoding,
            "delimiter": self.delimiter,
            "sheet_name": self.sheet_name,
            "header_row": self.header_row,
            "source_rows_total": self.source_rows_total,
            "source_rows_nonblank": self.source_rows_nonblank,
            "blank_rows_skipped": self.blank_rows_skipped,
            "rows_accepted": self.rows_accepted,
            "rows_rejected": self.rows_rejected,
            "rows_duplicate": self.rows_duplicate,
            "rows_in_review": self.rows_in_review,
            "rows_without_facts": self.rows_without_facts,
            "unique_fichas": self.unique_fichas,
            "population_categories": self.population_categories,
            "fact_rows": self.fact_rows,
            "total_apprentices": self.total_apprentices,
            "total_hours": self.total_hours,
            "total_hours_fact": self.total_hours_fact,
            "columns_detected": list(self.columns_detected),
            "categories_detected": list(self.categories_detected),
            "required_columns_present": self.required_columns_present,
            "period": self.period,
            "period_source": self.period_source,
            "uuid_strategy": self.uuid_strategy,
            "uuid_namespace": self.uuid_namespace,
            "hours_numeric_mode": self.hours_numeric_mode,
            "warnings": list(self.warnings),
            "output_dir": self.output_dir,
            "artifacts": {
                name: dict(info) for name, info in sorted(self.artifacts.items())
            },
            "data_fingerprint": self.data_fingerprint,
            "generated_at": self.generated_at,
        }
