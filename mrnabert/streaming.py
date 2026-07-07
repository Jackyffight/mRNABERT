"""Torch-free streaming/sharding logic for local-text pretraining.

The DDP data plane is the highest-risk part of pretraining: an off-by-one in the
rank x dataloader-worker sharding silently drops or duplicates training data, and
a partition count below the world size starves a rank and deadlocks the gradient
all-reduce. To make those invariants unit-testable without a GPU stack, all of the
pure logic lives here (stdlib only, no torch/datasets/transformers). The
``IterableDataset`` wrappers in ``mrnabert.pretrain`` add only tokenization.

Partitioning model: there are ``world_size * num_workers`` partitions; the
partition that a given (rank, dataloader-worker) owns is
``rank * num_workers + worker_id``. Every reader must cover the corpus exactly
once across all partitions with no overlap.
"""

from __future__ import annotations

import os
import random
from typing import Iterable, Iterator, Optional, Sequence


LOCAL_STREAMING_READERS = ("line-stride", "file-shard", "byte-range")


def partition_id_and_count(rank: int, world_size: int, worker_id: int, num_workers: int) -> tuple[int, int]:
    """Map a (rank, worker) pair to its global partition id and the partition count."""
    partition_id = rank * num_workers + worker_id
    num_partitions = max(1, world_size * num_workers)
    return partition_id, num_partitions


def per_partition_cap(max_samples: Optional[int], num_partitions: int) -> Optional[int]:
    """Split a GLOBAL ``max_samples`` budget across partitions.

    Ceil-divide so the sum across partitions is >= max_samples (never silently
    short), while the total stays within one partition's worth of max_samples —
    i.e. ~= max_samples rather than the old ``max_samples * num_partitions``.

    Best-effort, not exact: under streaming the caps are ``~= max_samples``. If a
    partition runs out of lines before hitting its per-partition cap (uneven
    byte-range partitions, or fewer total lines than partitions) the global total
    falls below max_samples. Single-process (num_partitions == 1) is exact:
    ``min(max_samples, corpus_size)``.
    """
    if max_samples is None:
        return None
    partitions = max(1, num_partitions)
    return -(-max_samples // partitions)


def iter_bounded_shuffle(lines: Iterable[str], buffer_size: int, seed: int) -> Iterator[str]:
    """Windowed shuffle: emit from a bounded reservoir so streaming stays O(buffer)."""
    if buffer_size <= 1:
        yield from lines
        return

    rng = random.Random(seed)
    buffer: list[str] = []
    for line in lines:
        if len(buffer) < buffer_size:
            buffer.append(line)
            continue
        index = rng.randrange(len(buffer))
        yield buffer[index]
        buffer[index] = line

    rng.shuffle(buffer)
    yield from buffer


def _decode_line(raw_line: bytes) -> Optional[str]:
    line = raw_line.decode("utf-8", errors="ignore").strip()
    return line or None


def iter_line_stride_lines(files: Sequence[str], partition_id: int, num_partitions: int) -> Iterator[str]:
    """Every rank scans each file; keep lines where line_index % num_partitions == partition_id."""
    for path in files:
        with open(path, "rb") as handle:
            for line_index, raw_line in enumerate(handle):
                if line_index % num_partitions != partition_id:
                    continue
                line = _decode_line(raw_line)
                if line is not None:
                    yield line


def iter_file_shard_lines(
    files: Sequence[str], rank: int, world_size: int, worker_id: int, num_workers: int
) -> Iterator[str]:
    """Assign whole files by rank, then lines within a rank's file by dataloader worker."""
    world = max(1, world_size)
    workers = max(1, num_workers)
    for file_index, path in enumerate(files):
        if file_index % world != rank:
            continue
        with open(path, "rb") as handle:
            for line_index, raw_line in enumerate(handle):
                if line_index % workers != worker_id:
                    continue
                line = _decode_line(raw_line)
                if line is not None:
                    yield line


def _iter_byte_range_file(path: str, start: int, end: int) -> Iterator[str]:
    if end <= start:
        return
    with open(path, "rb") as handle:
        if start > 0:
            # A line belongs to the partition whose range contains its first byte.
            # If the byte before `start` is a newline then `start` begins a fresh
            # line we own; otherwise `start` is mid-line and that line's remainder
            # belongs to the previous partition, so drop it. (Checking this rather
            # than always dropping avoids losing a line whose start is exactly on a
            # partition boundary — which happens for every boundary when lines are
            # equal length and the partition count divides the line count.)
            handle.seek(start - 1)
            if handle.read(1) != b"\n":
                handle.readline()
        else:
            handle.seek(0)
        while handle.tell() < end:
            raw_line = handle.readline()
            if not raw_line:
                break
            line = _decode_line(raw_line)
            if line is not None:
                yield line


def iter_byte_range_lines(files: Sequence[str], partition_id: int, num_partitions: int) -> Iterator[str]:
    """Each partition reads a contiguous seek-based byte range of every file."""
    for path in files:
        size = os.path.getsize(path)
        start = (size * partition_id) // num_partitions
        end = (size * (partition_id + 1)) // num_partitions
        yield from _iter_byte_range_file(path, start, end)


def iter_reader_lines(
    reader: str,
    files: Sequence[str],
    rank: int,
    world_size: int,
    worker_id: int,
    num_workers: int,
) -> Iterator[str]:
    """Dispatch to the reader named by ``reader``, yielding this partition's raw lines."""
    partition_id, num_partitions = partition_id_and_count(rank, world_size, worker_id, num_workers)
    if reader == "line-stride":
        return iter_line_stride_lines(files, partition_id, num_partitions)
    if reader == "file-shard":
        return iter_file_shard_lines(files, rank, world_size, worker_id, num_workers)
    if reader == "byte-range":
        return iter_byte_range_lines(files, partition_id, num_partitions)
    raise ValueError(f"Unknown local streaming reader: {reader!r}; expected one of {LOCAL_STREAMING_READERS}")


def validate_reader_partitions(reader: str, num_files: int, world_size: int) -> None:
    """Fail fast on a config that would starve a DDP rank and hang the all-reduce.

    ``file-shard`` assigns whole files by ``file_index % world_size``; with fewer
    shard files than ranks, some ranks iterate zero examples, drop out of the
    gradient all-reduce, and deadlock the job instead of erroring. Catch it before
    the datasets are built.
    """
    if reader == "file-shard" and world_size > 1 and num_files < world_size:
        raise ValueError(
            f"file-shard streaming assigns whole files by (file_index % world_size), so it needs at "
            f"least world_size={world_size} shard files, but only {num_files} matched the train pattern. "
            f"Ranks with no file would stall the DDP all-reduce. Provide more shards "
            f"(run_train.sh --auto-shard makes one per process) or use --streaming_reader line-stride."
        )
