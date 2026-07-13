from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from design_flow.cli import main as cli_main
from design_flow.domain import FastaRecord
from design_flow.fasta import parse_fasta
from design_flow.pipeline import analyze_project
from design_flow.qc import analyze_sequence_pairs, normalize_nucleotide, translate_cds
from design_flow.reporting import write_run_artifacts


VALID_AA = [
    FastaRecord("protein_1", "", "MAA"),
    FastaRecord("protein_2", "", "MKF"),
    FastaRecord("protein_3", "", "MGP"),
]
VALID_CDS = [
    FastaRecord("protein_1", "", "ATGGCTGCTTAA"),
    FastaRecord("protein_2", "", "ATGAAATTTTGA"),
    FastaRecord("protein_3", "", "ATGGGTCCTTAG"),
]


class FastaTests(unittest.TestCase):
    def test_parses_multiline_records_and_comments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            path = Path(temporary_dir) / "records.fasta"
            path.write_text(
                "; comment\n>protein_1 first protein\nMA\nA\n\n>protein_2\nMKF\n",
                encoding="utf-8",
            )
            records = parse_fasta(path)

        self.assertEqual([record.record_id for record in records], ["protein_1", "protein_2"])
        self.assertEqual(records[0].description, "first protein")
        self.assertEqual(records[0].sequence, "MAA")

    def test_rejects_duplicate_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            path = Path(temporary_dir) / "records.fasta"
            path.write_text(">same\nMAA\n>same\nMKF\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate FASTA ID"):
                parse_fasta(path)


class SequenceAuditTests(unittest.TestCase):
    def test_matching_three_proteins_pass(self) -> None:
        proteins, issues = analyze_sequence_pairs(VALID_AA, VALID_CDS, expected_count=3)

        self.assertEqual(issues, [])
        self.assertEqual([protein.status for protein in proteins], ["pass", "pass", "pass"])
        self.assertTrue(all(protein.metrics["translation_matches"] for protein in proteins))
        self.assertEqual(proteins[0].metrics["aa_length"], 3)
        self.assertEqual(proteins[0].metrics["cds_length_nt"], 12)

    def test_translation_mismatch_reports_first_position(self) -> None:
        proteins, issues = analyze_sequence_pairs(
            [FastaRecord("protein_1", "", "MGA")],
            [FastaRecord("protein_1", "", "ATGGCTGCTTAA")],
            expected_count=1,
        )

        self.assertEqual(issues, [])
        mismatch = next(issue for issue in proteins[0].issues if issue.code == "translation_mismatch")
        self.assertIn("position 2", mismatch.message)
        self.assertEqual(proteins[0].status, "fail")

    def test_missing_pair_and_count_fail_at_project_level(self) -> None:
        proteins, issues = analyze_sequence_pairs(
            VALID_AA,
            VALID_CDS[:2],
            expected_count=3,
        )

        self.assertEqual(len(proteins), 2)
        self.assertIn("cds_record_count", {issue.code for issue in issues})
        self.assertIn("missing_cds", {issue.code for issue in issues})

    def test_rna_is_normalized_before_translation(self) -> None:
        normalized, normalization_issues = normalize_nucleotide("AUGGCUUAA", "protein_1")
        translated, translation_issues, terminal_stop = translate_cds(normalized, "protein_1")

        self.assertEqual(normalized, "ATGGCTTAA")
        self.assertEqual(translated, "MA")
        self.assertTrue(terminal_stop)
        self.assertEqual([issue.code for issue in normalization_issues], ["rna_normalized"])
        self.assertEqual(translation_issues, [])


class EndToEndTests(unittest.TestCase):
    def _write_project(self, root: Path) -> Path:
        input_dir = root / "input"
        input_dir.mkdir(parents=True)
        (input_dir / "proteins_aa.fasta").write_text(
            ">protein_1\nMAA\n>protein_2\nMKF\n>protein_3\nMGP\n",
            encoding="utf-8",
        )
        (input_dir / "proteins_cds.fasta").write_text(
            ">protein_1\nATGGCTGCTTAA\n"
            ">protein_2\nATGAAATTTTGA\n"
            ">protein_3\nATGGGTCCTTAG\n",
            encoding="utf-8",
        )
        config = {
            "schema_version": 1,
            "project_id": "test-three-protein",
            "expected_protein_count": 3,
            "inputs": {
                "amino_acid_fasta": "input/proteins_aa.fasta",
                "nucleotide_fasta": "input/proteins_cds.fasta",
            },
            "outputs": {"run_root": "runs"},
            "context": {
                "protein_expression_host": "unspecified",
                "mrna_target_species": "unspecified",
            },
        }
        config_path = root / "project.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        return config_path

    def test_run_writes_reproducible_artifact_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            analysis = analyze_project(self._write_project(root))
            run_dir = write_run_artifacts(
                analysis,
                now=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(analysis.status, "pass")
            self.assertEqual(
                {path.name for path in run_dir.iterdir()},
                {"manifest.json", "proteins.json", "proteins.csv", "qc_issues.csv", "report.md"},
            )
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            latest = json.loads((root / "runs" / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "pass")
            self.assertEqual(manifest["stages"]["sequence_audit"], "pass")
            self.assertEqual(manifest["stages"]["structure_prediction"], "not_evaluated")
            self.assertEqual(latest["run_id"], manifest["run_id"])
            self.assertIn("does not establish", (run_dir / "report.md").read_text(encoding="utf-8"))

    def test_cli_validate_returns_zero_for_valid_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            config_path = self._write_project(Path(temporary_dir))
            self.assertEqual(cli_main(["validate", str(config_path)]), 0)

    def test_cli_init_refuses_to_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            project_dir = Path(temporary_dir) / "new-project"
            self.assertEqual(cli_main(["init", str(project_dir)]), 0)
            self.assertEqual(cli_main(["init", str(project_dir)]), 1)


if __name__ == "__main__":
    unittest.main()
