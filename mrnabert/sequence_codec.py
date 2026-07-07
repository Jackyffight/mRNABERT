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
    """Return the CDS approximated as the longest in-frame ORF (ATG..in-frame stop).

    This is a heuristic, and a deliberately narrow one. The true coding sequence
    is defined by annotation and start-site selection (Kozak context / ribosome
    scanning) and is frequently *not* the longest ORF: upstream ORFs, weak-context
    upstream AUGs, and annotated CDS shorter than an incidental downstream ORF all
    break the assumption, so this injects some label noise into the UTR/CDS
    boundary signal. Prefer curated RefSeq/GENCODE CDS coordinates when they are
    available; this fallback is for when they are not.

    Implementation: a single left-to-right pass per reading frame (O(n) total).
    Within a frame an ORF opens at the first start codon following the previous
    in-frame stop and closes at the next in-frame stop. Selection is deterministic
    — the longest ORF wins, ties broken by earliest start — matching the previous
    quadratic "scan every ATG" implementation byte-for-byte on the encoded output.
    """

    stop_set = set(stop_codons)
    length = len(mrna_sequence)
    regions: list[CDSRegion] = []

    for frame in range(3):
        orf_start: int | None = None
        for index in range(frame, length - 2, 3):
            codon = mrna_sequence[index : index + 3]
            if orf_start is None:
                if codon == start_codon:
                    orf_start = index
            elif codon in stop_set:
                regions.append(CDSRegion(start=orf_start, end=index + 3))
                orf_start = None

    if not regions:
        return None
    return max(regions, key=lambda region: (region.length, -region.start))


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
