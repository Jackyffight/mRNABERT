"""Strict FASTA parsing for project inputs."""

from __future__ import annotations

from pathlib import Path

from .domain import FastaRecord


def parse_fasta(path: Path) -> list[FastaRecord]:
    if not path.is_file():
        raise ValueError(f"FASTA file not found: {path}")

    records: list[FastaRecord] = []
    seen_ids: set[str] = set()
    record_id: str | None = None
    description = ""
    sequence_parts: list[str] = []

    def finish_record() -> None:
        nonlocal record_id, description, sequence_parts
        if record_id is None:
            return
        sequence = "".join(sequence_parts).replace(" ", "").upper()
        if not sequence:
            raise ValueError(f"FASTA record {record_id!r} has no sequence in: {path}")
        records.append(FastaRecord(record_id, description, sequence))
        record_id = None
        description = ""
        sequence_parts = []

    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if line.startswith(">"):
                finish_record()
                header = line[1:].strip()
                if not header:
                    raise ValueError(f"Empty FASTA header at {path}:{line_number}")
                parts = header.split(maxsplit=1)
                record_id = parts[0]
                description = parts[1] if len(parts) == 2 else ""
                if record_id in seen_ids:
                    raise ValueError(f"Duplicate FASTA ID {record_id!r} in: {path}")
                seen_ids.add(record_id)
                continue
            if record_id is None:
                raise ValueError(f"Sequence before first FASTA header at {path}:{line_number}")
            sequence_parts.append("".join(line.split()))

    finish_record()
    if not records:
        raise ValueError(f"No FASTA records found in: {path}")
    return records
