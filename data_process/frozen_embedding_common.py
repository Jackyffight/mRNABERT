"""Shared, dependency-light helpers for frozen embedding benchmarks."""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from pathlib import Path


SPLITS = ("train", "dev", "test")
VALID_NUCLEOTIDES = frozenset("ACGTN")


@dataclass(frozen=True)
class RegressionRecord:
    sequence: str
    normalized_sequence: str
    label: float
    sequence_sha256: str


def normalize_nucleotide_sequence(sequence: str) -> str:
    """Convert spaced codons or bracketed mRNA text to an Evo-compatible DNA string."""

    normalized = "".join(sequence.split()).replace("[", "").replace("]", "")
    normalized = normalized.upper().replace("U", "T")
    if not normalized:
        raise ValueError("Sequence is empty after normalization")
    invalid = sorted(set(normalized) - VALID_NUCLEOTIDES)
    if invalid:
        raise ValueError(f"Unsupported nucleotide symbols: {''.join(invalid)}")
    return normalized


def sequence_sha256(sequence: str) -> str:
    normalized = normalize_nucleotide_sequence(sequence)
    return hashlib.sha256(normalized.encode("ascii")).hexdigest()


def load_regression_records(path: Path) -> list[RegressionRecord]:
    records = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or not {"sequence", "label"}.issubset(reader.fieldnames):
            raise ValueError(f"Expected sequence,label columns in: {path}")
        for row_number, row in enumerate(reader, start=2):
            sequence = (row.get("sequence") or "").strip()
            label_text = (row.get("label") or "").strip()
            if not label_text:
                continue
            try:
                label = float(label_text)
                normalized = normalize_nucleotide_sequence(sequence)
            except ValueError as error:
                raise ValueError(f"Invalid record at {path}:{row_number}: {error}") from error
            records.append(
                RegressionRecord(
                    sequence=sequence,
                    normalized_sequence=normalized,
                    label=label,
                    sequence_sha256=hashlib.sha256(normalized.encode("ascii")).hexdigest(),
                )
            )
    if not records:
        raise ValueError(f"No labeled records found in: {path}")
    return records
