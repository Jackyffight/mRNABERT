import csv
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from mrnabert.sequence_codec import encode_mrna_sequence, split_sequence_by_option

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load(rel_path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ppd = _load("data_process/process_pretrain_data.py", "process_pretrain_data")
pfd = _load("data_process/process_finetune_data.py", "process_finetune_data")


class PretrainPreprocessParityTest(unittest.TestCase):
    def test_output_matches_codec_and_normalizes_rna(self):
        seqs = ["cccaugaaauaagg", "AUCG", "GGGCCCATGAAATAGTTT"]
        with tempfile.TemporaryDirectory() as tmp:
            fasta = Path(tmp) / "in.fasta"
            fasta.write_text("".join(f">s{i}\n{s}\n" for i, s in enumerate(seqs)), encoding="ascii")
            out = Path(tmp) / "out.txt"
            ppd.process_fasta_and_split_sequence(str(fasta), str(out))
            lines = out.read_text(encoding="utf-8").splitlines()

        # The flat single-file script now yields exactly the canonical codec output.
        self.assertEqual(lines, [encode_mrna_sequence(s) for s in seqs])
        # And RNA/lowercase is normalized (this is the drift the consolidation fixed).
        self.assertEqual(lines[0], "C C C ATG AAA TAA G G")


class FinetunePreprocessParityTest(unittest.TestCase):
    def _run(self, rows, option):
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "train.csv"
            outp = Path(tmp) / "out.csv"
            with open(inp, "w", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerows(rows)
            pfd.process_csv(str(inp), str(outp), option)
            with open(outp, encoding="utf-8") as handle:
                return list(csv.reader(handle))

    def test_complete_option_matches_codec(self):
        rows = [["sequence", "label"], ["aucg", "1"], ["AA[augcccuaa]tt", "0"]]
        got = self._run(rows, "complete")
        self.assertEqual(got[0], ["sequence", "label"])
        self.assertEqual(got[1], [split_sequence_by_option("aucg", "complete"), "1"])
        self.assertEqual(got[2], [split_sequence_by_option("AA[augcccuaa]tt", "complete"), "0"])
        self.assertEqual(got[2][0], "A A ATG CCC TAA T T")

    def test_codon_and_utr_options_match_codec(self):
        rows = [["sequence", "label"], ["AUGCCCAAA", "2"]]
        codon = self._run(rows, "codon")
        self.assertEqual(codon[1], [split_sequence_by_option("AUGCCCAAA", "codon"), "2"])
        utr = self._run(rows, "utr")
        self.assertEqual(utr[1], [split_sequence_by_option("AUGCCCAAA", "utr"), "2"])


if __name__ == "__main__":
    unittest.main()
