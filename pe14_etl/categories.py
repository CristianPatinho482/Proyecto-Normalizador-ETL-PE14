"""Catálogo canónico de categorías poblacionales del reporte PE-14.

El catálogo desacopla las cabeceras físicas (que cambian de formato entre
exportaciones) de los nombres atómicos que consume Power BI. Cualquier
categoría nueva se conserva mediante una definición derivada y se reporta como
advertencia de gobernanza; las 52 categorías del corte 2026 están catalogadas.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class CategoryDefinition:
    """Definición inmutable de una categoría de población especial."""

    key: str
    code: str
    name: str
    order: int
    cataloged: bool = True


def _slug(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    slug = re.sub(r"[^A-Za-z0-9]+", "_", ascii_text).strip("_").lower()
    return re.sub(r"_+", "_", slug)


# El orden reproduce el orden de las columnas del reporte institucional.
_RAW_CATALOG: tuple[tuple[str, str], ...] = (
    ("ACTOS_DE_GRUPOS_ARMADOS", "Actos de grupos armados"),
    (
        "ADOLESCENTE_DESVINCULADO_DE_GRUPOS_ARMADOS_ORGANIZADOS_AL_MARGEN_DE_LA_LEY",
        "Adolescente desvinculado de grupos armados organizados al margen de la ley",
    ),
    (
        "ADOLESCENTE_EN_CONFLICTO_CON_LA_LEY_PENAL",
        "Adolescente en conflicto con la ley penal",
    ),
    ("ADOLESCENTE_TRABAJADOR", "Adolescente trabajador"),
    ("AFROCOLOMBIANOS", "Afrocolombianos"),
    (
        "AFROCOLOMBIANOS_DESPLAZADOS_POR_LA_VIOLENCIA",
        "Afrocolombianos desplazados por la violencia",
    ),
    (
        "AFROCOLOMBIANOS_DESPLAZADOS_POR_LA_VIOLENCIA_CABEZA_DE_FAMILIA",
        "Afrocolombianos desplazados por la violencia cabeza de familia",
    ),
    ("AMENAZA", "Amenaza"),
    ("ARTESANOS", "Artesanos"),
    ("CAMPESINOS", "Campesinos"),
    ("DELITOS_SEXUALES", "Delitos sexuales"),
    ("DESAPARICION_FORZADA", "Desaparición forzada"),
    ("DESPLAZADOS_DISCAPACITADOS", "Desplazados discapacitados"),
    (
        "DESPLAZADOS_POR_FENOMENOS_NATURALES",
        "Desplazados por fenómenos naturales",
    ),
    (
        "DESPLAZADOS_POR_FENOMENOS_NATURALES_CABEZA_DE_FAMILIA",
        "Desplazados por fenómenos naturales cabeza de familia",
    ),
    ("DESPLAZADOS_POR_LA_VIOLENCIA", "Desplazados por la violencia"),
    (
        "DESPLAZADOS_POR_LA_VIOLENCIA_CABEZA_DE_FAMILIA",
        "Desplazados por la violencia cabeza de familia",
    ),
    ("DESPOJO", "Despojo"),
    ("DISCAPACIDAD", "Discapacidad"),
    ("DISCAPACIDAD_AUDITIVA", "Discapacidad auditiva"),
    ("DISCAPACIDAD_FISICA", "Discapacidad física"),
    ("DISCAPACIDAD_INTELECTUAL", "Discapacidad intelectual"),
    ("DISCAPACIDAD_MULTIPLE", "Discapacidad múltiple"),
    ("DISCAPACIDAD_PSICOSOCIAL", "Discapacidad psicosocial"),
    ("DISCAPACIDAD_VISUAL", "Discapacidad visual"),
    ("EMPRENDEDORES", "Emprendedores"),
    ("HERIDO", "Herido"),
    ("HOMICIDIO_/_MASACRE", "Homicidio / masacre"),
    ("INDIGENAS", "Indígenas"),
    (
        "INDIGENAS_DESPLAZADOS_POR_LA_VIOLENCIA",
        "Indígenas desplazados por la violencia",
    ),
    (
        "INDIGENAS_DESPLAZADOS_POR_LA_VIOLENCIA_CABEZA_DE_FAMILIA",
        "Indígenas desplazados por la violencia cabeza de familia",
    ),
    ("INPEC", "INPEC"),
    ("JOVENES_VULNERABLES", "Jóvenes vulnerables"),
    ("MICROEMPRESAS", "Microempresas"),
    (
        "MINAS_ANTIPERSONALES_Y_EXPLOSIVOS",
        "Minas antipersonales y explosivos",
    ),
    ("MUJER_CABEZA_DE_FAMILIA", "Mujer cabeza de familia"),
    ("NEGRO", "Negro"),
    ("NINGUNA", "Ninguna"),
    ("PALENQUEROS", "Palenqueros"),
    ("PROC_REINTEGRACION", "Proceso de reintegración"),
    ("RAIZALES", "Raizales"),
    ("RECLUTAMIENTO_FORZADO", "Reclutamiento forzado"),
    ("REMITIDOS_POR_EL_CIE", "Remitidos por el CIE"),
    ("REMITIDOS_POR_EL_PAL", "Remitidos por el PAL"),
    ("ROM", "ROM"),
    ("SECUESTRO", "Secuestro"),
    (
        "SOBREVIVIENTES_MINAS_ANTIPERSONALES",
        "Sobrevivientes de minas antipersonales",
    ),
    ("SOLDADOS_CAMPESINOS", "Soldados campesinos"),
    ("SORDOCEGUERA", "Sordoceguera"),
    ("TERCERA_EDAD", "Tercera edad"),
    ("TORTURA", "Tortura"),
    ("USO_DE_MENORES_POR_GRUPOS_ARMADOS", "Uso de menores por grupos armados"),
)


CATEGORY_CATALOG: dict[str, CategoryDefinition] = {
    key: CategoryDefinition(key=key, code=_slug(key), name=name, order=index)
    for index, (key, name) in enumerate(_RAW_CATALOG, start=1)
}


def resolve_category(key: str, order: int | None = None) -> CategoryDefinition:
    """Resuelve una categoría catalogada o construye una definición derivada.

    La función es total: una cabecera desconocida no interrumpe el ETL, pero
    queda marcada con ``cataloged=False`` para que la capa de calidad la
    reporte como advertencia de gobernanza.
    """

    normalized = re.sub(r"_+", "_", key.strip("_").upper())
    known = CATEGORY_CATALOG.get(normalized)
    if known is not None:
        return known
    fallback_order = order if order is not None else len(CATEGORY_CATALOG) + 1
    display = normalized.replace("_", " ").replace(" / ", " / ").strip().title()
    return CategoryDefinition(
        key=normalized,
        code=_slug(normalized),
        name=display,
        order=fallback_order,
        cataloged=False,
    )


def catalog_keys() -> tuple[str, ...]:
    """Devuelve las claves catalogadas en el orden institucional."""

    return tuple(CATEGORY_CATALOG)
