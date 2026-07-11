import csv
import tempfile
import unittest
from pathlib import Path

from data_process.clean_downstream_splits import clean_splits


def _write_split(path: Path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["sequence", "label"])
        writer.writerows(rows)


class CleanDownstreamSplitsTest(unittest.TestCase):
    def test_test_then_dev_take_priority_over_train(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            _write_split(
                source / "train.csv",
                [("AAA", "1"), ("BBB", "2"), ("EEE", "3"), ("EEE", "4")],
            )
            _write_split(source / "dev.csv", [("BBB", "4"), ("CCC", "5")])
            _write_split(source / "test.csv", [("AAA", "6"), ("DDD", "7")])

            summary = clean_splits(source, output)

            self.assertEqual(summary["output_counts"], {"train": 1, "dev": 2, "test": 2})
            self.assertEqual(summary["removed_duplicates"], {"train": 3, "dev": 0, "test": 0})
            self.assertEqual(summary["removed_cross_split"], {"train": 2, "dev": 0, "test": 0})
            self.assertEqual(summary["removed_within_split"], {"train": 1, "dev": 0, "test": 0})

    def test_real_mrfp_removes_known_train_test_overlap(self):
        source = Path("sample_data/fine-tune/mRFP")
        with tempfile.TemporaryDirectory() as tmpdir:
            summary = clean_splits(source, Path(tmpdir))
        self.assertEqual(summary["input_counts"], {"train": 1021, "dev": 219, "test": 219})
        self.assertEqual(summary["removed_duplicates"]["train"], 3)
        self.assertEqual(summary["removed_cross_split"]["train"], 2)
        self.assertEqual(summary["removed_within_split"]["train"], 1)
        self.assertEqual(summary["output_counts"]["train"], 1018)


if __name__ == "__main__":
    unittest.main()
