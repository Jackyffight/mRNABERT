"""Split fine-tuning CSV sequences into mRNABERT tokens.

Thin wrapper over ``mrnabert.sequence_codec.split_sequence_by_option`` (the same
codon/UTR logic used for pretraining) so fine-tuning input cannot drift from the
canonical tokenization. Options: ``utr`` (single bases), ``codon`` (triplets),
``complete`` (single bases for UTR, codons inside ``[...]``-marked CDS). The codec
normalizes each sequence (uppercase, U->T) first.
"""

import argparse
import csv
import glob
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mrnabert.sequence_codec import split_sequence_by_option  # noqa: E402


def process_csv(input_file, output_file, option):
    """Split every sequence in a CSV, writing a `sequence,label` file with the same rows."""
    output_dir = os.path.dirname(output_file)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)

    try:
        with open(input_file, mode="r", encoding="utf-8") as infile, open(
            output_file, mode="w", newline="", encoding="utf-8"
        ) as outfile:
            csv_reader = csv.reader(infile)
            csv_writer = csv.writer(outfile)

            for idx, row in enumerate(csv_reader):
                if not row:  # Skip empty rows
                    continue
                if idx == 0:
                    csv_writer.writerow(["sequence", "label"])
                else:
                    processed_sequence = split_sequence_by_option(row[0], option)
                    csv_writer.writerow([processed_sequence] + row[1:])
    except Exception as e:  # noqa: BLE001 - preserve the original best-effort behavior
        print(f"Error processing file {input_file}: {e}")


def process_path(input_dir, output_dir, option):
    """Split every CSV in ``input_dir`` into ``output_dir`` keeping filenames."""
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    for csv_file in glob.glob(os.path.join(input_dir, "*.csv")):
        file_name = os.path.basename(csv_file)
        output_file = os.path.join(output_dir, file_name)
        process_csv(csv_file, output_file, option)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Split sequences in all CSV files in a directory and write them to another directory, keeping filenames."
    )
    parser.add_argument("--input_dir", type=str, required=True, help="Directory containing input CSV files.")
    parser.add_argument("--output_dir", type=str, required=True, help="Directory where processed files will be stored.")
    parser.add_argument(
        "--split_option",
        choices=["utr", "codon", "complete"],
        default="codon",
        help="'utr' (single base split), 'codon' (triplet split), or 'complete' (bases for UTR, codons for [CDS]). Default 'codon'.",
    )

    args = parser.parse_args()

    process_path(args.input_dir, args.output_dir, args.split_option)

    print("Splitting of all CSV files completed. Results have been saved to:", args.output_dir)
