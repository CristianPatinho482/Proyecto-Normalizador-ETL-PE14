from __future__ import annotations

import csv
from pathlib import Path


def write_source(path: Path, *, ficha: int = 12345678, count: int = 2, hours: str = "12:30") -> Path:
    static = ["IDENTIFICADOR_FICHA", "CODIGO_PROGRAMA", "CODIGO_CENTRO", "CODIGO_REGIONAL", "GRAN_TOTAL_APRENDICES", "TOTAL_HORAS"]
    categories = [f"CAT_{index:02d}_TOTAL_APRENDICES" for index in range(1, 51)]
    header = static + categories
    values = [ficha, "PROG1", "9217", "76", count, hours] + [count if index == 1 else 0 for index in range(1, 51)]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(["PE-14_ ejecución mensual"])
        writer.writerow(["202605"])
        writer.writerow(header)
        writer.writerow(values)
    return path
