import argparse
from pathlib import Path


STOP_CODONS = ("TAG", "TAA", "TGA")


def find_longest_cds(mrna_sequence, start_codon="ATG", stop_codons=STOP_CODONS):
    start_index = mrna_sequence.find(start_codon)
    longest_cds_info = None

    while start_index != -1:
        end_index = start_index + len(start_codon)
        while end_index < len(mrna_sequence):
            codon = mrna_sequence[end_index : end_index + 3]
            if codon in stop_codons and (end_index - start_index) % 3 == 0:
                current_cds_length = end_index - start_index + 3
                if longest_cds_info is None or current_cds_length > longest_cds_info["length"]:
                    longest_cds_info = {
                        "start": start_index,
                        "end": end_index + 2,
                        "length": current_cds_length,
                    }
                break
            end_index += 1
        start_index = mrna_sequence.find(start_codon, start_index + 1)

    return longest_cds_info


def split_sequence(sequence, cds_info):
    if not cds_info:
        return " ".join(sequence)

    tokens = []
    start = cds_info["start"]
    end = cds_info["end"] + 1

    tokens.extend(sequence[:start])
    cds = sequence[start:end]
    tokens.extend(cds[i : i + 3] for i in range(0, len(cds), 3))
    tokens.extend(sequence[end:])

    return " ".join(tokens)


def iter_fasta_sequences(path):
    current = []
    with path.open("r") as handle:
        for line in handle:
            line = line.strip().upper()
            if not line:
                continue
            if line.startswith(">"):
                if current:
                    yield "".join(current)
                    current = []
                continue
            current.append(line.replace("U", "T"))
    if current:
        yield "".join(current)


def iter_input_files(raw_dir, input_list):
    seen = set()

    candidates = [raw_dir]
    if input_list:
        with Path(input_list).open("r") as handle:
            candidates.extend(Path(line.strip()) for line in handle if line.strip())

    for candidate in candidates:
        if candidate.is_dir():
            files = sorted(
                path
                for path in candidate.rglob("*")
                if path.suffix.lower() in {".fa", ".fasta", ".fna"}
            )
        else:
            files = [candidate]

        for path in files:
            path = path.resolve()
            if path in seen:
                continue
            seen.add(path)
            yield path


def process_files(raw_dir, input_list, output_dir, output_name):
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / output_name

    total_sequences = 0
    with output_path.open("w") as output:
        for path in iter_input_files(raw_dir, input_list):
            file_sequences = 0
            for sequence in iter_fasta_sequences(path):
                cds_info = find_longest_cds(sequence)
                output.write(split_sequence(sequence, cds_info) + "\n")
                file_sequences += 1
            total_sequences += file_sequences
            print(f"{path}\t{file_sequences}")

    print(f"Processed {total_sequences} sequences -> {output_path}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Stream FASTA files into mRNABERT pretraining text format."
    )
    parser.add_argument(
        "--raw-dir",
        "--raw_dir",
        type=Path,
        default=Path("raw"),
        help="Directory containing extracted FASTA files. Defaults to ./raw.",
    )
    parser.add_argument(
        "--input-list",
        "--input_list",
        default=None,
        help="Optional text file containing one FASTA path per line.",
    )
    parser.add_argument(
        "--output-dir",
        "--output_dir",
        type=Path,
        required=True,
        help="Output directory. The merged training file is written here.",
    )
    parser.add_argument(
        "--output-name",
        "--output_name",
        default="pre.txt",
        help="Output filename inside --output-dir. Defaults to pre.txt.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    process_files(args.raw_dir, args.input_list, args.output_dir, args.output_name)
