#!/usr/bin/env python3
"""Execute metapredict inside its isolated tool environment."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path
from typing import Any

import metapredict


def _read_fasta(path: Path) -> dict[str, str]:
    records: dict[str, str] = {}
    record_id: str | None = None
    chunks: list[str] = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if record_id is not None:
                records[record_id] = "".join(chunks)
            record_id = line[1:].split(maxsplit=1)[0]
            if not record_id or record_id in records:
                raise ValueError(f"Invalid FASTA record ID at line {line_number}")
            chunks = []
        elif record_id is None:
            raise ValueError(f"FASTA sequence appears before a header at line {line_number}")
        else:
            chunks.append(line.upper())
    if record_id is not None:
        records[record_id] = "".join(chunks)
    if not records or any(not sequence for sequence in records.values()):
        raise ValueError("Input FASTA contains no complete records")
    return records


def _serializable_records(
    predictions: dict[str, Any],
    expected_sequences: dict[str, str],
) -> dict[str, dict[str, Any]]:
    if set(predictions) != set(expected_sequences):
        raise ValueError("metapredict returned a different record set")
    records = {}
    for record_id, prediction in predictions.items():
        sequence = prediction.sequence
        scores = prediction.disorder
        if hasattr(scores, "tolist"):
            scores = scores.tolist()
        boundaries = [
            [int(value) for value in boundary]
            for boundary in prediction.disordered_domain_boundaries
        ]
        if sequence != expected_sequences[record_id]:
            raise ValueError(f"metapredict sequence mismatch: {record_id}")
        records[record_id] = {
            "sequence": sequence,
            "scores": [float(score) for score in scores],
            "disordered_domain_boundaries": boundaries,
            "folded_domain_boundaries": [
                [int(value) for value in boundary]
                for boundary in prediction.folded_domain_boundaries
            ],
        }
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-fasta", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--model-version", default="V3")
    parser.add_argument("--minimum-idr-length", type=int, default=12)
    parser.add_argument("--minimum-folded-domain-length", type=int, default=50)
    parser.add_argument("--gap-closure", type=int, default=10)
    args = parser.parse_args()

    sequences = _read_fasta(args.input_fasta)
    predictions = metapredict.predict_disorder(
        sequences,
        version=args.model_version,
        device=args.device,
        normalized=True,
        round_values=False,
        return_numpy=False,
        return_domains=True,
        minimum_IDR_size=args.minimum_idr_length,
        minimum_folded_domain=args.minimum_folded_domain_length,
        gap_closure=args.gap_closure,
        show_progress_bar=True,
    )
    document = {
        "schema_version": "vaxflow.metapredict-raw.v1",
        "package_version": importlib.metadata.version("metapredict"),
        "model_version": args.model_version,
        "device": args.device,
        "parameters": {
            "minimum_idr_length": args.minimum_idr_length,
            "minimum_folded_domain_length": args.minimum_folded_domain_length,
            "gap_closure": args.gap_closure,
        },
        "records": _serializable_records(predictions, sequences),
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
