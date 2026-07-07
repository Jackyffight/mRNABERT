"""Single-file FASTA -> mRNABERT pretraining text (UTR bases + CDS codons).

Thin wrapper over the canonical codec in ``mrnabert.sequence_codec`` so this path
and ``main.py preprocess`` (the streaming path) produce byte-identical output. The
codec normalizes each sequence (uppercase, U->T) before finding the CDS, so RNA or
lowercase input is handled correctly. For directory-scale/streaming preprocessing
use ``python main.py preprocess`` instead.
"""

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mrnabert.sequence_codec import encode_mrna_sequence  # noqa: E402


def read_fasta_sequences(input_file_path):
    sequences = []
    current_sequence = ""
    with open(input_file_path, "r") as input_file:
        for line in input_file:
            if line.startswith(">"):
                if current_sequence:
                    sequences.append(current_sequence)
                current_sequence = ""
            else:
                current_sequence += line.strip()
    if current_sequence:
        sequences.append(current_sequence)
    return sequences


def process_fasta_and_split_sequence(input_file_path, output_file_path):
    start_time = time.time()
    sequences = read_fasta_sequences(input_file_path)
    with open(output_file_path, "w") as output_file:
        for mrna_sequence in sequences:
            output_file.write(encode_mrna_sequence(mrna_sequence) + "\n")
    print(f"Process completed. Total runtime: {time.time() - start_time:.6f} seconds")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Process a FASTA file into mRNABERT pretraining text (UTR split to bases, longest CDS split to codons)."
    )
    parser.add_argument("--input_file", type=str, default="data_process/pre-train/pre_input.fasta", help="Path to the input FASTA file.")
    parser.add_argument("--output_file", type=str, default="sample_data/pre.txt", help="Path to the output file.")

    args = parser.parse_args()

    process_fasta_and_split_sequence(args.input_file, args.output_file)
    print("Process completed. Results have been saved to:", args.output_file)
