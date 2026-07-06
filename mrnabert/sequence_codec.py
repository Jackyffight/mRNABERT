"""Sequence encoding for mRNABERT.

The training format is one whitespace-tokenized mRNA sequence per line:
UTR regions are split into single bases and CDS regions are split into codons.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence


STOP_CODONS = ("TAG", "TAA", "TGA")
FASTA_SUFFIXES = {".fa", ".fasta", ".fna"}


@dataclass(frozen=True)
class CDSRegion:
    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class FastaRecord:
    sequence: str
    file_index: int
    bytes_read: int


def normalize_sequence(sequence: str | bytes) -> str:
    if isinstance(sequence, bytes):
        sequence = sequence.decode("ascii")
    return sequence.strip().upper().replace("U", "T")


def find_longest_cds(
    mrna_sequence: str,
    start_codon: str = "ATG",
    stop_codons: Sequence[str] = STOP_CODONS,
) -> CDSRegion | None:
    """Find the longest in-frame ORF.

    This intentionally preserves the original repository's heuristic: scan every
    ATG and keep the longest ATG...stop region found in that frame.
    """

    start_index = mrna_sequence.find(start_codon)
    longest: CDSRegion | None = None

    while start_index != -1:
        for end_index in range(start_index + len(start_codon), len(mrna_sequence) - 2, 3):
            codon = mrna_sequence[end_index : end_index + 3]
            if codon in stop_codons:
                region = CDSRegion(start=start_index, end=end_index + 3)
                if longest is None or region.length > longest.length:
                    longest = region
                break
        start_index = mrna_sequence.find(start_codon, start_index + 1)

    return longest


def encode_mrna_sequence(sequence: str, cds_region: CDSRegion | None = None) -> str:
    sequence = normalize_sequence(sequence)
    if cds_region is None:
        cds_region = find_longest_cds(sequence)
    if cds_region is None:
        return " ".join(sequence)

    tokens: list[str] = []
    tokens.extend(sequence[: cds_region.start])
    cds = sequence[cds_region.start : cds_region.end]
    tokens.extend(cds[i : i + 3] for i in range(0, len(cds), 3))
    tokens.extend(sequence[cds_region.end :])
    return " ".join(tokens)


def split_sequence_by_option(sequence: str, option: str) -> str:
    """Encode a fine-tuning sequence with the repository's split options."""

    sequence = normalize_sequence(sequence)
    if option == "utr":
        return " ".join(sequence)
    if option == "codon":
        return " ".join(sequence[i : i + 3] for i in range(0, len(sequence), 3))
    if option != "complete":
        raise ValueError(f"Unsupported split option: {option}")

    tokens: list[str] = []
    cds_flag = False
    cds_sequence = ""

    for char in sequence:
        if char == "[":
            cds_flag = True
            if cds_sequence:
                tokens.extend(cds_sequence)
                cds_sequence = ""
        elif char == "]":
            cds_flag = False
            if cds_sequence:
                tokens.extend(cds_sequence[i : i + 3] for i in range(0, len(cds_sequence), 3))
                cds_sequence = ""
        elif cds_flag:
            cds_sequence += char
        else:
            tokens.append(char)

    if cds_sequence:
        tokens.extend(cds_sequence[i : i + 3] for i in range(0, len(cds_sequence), 3))

    return " ".join(tokens)


def iter_fasta_records(path: Path, file_index: int = 1) -> Iterator[FastaRecord]:
    current: list[bytes] = []
    bytes_read = 0

    with path.open("rb") as handle:
        for raw_line in handle:
            bytes_read += len(raw_line)
            line = raw_line.strip().upper()
            if not line:
                continue
            if line.startswith(b">"):
                if current:
                    yield FastaRecord(
                        sequence=normalize_sequence(b"".join(current)),
                        file_index=file_index,
                        bytes_read=bytes_read,
                    )
                    current = []
                continue
            current.append(line.replace(b"U", b"T"))

    if current:
        yield FastaRecord(
            sequence=normalize_sequence(b"".join(current)),
            file_index=file_index,
            bytes_read=bytes_read,
        )


def discover_fasta_files(raw_dir: Path, input_list: str | None = None) -> list[Path]:
    seen: set[Path] = set()
    candidates: list[Path] = [raw_dir]

    if input_list:
        with Path(input_list).open("r") as handle:
            candidates.extend(Path(line.strip()) for line in handle if line.strip())

    files: list[Path] = []
    for candidate in candidates:
        if candidate.is_dir():
            discovered = sorted(path for path in candidate.rglob("*") if path.suffix.lower() in FASTA_SUFFIXES)
        else:
            discovered = [candidate]

        for path in discovered:
            path = path.resolve()
            if path in seen:
                continue
            seen.add(path)
            files.append(path)

    return files


def iter_all_fasta_records(input_files: Iterable[Path]) -> Iterator[FastaRecord]:
    for file_index, path in enumerate(input_files, start=1):
        yield from iter_fasta_records(path, file_index=file_index)


def encode_record(record: FastaRecord) -> tuple[str, int, int]:
    return encode_mrna_sequence(record.sequence), record.file_index, record.bytes_read
