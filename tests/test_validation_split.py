import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load(rel_path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mvs = _load("data_process/make_validation_split.py", "make_validation_split")


class ValidationSplitTest(unittest.TestCase):
    def _corpus(self, n=5000):
        return [f"seq token {i:05d} A C G T" for i in range(n)]

    def test_deterministic_and_partitions_without_overlap(self):
        lines = self._corpus()
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "pre.txt"
            inp.write_text("".join(l + "\n" for l in lines), encoding="utf-8")
            val = Path(tmp) / "val.txt"
            train = Path(tmp) / "train.txt"
            summ1 = mvs.split_file(inp, val, train, val_fraction=0.1, seed=42, progress_interval=0)
            val_lines = val.read_text().splitlines()
            train_lines = train.read_text().splitlines()

        # No overlap, exact partition of the (non-blank) corpus.
        self.assertEqual(set(val_lines) & set(train_lines), set())
        self.assertEqual(sorted(val_lines + train_lines), sorted(lines))
        self.assertEqual(summ1["val_lines"] + summ1["train_lines"], len(lines))
        # Fraction is roughly the requested 10% (hash spread; allow slack).
        self.assertGreater(summ1["val_fraction_actual"], 0.06)
        self.assertLess(summ1["val_fraction_actual"], 0.14)

    def test_same_seed_same_assignment(self):
        lines = self._corpus(2000)
        assign_a = [mvs.is_validation(l, 42, 1000) for l in lines]
        assign_b = [mvs.is_validation(l, 42, 1000) for l in lines]
        self.assertEqual(assign_a, assign_b)

    def test_different_seed_changes_assignment(self):
        lines = self._corpus(2000)
        a = [mvs.is_validation(l, 42, 1000) for l in lines]
        b = [mvs.is_validation(l, 7, 1000) for l in lines]
        self.assertNotEqual(a, b)

    def test_identical_lines_land_on_same_side(self):
        # Exact duplicates must never straddle train/val (the leakage guarantee).
        dup = "A C G T ATG AAA TAA"
        self.assertEqual(mvs.is_validation(dup, 42, 5000), mvs.is_validation(dup, 42, 5000))
        # And stripping is applied, so whitespace variants hash identically.
        self.assertEqual(mvs.split_bucket(dup, 42), mvs.split_bucket("  " + dup + "  \n", 42))

    def test_val_fraction_bounds(self):
        with self.assertRaises(ValueError):
            mvs.val_buckets_for_fraction(0.0)
        with self.assertRaises(ValueError):
            mvs.val_buckets_for_fraction(1.0)
        self.assertEqual(mvs.val_buckets_for_fraction(0.01), 100)


if __name__ == "__main__":
    unittest.main()
