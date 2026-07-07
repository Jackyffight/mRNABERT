import tempfile
import unittest
from pathlib import Path

from mrnabert.sequence_codec import (
    CDSRegion,
    discover_fasta_files,
    encode_mrna_sequence,
    find_longest_cds,
    iter_fasta_records,
    normalize_sequence,
    split_sequence_by_option,
)


class SequenceCodecTest(unittest.TestCase):
    def test_normalize_sequence_uppercases_and_converts_u(self):
        self.assertEqual(normalize_sequence(" auCg\n"), "ATCG")

    def test_find_longest_cds_uses_in_frame_stop_codon(self):
        region = find_longest_cds("CCCATGAAATAAGGATGCCCCCCTGA")
        self.assertEqual(region, CDSRegion(start=14, end=26))

    def test_encode_mrna_sequence_splits_utr_as_bases_and_cds_as_codons(self):
        encoded = encode_mrna_sequence("CCCATGAAATAAGG")
        self.assertEqual(encoded, "C C C ATG AAA TAA G G")

    def test_encode_mrna_sequence_without_cds_splits_all_bases(self):
        encoded = encode_mrna_sequence("AACCGG")
        self.assertEqual(encoded, "A A C C G G")

    def test_find_longest_cds_returns_none_without_start_codon(self):
        self.assertIsNone(find_longest_cds("AAACCCGGGTTT"))

    def test_find_longest_cds_breaks_ties_by_earliest_start(self):
        # Two equal-length in-frame ORFs; the earlier one must win (deterministic).
        self.assertEqual(find_longest_cds("ATGTAAATGTAA"), CDSRegion(start=0, end=6))

    def test_encode_normalizes_rna_and_lowercase(self):
        # U->T + uppercasing happens before CDS finding, so RNA/lowercase input
        # tokenizes identically to its DNA-uppercase form.
        self.assertEqual(encode_mrna_sequence("cccaugaaauaagg"), "C C C ATG AAA TAA G G")

    def test_split_complete_sequence_uses_bracketed_cds(self):
        encoded = split_sequence_by_option("AA[ATGCCCTAA]TT", "complete")
        self.assertEqual(encoded, "A A ATG CCC TAA T T")

    def test_iter_fasta_records_concatenates_multiline_sequences(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "input.fasta"
            path.write_text(">a\nAAU\nCC\n>b\nGG\n", encoding="ascii")
            records = list(iter_fasta_records(path))
        self.assertEqual([record.sequence for record in records], ["AATCC", "GG"])

    def test_discover_fasta_files_deduplicates_raw_dir_and_input_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            fasta = root / "a.fasta"
            fasta.write_text(">a\nAA\n", encoding="ascii")
            ignored = root / "a.txt"
            ignored.write_text(">x\nCC\n", encoding="ascii")
            input_list = root / "inputs.txt"
            input_list.write_text(str(fasta), encoding="ascii")
            self.assertEqual(discover_fasta_files(root, str(input_list)), [fasta.resolve()])


if __name__ == "__main__":
    unittest.main()
