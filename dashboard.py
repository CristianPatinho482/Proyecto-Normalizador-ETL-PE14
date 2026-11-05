"""Tablero Streamlit para inspeccionar calidad de salidas PE-14."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import streamlit as st


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "control_pe14"


def read_manifest(folder: Path) -> tuple[dict, Path]:
    manifest_path = folder / "pe14_curated_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"No se encontró {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8")), manifest_path


def artifact_path(entry: dict, key: str, manifest_path: Path) -> Path:
    raw = entry.get("artifacts", {}).get(key)
    if not raw:
        raise FileNotFoundError(f"El manifiesto no registra el artefacto '{key}'.")
    path = Path(raw)
    if path.exists():
        return path
    portable = manifest_path.parent / path.name
    if portable.exists():
        return portable
    raise FileNotFoundError(f"No se encontró el artefacto {path.name} junto al manifiesto.")


def read_csv(entry: dict, key: str, manifest_path: Path) -> pd.DataFrame:
    return pd.read_csv(artifact_path(entry, key, manifest_path), encoding="utf-8-sig", keep_default_na=False)


def check_file_hashes(entry: dict, manifest_path: Path) -> tuple[int, int]:
    metadata_path = artifact_path(entry, "metadata", manifest_path)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    registered_paths = {
        Path(value).name: Path(value)
        for value in entry.get("artifacts", {}).values()
    }
    passed = failed = 0
    for filename, expected in metadata.get("artifacts", {}).items():
        path = Path(
            expected.get("path")
            or registered_paths.get(filename, manifest_path.parent / filename)
        )
        if not path.exists():
            path = manifest_path.parent / filename
        if not path.exists():
            failed += 1
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual == expected.get("sha256"):
            passed += 1
        else:
            failed += 1
    return passed, failed


def validate_period(entry: dict, manifest_path: Path, fact: pd.DataFrame, audit: pd.DataFrame) -> list[tuple[str, bool, str]]:
    ficha = read_csv(entry, "dim_ficha_programa", manifest_path)
    centro = read_csv(entry, "dim_centro_sede", manifest_path)
    poblacion = read_csv(entry, "dim_poblacion_especial", manifest_path)
    ficha_ids = set(ficha.get("ID_FICHA_SK", pd.Series(dtype=str)).astype(str))
    centro_ids = set(centro.get("ID_CENTRO_SK", pd.Series(dtype=str)).astype(str))
    categoria_ids = set(poblacion.get("CODIGO_CATEGORIA", pd.Series(dtype=str)).astype(str))
    checks = [
        ("Claves del hecho únicas", not fact.duplicated(["ID_PERIODO", "CODIGO_FICHA", "CODIGO_CATEGORIA"]).any(), "Periodo + ficha + categoría"),
        ("UUID del hecho único", not fact["ID_HECHO_UUID"].duplicated().any(), "ID_HECHO_UUID sin duplicados"),
        ("Hechos enlazados a ficha", fact["ID_FICHA_SK"].astype(str).isin(ficha_ids).all(), "Cada ID_FICHA_SK existe en Dim_Ficha_Programa"),
        ("Hechos enlazados a categoría", fact["CODIGO_CATEGORIA"].astype(str).isin(categoria_ids).all(), "Cada categoría existe en Dim_Poblacion_Especial"),
        ("Fichas enlazadas a centro", ficha["ID_CENTRO_SK"].astype(str).isin(centro_ids).all(), "Cada ID_CENTRO_SK existe en Dim_Centro_Sede"),
        ("Cantidades no negativas", pd.to_numeric(fact["CANTIDAD_APRENDICES"], errors="coerce").ge(0).all(), "CANTIDAD_APRENDICES ≥ 0"),
        ("Horas no negativas", pd.to_numeric(fact["TOTAL_HORAS"], errors="coerce").ge(0).all(), "TOTAL_HORAS ≥ 0"),
        ("Auditoría cubre filas aceptadas", len(audit) >= int(entry.get("rows_accepted", 0)), "La auditoría contiene al menos una fila por ficha aceptada"),
    ]
    return checks


st.set_page_config(page_title="Calidad de datos PE-14", page_icon="✅", layout="wide")
st.title("Validación de calidad de datos · PE-14")
st.caption("SENA · Centro ASTIN · Resultados del ETL normalizador")

folder_text = st.sidebar.text_input("Carpeta de control", str(DEFAULT_OUTPUT))
folder = Path(folder_text).expanduser()
try:
    manifest, manifest_path = read_manifest(folder)
except Exception as exc:
    st.error(f"No se pueden cargar los resultados: {exc}")
    st.stop()

periods = manifest.get("periods", [])
if not periods:
    st.warning("El manifiesto no contiene periodos procesados.")
    st.stop()

periods = sorted(periods, key=lambda row: int(row["period"]))
overview = pd.DataFrame([{
    "Periodo": f"{row['period'] // 100:04d}-{row['period'] % 100:02d}",
    "Fuente": row.get("source", ""),
    "Hechos": row.get("fact_rows", 0),
    "Fichas aceptadas": row.get("rows_accepted", 0),
    "Rechazadas": row.get("rows_rejected", 0),
} for row in periods])
selected_label = st.sidebar.selectbox("Periodo", overview["Periodo"].tolist(), index=len(overview) - 1)
selected = periods[overview.index[overview["Periodo"] == selected_label][0]]

st.subheader("Resumen de periodos")
st.dataframe(overview, use_container_width=True, hide_index=True)

try:
    fact = read_csv(selected, "fact_aprendices", manifest_path)
    audit = read_csv(selected, "auditoria", manifest_path)
    rejected = read_csv(selected, "rechazados", manifest_path)
    metadata = json.loads(artifact_path(selected, "metadata", manifest_path).read_text(encoding="utf-8"))
except Exception as exc:
    st.error(f"No se pudieron leer los artefactos del periodo: {exc}")
    st.stop()

review_count = int((audit.get("ESTADO_CALIDAD", pd.Series(dtype=str)) == "REVISAR").sum())
rejected_count = int((audit.get("ESTADO_CALIDAD", pd.Series(dtype=str)) == "RECHAZADO").sum()) + len(rejected)
st.subheader(f"Detalle del periodo {selected_label}")
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Filas de hecho", f"{len(fact):,}")
m2.metric("Fichas aceptadas", f"{metadata.get('rows_accepted', 0):,}")
m3.metric("En revisión", f"{review_count:,}")
m4.metric("Rechazos", f"{rejected_count:,}")
m5.metric("Categorías", f"{metadata.get('population_categories', 0):,}")

if not audit.empty and "ESTADO_CALIDAD" in audit:
    counts = audit["ESTADO_CALIDAD"].value_counts().rename_axis("Estado").to_frame("Filas")
    st.bar_chart(counts)

st.subheader("Validaciones de integridad")
checks = validate_period(selected, manifest_path, fact, audit)
hash_ok, hash_failed = check_file_hashes(selected, manifest_path)
checks.append(("Huella SHA-256 de artefactos", hash_failed == 0, f"{hash_ok} correctas · {hash_failed} fallidas"))
st.dataframe(pd.DataFrame([{"Control": name, "Resultado": "OK" if ok else "ERROR", "Detalle": detail} for name, ok, detail in checks]), use_container_width=True, hide_index=True)

st.subheader("Descuadres de auditoría")
if "DIFERENCIA" in audit:
    numeric_difference = pd.to_numeric(audit["DIFERENCIA"], errors="coerce").fillna(0)
    discrepancies = audit[numeric_difference.ne(0)]
    st.metric("Filas con diferencia entre categorías y gran total", f"{len(discrepancies):,}")
    st.dataframe(discrepancies, use_container_width=True, hide_index=True)

with st.expander("Advertencias del procesamiento"):
    warnings = metadata.get("warnings", [])
    if warnings:
        st.dataframe(pd.DataFrame({"Advertencia": warnings}), use_container_width=True, hide_index=True)
    else:
        st.success("El ETL no registró advertencias de esquema.")

with st.expander("Auditoría completa"):
    st.dataframe(audit, use_container_width=True, hide_index=True)
with st.expander("Hechos normalizados"):
    st.dataframe(fact, use_container_width=True, hide_index=True)
with st.expander("Rechazos detallados"):
    st.dataframe(rejected, use_container_width=True, hide_index=True)
