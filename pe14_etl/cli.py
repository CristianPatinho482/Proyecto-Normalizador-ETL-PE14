"""Interfaz de línea de comandos para procesar uno o varios PE-14."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .contracts import MODULE_ID, MODULE_VERSION
from .io import discover_sources, extract_table, period_from_filename, period_from_preamble, read_raw_table
from .normalize import build_population_schema, normalize_table
from .persist import relocate_legacy_control_files, write_manifest, write_period_artifacts


def process_file(
    source: Path,
    output_dir: Path,
    control_dir: Path,
    numeric_mode: str,
) -> dict[str, Any]:
    raw = read_raw_table(source)
    header_index = None
    table = extract_table(raw, header_index)
    period = period_from_filename(source.name) or period_from_preamble(raw.rows, table.header_row)
    if period is None:
        raise ValueError("No se pudo inferir el periodo YYYYMM desde el nombre ni la cabecera.")
    schema = build_population_schema(table.header)
    result = normalize_table(table, schema, period, numeric_mode=numeric_mode)
    metadata = {
        "module_id": MODULE_ID,
        "module_version": MODULE_VERSION,
        "source_name": source.name,
        "source_path": str(source.resolve()),
        "source_kind": raw.kind,
        "source_sha256": raw.sha256,
        "source_bytes": raw.size_bytes,
        "encoding": raw.encoding,
        "delimiter": raw.delimiter,
        "sheet_name": raw.sheet_name,
        "header_row": table.header_row + 1,
        "source_rows_total": table.source_rows_total,
        "source_rows_nonblank": table.source_rows_nonblank,
        "blank_rows_skipped": table.blank_rows_skipped,
        "period": period,
        "period_source": "nombre_archivo" if period_from_filename(source.name) is not None else "cabecera",
        "categories_detected": [item.category.key for item in schema.categories],
        "population_categories": len(schema.categories),
        "rows_accepted": result.rows_accepted,
        "rows_rejected": result.rows_rejected,
        "rows_duplicate": result.rows_duplicate,
        "rows_in_review": result.rows_in_review,
        "rows_without_facts": result.rows_without_facts,
        "unique_fichas": result.unique_fichas,
        "fact_rows": result.fact_rows,
        "total_apprentices": result.total_apprentices,
        "total_hours": format(result.total_hours, ".2f"),
        "total_hours_fact": format(result.total_hours_fact, ".2f"),
        "hours_numeric_mode": numeric_mode,
        "data_fingerprint": result.data_fingerprint,
        "warnings": list(result.warnings),
    }
    artifacts = write_period_artifacts(result, output_dir, control_dir, metadata)
    return {
        "source": source.name,
        "period": period,
        "source_sha256": raw.sha256,
        "data_fingerprint": result.data_fingerprint,
        "fact_rows": result.fact_rows,
        "rows_accepted": result.rows_accepted,
        "rows_rejected": result.rows_rejected,
        "artifacts": artifacts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Normaliza reportes PE-14 a CSV en 1FN.")
    parser.add_argument("source", nargs="?", default="PE14", help="Archivo o carpeta de entrada (por defecto: ./PE14).")
    parser.add_argument("--output", help="Carpeta de salida (por defecto: la carpeta de entrada).")
    parser.add_argument("--control-output", help="Carpeta para auditoría, rechazados, metadatos y manifiesto (por defecto: ./control_pe14 en la raíz del proyecto).")
    parser.add_argument("--no-recursive", action="store_true", help="No recorrer subcarpetas.")
    parser.add_argument("--hours-numeric-mode", choices=("hours", "days"), default="hours", help="Interpretación de celdas numéricas de TOTAL_HORAS.")
    args = parser.parse_args(argv)
    source = Path(args.source).expanduser().resolve()
    output = Path(args.output).expanduser().resolve() if args.output else (source.parent if source.is_file() else source)
    project_root = Path(__file__).resolve().parents[1]
    control_output = Path(args.control_output).expanduser().resolve() if args.control_output else project_root / "control_pe14"
    if not source.exists():
        parser.error(f"No existe la ruta de entrada: {source}")
    if output == control_output:
        parser.error("--output y --control-output deben apuntar a carpetas distintas.")
    excluded_dirs = tuple(
        path for path in (output, control_output)
        if path != source and source in path.parents
    )
    candidates = discover_sources(
        source,
        recursive=not args.no_recursive,
        exclude_dirs=excluded_dirs,
    )
    if not candidates:
        print(f"No se encontraron archivos fuente compatibles en {source}", file=sys.stderr)
        return 2
    relocated = relocate_legacy_control_files(output, control_output)
    if relocated:
        print(f"Se trasladaron {len(relocated)} archivos de control anteriores a {control_output}")
    entries: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for candidate in candidates:
        try:
            entries.append(process_file(candidate, output, control_output, args.hours_numeric_mode))
            print(f"OK {candidate.name}")
        except Exception as exc:
            failures.append({"source": str(candidate), "error": str(exc)})
            print(f"ERROR {candidate.name}: {exc}", file=sys.stderr)
    manifest = write_manifest(control_output, entries)
    print(json.dumps({"processed": len(entries), "failed": len(failures), "manifest": str(manifest), "failures": failures}, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
