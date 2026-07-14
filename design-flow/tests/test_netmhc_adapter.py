from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from design_flow.netmhc_adapter import (
    _preserve_failed_output,
    _short_tool_path,
    build_mhc_observations,
    parse_netmhciipan_xls,
    parse_netmhcpan_xls,
)


class NetMHCAdapterTests(unittest.TestCase):
    def test_predictor_receives_short_paths_relative_to_working_directory(self) -> None:
        working_directory = Path("/very/long/runtime/path/.identity.partial")

        self.assertEqual(
            _short_tool_path(
                working_directory / "raw/class-i-000-BoLA-1_00901.xls",
                working_directory,
            ),
            "raw/class-i-000-BoLA-1_00901.xls",
        )
        self.assertEqual(
            _short_tool_path(working_directory / "candidates.fasta", working_directory),
            "candidates.fasta",
        )

    def test_failed_adapter_output_is_preserved_with_diagnostic_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            partial = root / ".identity.partial"
            failed = root / "identity.failed"
            log = partial / "raw/class-i.log"
            log.parent.mkdir(parents=True)
            log.write_text("predictor failure\n", encoding="utf-8")

            preserved = _preserve_failed_output(
                partial,
                failed,
                identity="identity",
                error=ValueError("fixture failure"),
            )

            self.assertEqual(preserved, failed)
            self.assertFalse(partial.exists())
            self.assertEqual(
                (failed / "raw/class-i.log").read_text(encoding="utf-8"),
                "predictor failure\n",
            )
            failure = (failed / "failure.json").read_text(encoding="utf-8")
            self.assertIn('"error": "fixture failure"', failure)

    def test_parses_both_predictors_and_binds_peptides_to_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            class_i_path = root / "class-i.xls"
            class_i_path.write_text(
                "#netmhcpan command\n"
                "\t\t\tBoLA-1:00901\n"
                "Pos\tPeptide\tID\tcore\ticore\tEL_score\tEL_rank\tBA_score\tBA_rank\tAve\tNB\n"
                "1\tMKTAYIAKQ\tc000\tMKTAYIAKQ\tMKTAYIAKQ\t0.8\t0.4\t0.7\t0.8\t0.8\t1\n"
                "2\tKTAYIAKQR\tc000\tKTAYIAKQR\tKTAYIAKQR\t0.1\t12.0\t0.2\t20.0\t0.1\t0\n",
                encoding="utf-8",
            )
            class_ii_path = root / "class-ii.xls"
            class_ii_path.write_text(
                "#netmhciipan command\n"
                "\t\t\tBoLA-DRB3_00101\n"
                "Pos\tPeptide\tID\tTarget\tCore\tInverted\tScore_EL\tRank_EL\tScore_BA\tnM\tRank_BA\tAve\tNB\n"
                "1\tMKTAYIAKQRQISFV\tc000\tNA\tAYIAKQRQI\t0\t0.4\t3.0\t0.5\t250.0\t2.5\t0.4\t1\n",
                encoding="utf-8",
            )
            predictions = parse_netmhcpan_xls(class_i_path, "BoLA-1:00901")
            predictions += parse_netmhciipan_xls(
                class_ii_path, "BoLA-DRB3_00101"
            )
            candidate = {
                "candidate_id": "candidate-fixture",
                "amino_acid_sequence": "MKTAYIAKQRQISFVKSHFSRQ",
                "amino_acid_sha256": "a" * 64,
            }

            observations = build_mhc_observations(predictions, {"c000": candidate})

            self.assertEqual(len(observations), 3)
            by_peptide = {item["peptide"]: item for item in observations}
            self.assertEqual(by_peptide["MKTAYIAKQ"]["binding_level"], "strong")
            self.assertEqual(by_peptide["MKTAYIAKQ"]["status"], "supported")
            self.assertEqual(by_peptide["KTAYIAKQR"]["status"], "not_supported")
            self.assertEqual(
                by_peptide["MKTAYIAKQRQISFV"]["binding_level"], "weak"
            )
            self.assertEqual(by_peptide["MKTAYIAKQRQISFV"]["affinity_nm"], 250.0)
            self.assertEqual(by_peptide["MKTAYIAKQRQISFV"]["residue_end"], 15)

    def test_rejects_prediction_peptide_that_does_not_match_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            path = Path(temporary_dir) / "class-i.xls"
            path.write_text(
                "Pos\tPeptide\tID\tcore\ticore\tEL_score\tEL_rank\tBA_score\tBA_rank\tAve\tNB\n"
                "1\tAAAAAAAAA\tc000\tAAAAAAAAA\tAAAAAAAAA\t0.8\t0.4\t0.7\t0.8\t0.8\t1\n",
                encoding="utf-8",
            )
            predictions = parse_netmhcpan_xls(path, "BoLA-1:00901")
            candidate = {
                "candidate_id": "candidate-fixture",
                "amino_acid_sequence": "MKTAYIAKQRQISFV",
                "amino_acid_sha256": "a" * 64,
            }

            with self.assertRaisesRegex(ValueError, "does not match"):
                build_mhc_observations(predictions, {"c000": candidate})


if __name__ == "__main__":
    unittest.main()
