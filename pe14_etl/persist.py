"""Escritura determinista y atómica de los artefactos PE-14."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, ".2f")
    return value


def _csv_bytes(rows: Iterable[dict[str, Any]], columns: tuple[str, ...]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _json_value(row.get(key, "")) for key in columns})
    return buffer.getvalue().encode("utf-8-sig")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".pe14-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def relocate_legacy_control_files(data_dir: str | Path, control_dir: str | Path) -> list[Path]:
    """Mueve sidecars de versiones anteriores fuera de la carpeta analítica."""
    source_dir, target_dir = Path(data_dir), Path(control_dir)
    if not source_dir.is_dir() or source_dir.resolve() == target_dir.resolve():
        return []
    suffixes = ("_auditoria.csv", "_rechazados.csv", "_metadata.json")
    legacy = [
        path for path in source_dir.iterdir()
        if path.is_file()
        and path.name.startswith("pe14_curated_")
        and (path.name == "pe14_curated_manifest.json" or path.name.endswith(suffixes))
    ]
    relocated: list[Path] = []
    for source in legacy:
        content = source.read_bytes()
        destination = target_dir / source.name
        if destination.exists() and destination.read_bytes() != content:
            digest = hashlib.sha256(content).hexdigest()[:10]
            destination = target_dir / f"{source.stem}_legacy_{digest}{source.suffix}"
        if destination.exists():
            source.unlink()
        else:
            _atomic_write(destination, content)
            source.unlink()
        relocated.append(destination)
    return relocated


def write_period_artifacts(
    result: Any,
    output_dir: str | Path,
    control_dir: str | Path,
    metadata: dict[str, Any],
) -> dict[str, str]:
    """Publica tablas analíticas y controles en carpetas separadas."""
    target = Path(output_dir)
    controls = Path(control_dir)
    base = f"pe14_curated_{result.period // 100:04d}_{result.period % 100:02d}"
    data_tables = {
        "fact_aprendices": (result.fact, ("ID_HECHO_UUID", "ID_PERIODO", "ID_FICHA_SK", "CODIGO_FICHA", "CODIGO_CATEGORIA", "CATEGORIA_POBLACION", "CANTIDAD_APRENDICES", "TOTAL_HORAS", "ESTADO_CALIDAD")),
        "dim_ficha_programa": (result.dim_ficha, ("ID_FICHA_SK", "ID_PERIODO", "CODIGO_FICHA", "IDENTIFICADOR_UNICO_FICHA", "CODIGO_PROGRAMA", "VERSION_PROGRAMA", "NOMBRE_PROGRAMA_FORMACION", "MODALIDAD_FORMACION", "NIVEL_FORMACION", "NUMERO_CURSOS", "CODIGO_PROGRAMA_ESPECIAL", "NOMBRE_PROGRAMA_ESPECIAL", "ID_CENTRO_SK", "CODIGO_CENTRO", "NOMBRE_CENTRO", "CODIGO_REGIONAL", "NOMBRE_REGIONAL", "CODIGO_DEPARTAMENTO_CURSO", "NOMBRE_DEPARTAMENTO_CURSO", "CODIGO_MUNICIPIO_CURSO", "NOMBRE_MUNICIPIO_CURSO", "CODIGO_PAIS_CURSO", "NOMBRE_PAIS_CURSO", "CODIGO_CONVENIO", "NOMBRE_CONVENIO", "AMPLIACION_COBERTURA", "GRAN_TOTAL_APRENDICES", "TOTAL_HORAS", "ESTADO_CALIDAD")),
        "dim_centro_sede": (result.dim_centro, ("ID_CENTRO_SK", "CODIGO_REGIONAL", "NOMBRE_REGIONAL", "CODIGO_CENTRO", "NOMBRE_CENTRO", "CODIGO_PAIS_CURSO", "NOMBRE_PAIS_CURSO")),
        "dim_poblacion_especial": (result.dim_poblacion, ("CODIGO_CATEGORIA", "NOMBRE_CATEGORIA", "ORDEN")),
        "dim_periodo": (result.dim_periodo, ("ID_PERIODO", "ANIO", "MES", "NOMBRE_MES", "FECHA_INICIO", "FECHA_FIN")),
    }
    control_tables = {
        "auditoria": (result.audit, ("FILA_ORIGEN", "ID_PERIODO", "CODIGO_FICHA", "GRAN_TOTAL_APRENDICES", "SUMA_CATEGORIAS", "DIFERENCIA", "TOTAL_HORAS_CRUDO", "TOTAL_HORAS_NORMALIZADO", "CATEGORIAS_POSITIVAS", "HECHOS_GENERADOS", "ESTADO_CALIDAD", "OBSERVACIONES")),
        "rechazados": (result.rejected, ("FILA_ORIGEN", "ID_PERIODO", "IDENTIFICADOR_FICHA", "CODIGO_PROGRAMA", "CODIGO_CENTRO", "CATEGORIA_POBLACION", "VALOR_CRUDO", "MOTIVO", "ESTADO_CALIDAD")),
    }
    artifacts: dict[str, str] = {}
    hashes: dict[str, dict[str, Any]] = {}
    for directory, tables in ((target, data_tables), (controls, control_tables)):
        for name, (rows, columns) in tables.items():
            path = directory / f"{base}_{name}.csv"
            content = _csv_bytes(rows, columns)
            _atomic_write(path, content)
            artifacts[name] = str(path)
            hashes[path.name] = {
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
                "path": str(path.resolve()),
            }
    metadata = dict(metadata)
    metadata["artifacts"] = hashes
    metadata["data_output_dir"] = str(target.resolve())
    metadata["control_output_dir"] = str(controls.resolve())
    metadata_path = controls / f"{base}_metadata.json"
    content = (json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True, default=_json_value) + "\n").encode("utf-8")
    _atomic_write(metadata_path, content)
    artifacts["metadata"] = str(metadata_path)
    return artifacts


def write_manifest(control_dir: str | Path, entries: list[dict[str, Any]]) -> Path:
    path = Path(control_dir) / "pe14_curated_manifest.json"
    payload = {"module_id": "ETL-PE14-UNPIVOT-NORMALIZER", "periods_processed": len(entries), "periods": entries}
    _atomic_write(path, (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return path
