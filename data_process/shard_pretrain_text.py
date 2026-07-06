#!/usr/bin/env python3
"""Shard preprocessed mRNABERT text data for distributed streaming training."""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import time
from pathlib import Path


BUFFER_SIZE = 16 * 1024 * 1024


def format_bytes(value: float) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TiB"


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def manifest_matches(manifest_path: Path, input_path: Path, output_dir: Path, shards: int, seed: int) -> bool:
    if not manifest_path.exists():
        return False
    try:
        with manifest_path.open("r") as handle:
            manifest = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return False

    stat = input_path.stat()
    if manifest.get("input_path") != str(input_path.resolve()):
        return False
    if manifest.get("input_size") != stat.st_size:
        return False
    if manifest.get("input_mtime_ns") != stat.st_mtime_ns:
        return False
    if manifest.get("shards") != shards:
        return False
    if manifest.get("seed") != seed:
        return False

    shard_files = manifest.get("shard_files") or []
    if len(shard_files) != shards:
        return False
    return all((output_dir / name).exists() for name in shard_files)


def shard_file(
    input_path: Path,
    output_dir: Path,
    shards: int,
    seed: int,
    progress_interval: float,
    overwrite: bool,
) -> None:
    input_path = input_path.resolve()
    output_dir = output_dir.resolve()
    manifest_path = output_dir / "manifest.json"

    if manifest_matches(manifest_path, input_path, output_dir, shards, seed) and not overwrite:
        print(f"Shard cache is ready: {output_dir}", flush=True)
        return

    if output_dir.exists():
        if not overwrite:
            print(f"Shard cache is stale, rebuilding: {output_dir}", flush=True)
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    tmp_dir = output_dir.with_name(f".{output_dir.name}.tmp.{os.getpid()}")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)

    stat = input_path.stat()
    shard_names = [f"pre_shard_{idx:05d}.txt" for idx in range(shards)]
    shard_paths = [tmp_dir / name for name in shard_names]
    line_counts = [0 for _ in range(shards)]
    byte_counts = [0 for _ in range(shards)]
    rng = random.Random(seed)

    print(
        "Sharding pretrain text "
        f"input={input_path} size={format_bytes(stat.st_size)} shards={shards} seed={seed}",
        flush=True,
    )
    start_time = time.time()
    last_report = start_time
    bytes_read = 0
    lines_read = 0

    handles = [path.open("wb", buffering=BUFFER_SIZE) for path in shard_paths]
    try:
        with input_path.open("rb", buffering=BUFFER_SIZE) as input_handle:
            for raw_line in input_handle:
                bytes_read += len(raw_line)
                if not raw_line.strip():
                    continue
                shard_id = rng.randrange(shards)
                handles[shard_id].write(raw_line)
                line_counts[shard_id] += 1
                byte_counts[shard_id] += len(raw_line)
                lines_read += 1

                now = time.time()
                if progress_interval > 0 and now - last_report >= progress_interval:
                    elapsed = max(1e-6, now - start_time)
                    rate = bytes_read / elapsed
                    remaining = max(0, stat.st_size - bytes_read)
                    eta = remaining / rate if rate > 0 else 0
                    pct = bytes_read / stat.st_size * 100 if stat.st_size else 100.0
                    print(
                        "shard_progress "
                        f"bytes={format_bytes(bytes_read)}/{format_bytes(stat.st_size)} "
                        f"pct={pct:.2f}% lines={lines_read} "
                        f"rate={format_bytes(rate)}/s elapsed={format_duration(elapsed)} "
                        f"eta={format_duration(eta)}",
                        flush=True,
                    )
                    last_report = now
    finally:
        for handle in handles:
            handle.close()

    for shard_path, shard_name in zip(shard_paths, shard_names):
        final_path = output_dir / shard_name
        shard_path.replace(final_path)
    shutil.rmtree(tmp_dir, ignore_errors=True)

    elapsed = max(1e-6, time.time() - start_time)
    manifest = {
        "input_path": str(input_path),
        "input_size": stat.st_size,
        "input_mtime_ns": stat.st_mtime_ns,
        "shards": shards,
        "seed": seed,
        "shard_files": shard_names,
        "line_counts": line_counts,
        "byte_counts": byte_counts,
        "total_lines": lines_read,
        "total_bytes": bytes_read,
        "elapsed_seconds": elapsed,
        "average_rate_bytes_per_second": bytes_read / elapsed,
    }
    with manifest_path.open("w") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)

    print(
        "shard_done "
        f"output_dir={output_dir} lines={lines_read} bytes={format_bytes(bytes_read)} "
        f"elapsed={format_duration(elapsed)} rate={format_bytes(bytes_read / elapsed)}/s",
        flush=True,
    )
    for idx, (lines, size) in enumerate(zip(line_counts, byte_counts)):
        print(f"shard_{idx:05d} lines={lines} bytes={format_bytes(size)}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Input pre.txt file.")
    parser.add_argument("--output-dir", required=True, help="Directory for shard files and manifest.")
    parser.add_argument("--shards", type=int, required=True, help="Number of output shards.")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic random assignment seed.")
    parser.add_argument("--progress-interval", type=float, default=30.0, help="Progress log interval in seconds.")
    parser.add_argument("--overwrite", action="store_true", help="Rebuild shards even when manifest matches.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.shards < 1:
        raise SystemExit("--shards must be >= 1")
    shard_file(
        input_path=Path(args.input),
        output_dir=Path(args.output_dir),
        shards=args.shards,
        seed=args.seed,
        progress_interval=args.progress_interval,
        overwrite=args.overwrite,
    )


if __name__ == "__main__":
    main()
