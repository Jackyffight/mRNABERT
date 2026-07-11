import json
import math
import tempfile
import unittest
from pathlib import Path

from data_process.summarize_regression_results import load_results, summarize


class SummarizeRegressionResultsTest(unittest.TestCase):
    def _write(self, root: Path, run: str, spearman: float) -> None:
        path = root / run / "results" / run / "eval_results.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "eval_spearman_corr": spearman,
                    "eval_pearson_corr": spearman + 0.1,
                    "eval_r2_score": spearman - 0.1,
                    "eval_mse_loss": 1.0 - spearman,
                }
            ),
            encoding="utf-8",
        )

    def test_groups_seeded_runs_and_calculates_sample_std(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(root, "internal-seed13", 0.2)
            self._write(root, "internal-seed42", 0.4)

            rows = load_results(root)
            summaries = summarize(rows)

        self.assertEqual([row["seed"] for row in rows], [13, 42])
        self.assertEqual(len(summaries), 1)
        self.assertAlmostEqual(summaries[0]["eval_spearman_corr_mean"], 0.3)
        self.assertAlmostEqual(summaries[0]["eval_spearman_corr_std"], 0.1414213562)

    def test_nan_metric_is_reported_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(root, "random-seed13", 0.2)
            self._write(root, "random-seed42", math.nan)

            rows = load_results(root)
            summaries = summarize(rows)

        self.assertEqual(summaries[0]["eval_spearman_corr_valid"], 1)
        self.assertAlmostEqual(summaries[0]["eval_spearman_corr_mean"], 0.2)
        self.assertEqual(summaries[0]["eval_spearman_corr_std"], 0.0)

    def test_legacy_baseline_name_is_labeled_as_full_finetune_1e4(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(root, "internal-checkpoint-600000-seed42", 0.8)
            rows = load_results(root)

        self.assertEqual(rows[0]["model"], "internal-checkpoint-600000-full-lr1e-4")

    def test_reads_best_dev_metric_from_latest_checkpoint_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run = "internal-seed42"
            self._write(root, run, 0.8)
            for step, best_metric in ((20, 0.5), (40, 0.7)):
                path = root / run / f"checkpoint-{step}" / "trainer_state.json"
                path.parent.mkdir(parents=True)
                path.write_text(
                    json.dumps({"global_step": step, "best_metric": best_metric}),
                    encoding="utf-8",
                )

            rows = load_results(root)

        self.assertEqual(rows[0]["dev_best_spearman"], 0.7)


if __name__ == "__main__":
    unittest.main()
