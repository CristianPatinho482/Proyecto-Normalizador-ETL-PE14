import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pe14_etl.cli import main, process_file
from pe14_etl.io import discover_sources
from pe14_etl.persist import write_manifest
from tests.support import write_source


class IntegrationAndIdempotenceTests(unittest.TestCase):
    def test_cli_separates_tables_and_control_outputs_and_migrates_legacy_files(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
            root = Path(directory)
            source_dir, output, control = root / "input", root / "powerbi", root / "control"
            source_dir.mkdir()
            output.mkdir()
            write_source(source_dir / "PE-14_mayo 2026.csv")
            legacy = output / "pe14_curated_2026_04_auditoria.csv"
            legacy.write_text("legacy", encoding="utf-8")

            exit_code = main([
                str(source_dir), "--output", str(output), "--control-output", str(control)
            ])

            self.assertEqual(exit_code, 0)
            self.assertEqual(len(list(output.glob("*.csv"))), 5)
            self.assertFalse(legacy.exists())
            self.assertTrue((control / legacy.name).exists())
            self.assertTrue((control / "pe14_curated_2026_05_metadata.json").exists())
            self.assertTrue((control / "pe14_curated_manifest.json").exists())

    def test_file_to_artifacts_and_rerun_are_idempotent(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
            root = Path(directory)
            source_dir, output, control = root / "input", root / "output", root / "control"
            source_dir.mkdir()
            source = write_source(source_dir / "PE-14_mayo 2026.csv")
            original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            first = process_file(source, output, control, "hours")
            write_manifest(control, [first])
            first_bytes = {
                str(path.relative_to(root)): path.read_bytes()
                for folder in (output, control) for path in folder.iterdir()
            }
            second = process_file(source, output, control, "hours")
            second_bytes = {
                str(path.relative_to(root)): path.read_bytes()
                for folder in (output, control) for path in folder.iterdir()
            }
            self.assertEqual(first["data_fingerprint"], second["data_fingerprint"])
            self.assertEqual(first_bytes, second_bytes)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), original_hash)
            self.assertEqual(len(discover_sources(output)), 0)
            self.assertEqual({path.suffix for path in output.iterdir()}, {".csv"})
            self.assertEqual(len(list(output.glob("*.csv"))), 5)
            self.assertTrue((control / "pe14_curated_2026_05_auditoria.csv").exists())
            self.assertTrue((control / "pe14_curated_manifest.json").exists())
            manifest = json.loads((control / "pe14_curated_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(Path(manifest["periods"][0]["artifacts"]["fact_aprendices"]), output / "pe14_curated_2026_05_fact_aprendices.csv")

    def test_legacy_control_files_are_moved_out_of_data_directory(self):
        from pe14_etl.persist import relocate_legacy_control_files

        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
            root = Path(directory)
            data_dir, control_dir = root / "data", root / "control"
            data_dir.mkdir()
            legacy = data_dir / "pe14_curated_2026_05_auditoria.csv"
            legacy.write_text("audit", encoding="utf-8")
            (data_dir / "pe14_curated_2026_05_fact_aprendices.csv").write_text("fact", encoding="utf-8")
            moved = relocate_legacy_control_files(data_dir, control_dir)
            self.assertEqual(len(moved), 1)
            self.assertFalse(legacy.exists())
            self.assertTrue((control_dir / legacy.name).exists())
            self.assertTrue((data_dir / "pe14_curated_2026_05_fact_aprendices.csv").exists())

    def test_available_monthly_corpus_passes_end_to_end(self):
        project = Path(__file__).resolve().parents[1]
        source_dir = project / "PE14"
        sources = discover_sources(source_dir)
        if not sources:
            self.skipTest("No se encontró la carpeta PE14 con reportes de muestra")
        expected_periods = {202605, 202606, 202607, 202608, 202610}
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
            results = [
                process_file(path, Path(directory) / "data", Path(directory) / "control", "hours")
                for path in sources
            ]
        self.assertEqual({item["period"] for item in results}, expected_periods)
        self.assertTrue(all(item["fact_rows"] > 0 for item in results))
        self.assertTrue(all(item["rows_rejected"] == 0 for item in results))


if __name__ == "__main__":
    unittest.main()
