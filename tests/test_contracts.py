import unittest

from pe14_etl.contracts import ContractViolation
from pe14_etl.io import RawTable, extract_table
from pe14_etl.normalize import build_population_schema, normalize_table

from tests.support import write_source
from pe14_etl.io import read_raw_table
from pathlib import Path
import tempfile


class ContractTests(unittest.TestCase):
    def test_schema_precondition_requires_at_least_fifty_categories(self):
        with self.assertRaises(ContractViolation):
            build_population_schema(["IDENTIFICADOR_FICHA", "CODIGO_PROGRAMA", "CODIGO_CENTRO", "CAT_TOTAL_APRENDICES"])

    def test_normalization_postconditions_and_referential_integrity(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
            source = write_source(Path(directory) / "PE-14_mayo 2026.csv")
            table = extract_table(read_raw_table(source))
            schema = build_population_schema(table.header)
            result = normalize_table(table, schema, 202605)
        self.assertEqual(len(result.fact), 1)
        fact = result.fact[0]
        self.assertEqual(fact["CANTIDAD_APRENDICES"], 2)
        self.assertIn(fact["ID_FICHA_SK"], {row["ID_FICHA_SK"] for row in result.dim_ficha})
        self.assertIn(fact["CODIGO_CATEGORIA"], {row["CODIGO_CATEGORIA"] for row in result.dim_poblacion})
        self.assertEqual(fact["ESTADO_CALIDAD"], "OK")

    def test_missing_required_key_is_rejected_at_extraction(self):
        rows = [["A"] * 50]
        with self.assertRaises(ContractViolation):
            extract_table(RawTable(path=Path("bad.csv"), kind="csv", rows=rows), header_row=0)


if __name__ == "__main__":
    unittest.main()
