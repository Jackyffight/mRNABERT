import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "plot_model_comparison.py"
DATA_PATH = REPO_ROOT / "docs" / "reports" / "model-comparison-data-20260716.json"


def load_plot_module():
    spec = importlib.util.spec_from_file_location("plot_model_comparison", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ModelComparisonPlotTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_plot_module()

    def test_audited_evidence_validates(self):
        data = self.module.load_json(DATA_PATH)
        self.module.validate_evidence(data)

    def test_favorable_claims_are_derived_from_audited_values(self):
        data = self.module.load_json(DATA_PATH)
        models = {model["id"]: model for model in data["models"]}
        proxy = {row["model_id"]: row for row in data["proxy_mlm"]["results"]}
        sweep = data["mrfp"]["full_finetune_lr_sweep"]

        parameter_reduction = 1 - (
            models["internal-600k"]["parameter_count"]
            / models["public-mrnabert"]["parameter_count"]
        )
        throughput_gain = (
            proxy["internal-600k"]["samples_per_second"]
            / proxy["public-mrnabert"]["samples_per_second"]
            - 1
        )
        evaluation_time_reduction = 1 - (
            proxy["public-mrnabert"]["samples_per_second"]
            / proxy["internal-600k"]["samples_per_second"]
        )
        internal = [
            row["test"]["spearman_mean"]
            for row in sweep
            if row["model_id"] == "internal-600k"
        ]
        public = [
            row["test"]["spearman_mean"]
            for row in sweep
            if row["model_id"] == "public-mrnabert"
        ]

        self.assertAlmostEqual(parameter_reduction, 0.2412, places=4)
        self.assertAlmostEqual(throughput_gain, 0.0879, places=4)
        self.assertAlmostEqual(evaluation_time_reduction, 0.0808, places=4)
        self.assertAlmostEqual(min(internal) - min(public), 0.3134, places=4)

    def test_shared_probe_results_map_all_three_models(self):
        payload = {
            "protocol": "frozen-mean-pooled-l2-embedding-train-pca-ridge-v1",
            "split_counts": {"train": 1018, "dev": 219, "test": 219},
            "results": [
                {"model": "internal-checkpoint-600000", "test_metrics": {"spearman": 0.7}},
                {"model": "public-YYLY66", "test_metrics": {"spearman": 0.71}},
                {"model": "evo2-7b", "test_metrics": {"spearman": 0.69}},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            results = self.module.load_three_model_results(path)

        self.assertEqual(
            set(results),
            {"internal-600k", "public-mrnabert", "evo2-7b"},
        )

    def test_shared_probe_rejects_incomplete_model_set(self):
        payload = {
            "protocol": "frozen-mean-pooled-l2-embedding-train-pca-ridge-v1",
            "split_counts": {"train": 1018, "dev": 219, "test": 219},
            "results": [
                {"model": "internal-checkpoint-600000", "test_metrics": {"spearman": 0.7}},
                {"model": "public-YYLY66", "test_metrics": {"spearman": 0.71}},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must contain exactly"):
                self.module.load_three_model_results(path)


if __name__ == "__main__":
    unittest.main()
