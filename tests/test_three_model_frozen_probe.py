import csv
from pathlib import Path
import tempfile
import unittest

from data_process.frozen_embedding_common import (
    load_regression_records,
    normalize_nucleotide_sequence,
)


class FrozenEmbeddingCommonTest(unittest.TestCase):
    def test_normalize_spaced_codon_and_bracketed_rna(self):
        self.assertEqual(normalize_nucleotide_sequence("[AUG GCC] UAA"), "ATGGCCTAA")

    def test_normalize_rejects_non_nucleotide_tokens(self):
        with self.assertRaisesRegex(ValueError, "Unsupported nucleotide symbols"):
            normalize_nucleotide_sequence("ATG ZZZ")

    def test_load_records_preserves_model_input_and_stable_alignment_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "train.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=("sequence", "label"))
                writer.writeheader()
                writer.writerow({"sequence": "ATG GCC TAA", "label": "1.5"})

            record = load_regression_records(path)[0]

        self.assertEqual(record.sequence, "ATG GCC TAA")
        self.assertEqual(record.normalized_sequence, "ATGGCCTAA")
        self.assertEqual(len(record.sequence_sha256), 64)


class ThreeModelProbeScriptContractTest(unittest.TestCase):
    def test_evo2_model_and_package_are_pinned(self):
        setup = Path("scripts/setup_evo2_baseline_nas.sh").read_text(encoding="utf-8")
        downloader = Path("data_process/download_evo2_baseline.py").read_text(encoding="utf-8")

        self.assertIn('"evo2==0.6.0"', setup)
        self.assertIn(
            "https://files.pythonhosted.org/packages/e2/ed/"
            "9dab64893b6b78f832e4d18522bbd6696350a415c20e0af6bcea1b0f8152/"
            "vtx-1.1.0-py3-none-any.whl",
            setup,
        )
        self.assertIn("0ff9f1db2f9e81e288150b60fd4fe4832b8b992ac2c6c947271b2036ffeb8299", setup)
        self.assertNotIn('"vtx==1.1.0"', setup)
        self.assertIn("bda0089f92582d5baabf0f22d9fc85f3588f6b58", downloader)
        self.assertIn("c66645929dc1b9c631f5be656da8726f38946315dc9167000a615dd626fcecf4", downloader)

    def test_flash_attention_builds_against_worker_torch_for_a100(self):
        setup = Path("scripts/setup_evo2_baseline_nas.sh").read_text(encoding="utf-8")

        self.assertIn("FLASH_ATTENTION_FORCE_BUILD=TRUE", setup)
        self.assertIn("FLASH_ATTN_CUDA_ARCHS=80", setup)
        self.assertIn("MAX_JOBS=8", setup)
        self.assertIn("--no-cache-dir", setup)
        self.assertIn("--no-binary", setup)
        self.assertIn("--no-build-isolation", setup)
        self.assertIn('"flash-attn==2.8.0.post2"', setup)
        self.assertIn("import flash_attn, flash_attn_2_cuda", setup)
        self.assertNotIn("flash_attn-2.8.0.post2%2Bcu12torch2.7", setup)

    def test_comparison_uses_shared_probe_and_dev_selection(self):
        runner = Path("scripts/run_three_model_frozen_probe_nas.sh").read_text(encoding="utf-8")
        evaluator = Path("data_process/evaluate_frozen_embeddings.py").read_text(encoding="utf-8")

        self.assertIn("internal-checkpoint-$STEP", runner)
        self.assertIn("public-YYLY66", runner)
        self.assertIn("evo2-7b", runner)
        self.assertIn("blocks.28.mlp.l3", runner)
        self.assertIn('FROZEN_CHECKPOINTS=(', runner)
        self.assertIn("Recovering frozen internal encoder", runner)
        self.assertIn("PCA(n_components=args.probe_dim", evaluator)
        self.assertIn("projector.fit_transform(train", evaluator)
        self.assertNotIn("projector.fit_transform(dev", evaluator)
        self.assertIn("--probe-dim 256", runner)
        self.assertIn("selected only by dev Spearman", evaluator)
        self.assertNotIn("test_metrics[\"spearman\"]", evaluator)


if __name__ == "__main__":
    unittest.main()
