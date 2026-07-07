"""Deterministic hash-based train/validation split for mRNABERT pretraining text.

A held-out validation set is the precondition for honest checkpoint selection (pick
the next base checkpoint by validation loss, not the resume-distorted train loss).
The split is by a hash of the (stripped) line, so **identical sequences always land
on the same side** — removing exact-duplicate leakage between train and validation,
which matters for a redundant multi-species mRNA corpus. Deterministic given --seed.

For a genuinely clean holdout, train from the emitted `--train-out` complement (not
the original `pre.txt`), then evaluate checkpoints on `--val-out`. Evaluating a
checkpoint that was trained on the full corpus against this split is only a proxy:
some validation lines were already seen, so the loss is mildly optimistic.

Usage:
  python data_process/make_validation_split.py \
    --input /path/pre.txt --val-out valid.txt --train-out train_holdout.txt \
    --val-fraction 0.01 --seed 42
"""

from __future__ import annotations

import argparse
import hashlib
import time
from pathlib import Path

BUCKETS = 10_000


def split_bucket(line: str, seed: int) -> int:
    """Stable bucket in [0, BUCKETS) for a line, keyed by seed and stripped content."""
    key = f"{seed}\x00{line.strip()}".encode("utf-8")
    return int.from_bytes(hashlib.blake2b(key, digest_size=8).digest(), "big") % BUCKETS


def is_validation(line: str, seed: int, val_per_10k: int) -> bool:
    return split_bucket(line, seed) < val_per_10k


def val_buckets_for_fraction(val_fraction: float) -> int:
    if not 0.0 < val_fraction < 1.0:
        raise ValueError("--val-fraction must be strictly between 0 and 1.")
    return max(1, min(BUCKETS - 1, round(val_fraction * BUCKETS)))


def split_file(
    input_path: Path,
    val_path: Path,
    train_path: Path | None,
    val_fraction: float,
    seed: int,
    progress_interval: float = 30.0,
) -> dict:
    val_per_10k = val_buckets_for_fraction(val_fraction)
    val_path.parent.mkdir(parents=True, exist_ok=True)
    if train_path is not None:
        train_path.parent.mkdir(parents=True, exist_ok=True)

    val_count = 0
    train_count = 0
    skipped_blank = 0
    started = time.time()
    last_report = started

    train_handle = train_path.open("w", encoding="utf-8") if train_path is not None else None
    try:
        with input_path.open("r", encoding="utf-8", errors="ignore") as source, val_path.open(
            "w", encoding="utf-8"
        ) as val_handle:
            for line in source:
                stripped = line.strip()
                if not stripped:
                    skipped_blank += 1
                    continue
                if is_validation(stripped, seed, val_per_10k):
                    val_handle.write(stripped + "\n")
                    val_count += 1
                else:
                    train_count += 1
                    if train_handle is not None:
                        train_handle.write(stripped + "\n")

                if progress_interval > 0:
                    now = time.time()
                    if now - last_report >= progress_interval:
                        total = val_count + train_count
                        print(
                            f"split_progress total={total} val={val_count} train={train_count} "
                            f"elapsed={int(now - started)}s",
                            flush=True,
                        )
                        last_report = now
    finally:
        if train_handle is not None:
            train_handle.close()

    total = val_count + train_count
    summary = {
        "input": str(input_path),
        "val_out": str(val_path),
        "train_out": str(train_path) if train_path is not None else None,
        "seed": seed,
        "val_fraction_requested": val_fraction,
        "val_buckets_per_10k": val_per_10k,
        "kept_lines": total,
        "val_lines": val_count,
        "train_lines": train_count,
        "skipped_blank": skipped_blank,
        "val_fraction_actual": (val_count / total) if total else 0.0,
    }
    print(
        f"split_done kept={total} val={val_count} train={train_count} "
        f"val_fraction_actual={summary['val_fraction_actual']:.4f} skipped_blank={skipped_blank}",
        flush=True,
    )
    return summary


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, required=True, help="Input pretraining text file (pre.txt).")
    parser.add_argument("--val-out", "--val_out", type=Path, required=True, help="Output validation file.")
    parser.add_argument(
        "--train-out", "--train_out", type=Path, default=None,
        help="Optional complement (train-without-val); train from this for a clean holdout.",
    )
    parser.add_argument("--val-fraction", "--val_fraction", type=float, default=0.01, help="Validation fraction (0,1).")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic split seed.")
    parser.add_argument("--progress-interval", "--progress_interval", type=float, default=30.0)
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    if not args.input.exists():
        raise SystemExit(f"Input file not found: {args.input}")
    split_file(
        input_path=args.input,
        val_path=args.val_out,
        train_path=args.train_out,
        val_fraction=args.val_fraction,
        seed=args.seed,
        progress_interval=args.progress_interval,
    )


if __name__ == "__main__":
    main()
