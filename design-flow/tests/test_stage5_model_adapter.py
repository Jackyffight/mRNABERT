from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from design_flow.stage5_model_adapter import (
    build_disorder_observations,
    build_tmbed_observations,
    parse_tmbed_three_line,
)


def _candidate(sequence: str = "AAAAAAAAAAAAAAAA") -> dict[str, str]:
    return {
        "candidate_id": "candidate-fixture",
        "amino_acid_sequence": sequence,
        "amino_acid_sha256": "a" * 64,
    }


class Stage5ModelAdapterTests(unittest.TestCase):
    def test_parses_tmbed_and_builds_two_distinct_evidence_types(self) -> None:
        labels = "SSS..HHH...bbb.."
        sequence = "A" * len(labels)
        with tempfile.TemporaryDirectory() as temporary_dir:
            path = Path(temporary_dir) / "tmbed.pred"
            path.write_text(
                f">c000\n{sequence}\n{labels}\n",
                encoding="utf-8",
            )

            predictions = parse_tmbed_three_line(path)
            signal, topology = build_tmbed_observations(
                predictions, {"c000": _candidate(sequence)}
            )

        self.assertEqual(len(signal), 1)
        self.assertEqual(signal[0]["residue_start"], 1)
        self.assertEqual(signal[0]["residue_end"], 3)
        self.assertEqual(signal[0]["status"], "context")
        self.assertEqual(len(topology), 2)
        self.assertEqual(topology[0]["segment_type"], "alpha_helix")
        self.assertEqual(topology[0]["orientation"], "inside_to_outside")
        self.assertEqual(topology[0]["residue_start"], 6)
        self.assertEqual(topology[0]["residue_end"], 8)
        self.assertEqual(topology[1]["segment_type"], "beta_strand")
        self.assertEqual(topology[1]["orientation"], "outside_to_inside")
        self.assertEqual(topology[1]["residue_start"], 12)
        self.assertEqual(topology[1]["residue_end"], 14)

    def test_rejects_tmbed_sequence_mismatch(self) -> None:
        predictions = {"c000": {"sequence": "AAAA", "labels": "...."}}
        with self.assertRaisesRegex(ValueError, "differs from candidate"):
            build_tmbed_observations(predictions, {"c000": _candidate("AAAT")})

    def test_builds_one_based_disorder_observations_from_python_boundaries(self) -> None:
        sequence = "A" * 16
        scores = [0.1] + [0.8, 0.9, 0.7] + [0.2] * 12
        raw = {
            "model_version": "V3",
            "records": {
                "c000": {
                    "sequence": sequence,
                    "scores": scores,
                    "disordered_domain_boundaries": [[1, 4]],
                    "folded_domain_boundaries": [[4, 16]],
                }
            },
        }

        observations = build_disorder_observations(
            raw, {"c000": _candidate(sequence)}
        )

        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["residue_start"], 2)
        self.assertEqual(observations[0]["residue_end"], 4)
        self.assertEqual(observations[0]["mean_disorder_score"], 0.8)
        self.assertEqual(observations[0]["maximum_disorder_score"], 0.9)
        self.assertEqual(observations[0]["status"], "context")

    def test_rejects_out_of_range_disorder_scores(self) -> None:
        sequence = "AAAA"
        raw = {
            "model_version": "V3",
            "records": {
                "c000": {
                    "sequence": sequence,
                    "scores": [0.1, 1.1, 0.2, 0.3],
                    "disordered_domain_boundaries": [],
                }
            },
        }
        with self.assertRaisesRegex(ValueError, "invalid scores"):
            build_disorder_observations(raw, {"c000": _candidate(sequence)})


if __name__ == "__main__":
    unittest.main()
