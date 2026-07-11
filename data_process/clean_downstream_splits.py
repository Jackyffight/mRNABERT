"""Remove exact sequence leakage across train/dev/test CSV splits."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


SPLITS_BY_PRIORITY = ("test", "dev", "train")


def clean_splits(input_dir: Path, output_dir: Path) -> dict:
    rows_by_split: dict[str, list[dict[str, str]]] = {}
    fieldnames = None
    for split in SPLITS_BY_PRIORITY:
        path = input_dir / f"{split}.csv"
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None or "sequence" not in reader.fieldnames:
                raise ValueError(f"CSV must contain a sequence column: {path}")
            if fieldnames is None:
                fieldnames = reader.fieldnames
            elif reader.fieldnames != fieldnames:
                raise ValueError(f"CSV columns differ across splits: {path}")
            rows_by_split[split] = list(reader)

    seen: set[str] = set()
    cleaned: dict[str, list[dict[str, str]]] = {}
    removed: dict[str, dict[str, int]] = {}
    for split in SPLITS_BY_PRIORITY:
        kept = []
        seen_before_split = set(seen)
        seen_in_split: set[str] = set()
        counts = {"empty": 0, "within_split": 0, "cross_split": 0}
        for row in rows_by_split[split]:
            sequence = row["sequence"].strip()
            if not sequence:
                counts["empty"] += 1
                continue
            if sequence in seen_before_split:
                counts["cross_split"] += 1
                continue
            if sequence in seen_in_split:
                counts["within_split"] += 1
                continue
            seen_in_split.add(sequence)
            kept.append(row)
        seen.update(seen_in_split)
        cleaned[split] = kept
        removed[split] = counts

    output_dir.mkdir(parents=True, exist_ok=True)
    for split in ("train", "dev", "test"):
        output_path = output_dir / f"{split}.csv"
        with output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(cleaned[split])

    summary = {
        "input_dir": str(input_dir.resolve()),
        "output_dir": str(output_dir.resolve()),
        "priority": list(SPLITS_BY_PRIORITY),
        "input_counts": {split: len(rows_by_split[split]) for split in ("train", "dev", "test")},
        "output_counts": {split: len(cleaned[split]) for split in ("train", "dev", "test")},
        "removed_duplicates": {
            split: removed[split]["within_split"] + removed[split]["cross_split"]
            for split in ("train", "dev", "test")
        },
        "removed_within_split": {
            split: removed[split]["within_split"] for split in ("train", "dev", "test")
        },
        "removed_cross_split": {
            split: removed[split]["cross_split"] for split in ("train", "dev", "test")
        },
        "removed_empty": {split: removed[split]["empty"] for split in ("train", "dev", "test")},
    }
    with (output_dir / "dedup_summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return summary


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    summary = clean_splits(args.input_dir, args.output_dir)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
