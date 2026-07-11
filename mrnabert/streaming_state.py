"""Persistent lineage for resumable local streaming pretraining."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


STATE_FILENAME = "streaming_state.json"
STATE_VERSION = 1


@dataclass(frozen=True)
class StreamingCheckpointState:
    version: int
    global_step: int
    next_sample_cursor: int
    effective_batch_size: int
    resume_global_step: int
    resume_sample_cursor: int
    samples_consumed_since_resume: int
    resume_cursor_source: str
    streaming_reader: str
    shuffle_buffer: int
    shuffle_seed: int
    world_size: int
    dataloader_num_workers: int
    shard_manifest_path: Optional[str]
    shard_manifest_sha256: Optional[str]
    corpus_samples: Optional[int]
    corpus_pass: Optional[int]
    corpus_offset: Optional[int]
    created_at_utc: str


@dataclass(frozen=True)
class ResolvedResumeState:
    global_step: int
    next_sample_cursor: int
    source: str
    checkpoint_state: Optional[StreamingCheckpointState]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def shard_manifest_sha256(path: Path) -> str:
    """Hash corpus identity while ignoring run-specific sharding timings."""
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    for key in ("elapsed_seconds", "average_rate_bytes_per_second", "created_at_utc"):
        payload.pop(key, None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def load_corpus_samples(manifest_path: Optional[Path]) -> Optional[int]:
    if manifest_path is None or not manifest_path.exists():
        return None
    with manifest_path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    total_lines = manifest.get("total_lines")
    return int(total_lines) if total_lines is not None else None


def effective_batch_size(per_device_batch_size: int, gradient_accumulation_steps: int, world_size: int) -> int:
    values = (per_device_batch_size, gradient_accumulation_steps, world_size)
    if any(value < 1 for value in values):
        raise ValueError("batch size, gradient accumulation, and world size must all be >= 1")
    return per_device_batch_size * gradient_accumulation_steps * world_size


def next_sample_cursor(
    resume_sample_cursor: int,
    resume_global_step: int,
    current_global_step: int,
    batch_size: int,
) -> int:
    if resume_sample_cursor < 0 or resume_global_step < 0 or current_global_step < 0:
        raise ValueError("streaming cursor and global steps must be >= 0")
    if current_global_step < resume_global_step:
        raise ValueError(
            f"current_global_step={current_global_step} is before resume_global_step={resume_global_step}"
        )
    if batch_size < 1:
        raise ValueError("effective batch size must be >= 1")
    return resume_sample_cursor + ((current_global_step - resume_global_step) * batch_size)


def build_checkpoint_state(
    *,
    global_step: int,
    resume_global_step: int,
    resume_sample_cursor: int,
    effective_batch: int,
    streaming_reader: str,
    shuffle_seed: int,
    shuffle_buffer: int = 0,
    world_size: int = 1,
    dataloader_num_workers: int = 0,
    shard_manifest_path: Optional[str] = None,
    resume_cursor_source: str = "fresh",
) -> StreamingCheckpointState:
    manifest = Path(shard_manifest_path).resolve() if shard_manifest_path else None
    manifest_hash = shard_manifest_sha256(manifest) if manifest is not None and manifest.exists() else None
    corpus_samples = load_corpus_samples(manifest)
    cursor = next_sample_cursor(
        resume_sample_cursor,
        resume_global_step,
        global_step,
        effective_batch,
    )
    corpus_pass = cursor // corpus_samples if corpus_samples else None
    corpus_offset = cursor % corpus_samples if corpus_samples else None
    return StreamingCheckpointState(
        version=STATE_VERSION,
        global_step=global_step,
        next_sample_cursor=cursor,
        effective_batch_size=effective_batch,
        resume_global_step=resume_global_step,
        resume_sample_cursor=resume_sample_cursor,
        samples_consumed_since_resume=(global_step - resume_global_step) * effective_batch,
        resume_cursor_source=resume_cursor_source,
        streaming_reader=streaming_reader,
        shuffle_buffer=shuffle_buffer,
        shuffle_seed=shuffle_seed,
        world_size=world_size,
        dataloader_num_workers=dataloader_num_workers,
        shard_manifest_path=str(manifest) if manifest is not None else None,
        shard_manifest_sha256=manifest_hash,
        corpus_samples=corpus_samples,
        corpus_pass=corpus_pass,
        corpus_offset=corpus_offset,
        created_at_utc=datetime.now(timezone.utc).isoformat(),
    )


def write_checkpoint_state(directory: Path, state: StreamingCheckpointState) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    output_path = directory / STATE_FILENAME
    temporary_path = directory / f".{STATE_FILENAME}.tmp.{os.getpid()}"
    with temporary_path.open("w", encoding="utf-8") as handle:
        json.dump(asdict(state), handle, indent=2, sort_keys=True)
        handle.write("\n")
    temporary_path.replace(output_path)
    return output_path


def load_checkpoint_state(checkpoint: Path) -> Optional[StreamingCheckpointState]:
    path = checkpoint / STATE_FILENAME
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("version") != STATE_VERSION:
        raise ValueError(f"Unsupported streaming state version in {path}: {payload.get('version')}")
    return StreamingCheckpointState(**payload)


def load_trainer_global_step(checkpoint: Path) -> int:
    trainer_state_path = checkpoint / "trainer_state.json"
    if trainer_state_path.exists():
        with trainer_state_path.open("r", encoding="utf-8") as handle:
            return int(json.load(handle).get("global_step") or 0)
    prefix = "checkpoint-"
    if checkpoint.name.startswith(prefix) and checkpoint.name[len(prefix) :].isdigit():
        return int(checkpoint.name[len(prefix) :])
    raise ValueError(f"Cannot determine global step from checkpoint: {checkpoint}")


def resolve_resume_state(
    *,
    checkpoint: Path,
    fallback_effective_batch: int,
    override_sample_cursor: Optional[int] = None,
    current_shard_manifest_path: Optional[str] = None,
    current_streaming_reader: Optional[str] = None,
    current_shuffle_buffer: Optional[int] = None,
    current_shuffle_seed: Optional[int] = None,
    current_world_size: Optional[int] = None,
    current_dataloader_num_workers: Optional[int] = None,
) -> ResolvedResumeState:
    global_step = load_trainer_global_step(checkpoint)
    checkpoint_state = load_checkpoint_state(checkpoint)

    if override_sample_cursor is not None:
        if override_sample_cursor < 0:
            raise ValueError("override sample cursor must be >= 0")
        return ResolvedResumeState(global_step, override_sample_cursor, "explicit-override", checkpoint_state)

    if checkpoint_state is not None:
        if checkpoint_state.global_step != global_step:
            raise ValueError(
                f"Checkpoint global step mismatch: trainer={global_step}, "
                f"streaming={checkpoint_state.global_step}"
            )
        if current_shard_manifest_path and checkpoint_state.shard_manifest_sha256:
            current_manifest = Path(current_shard_manifest_path).resolve()
            current_hash = shard_manifest_sha256(current_manifest)
            if current_hash != checkpoint_state.shard_manifest_sha256:
                raise ValueError(
                    "Streaming shard manifest changed since checkpoint: "
                    f"checkpoint={checkpoint_state.shard_manifest_sha256}, current={current_hash}"
                )
        topology = {
            "streaming_reader": current_streaming_reader,
            "shuffle_buffer": current_shuffle_buffer,
            "shuffle_seed": current_shuffle_seed,
            "world_size": current_world_size,
            "dataloader_num_workers": current_dataloader_num_workers,
        }
        mismatches = []
        for field_name, current_value in topology.items():
            if current_value is None:
                continue
            checkpoint_value = getattr(checkpoint_state, field_name)
            if checkpoint_value != current_value:
                mismatches.append(f"{field_name}: checkpoint={checkpoint_value}, current={current_value}")
        if mismatches:
            raise ValueError("Streaming topology changed since checkpoint: " + "; ".join(mismatches))
        return ResolvedResumeState(
            global_step,
            checkpoint_state.next_sample_cursor,
            "checkpoint-streaming-state",
            checkpoint_state,
        )

    return ResolvedResumeState(
        global_step,
        global_step * fallback_effective_batch,
        "legacy-global-step-fallback",
        None,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve = subparsers.add_parser("resolve", help="Resolve the cursor for a resume checkpoint.")
    resolve.add_argument("--checkpoint", type=Path, required=True)
    resolve.add_argument("--effective-batch", type=int, required=True)
    resolve.add_argument("--override-sample-cursor", type=int)
    resolve.add_argument("--shard-manifest")
    resolve.add_argument("--streaming-reader")
    resolve.add_argument("--shuffle-buffer", type=int)
    resolve.add_argument("--shuffle-seed", type=int)
    resolve.add_argument("--world-size", type=int)
    resolve.add_argument("--dataloader-num-workers", type=int)

    bootstrap = subparsers.add_parser("bootstrap", help="Write state for a legacy checkpoint.")
    bootstrap.add_argument("--checkpoint", type=Path, required=True)
    bootstrap.add_argument("--next-sample-cursor", type=int, required=True)
    bootstrap.add_argument("--effective-batch", type=int, required=True)
    bootstrap.add_argument("--streaming-reader", default="file-shard")
    bootstrap.add_argument("--shuffle-buffer", type=int, default=20000)
    bootstrap.add_argument("--shuffle-seed", type=int, default=42)
    bootstrap.add_argument("--world-size", type=int, default=3)
    bootstrap.add_argument("--dataloader-num-workers", type=int, default=4)
    bootstrap.add_argument("--shard-manifest")
    return parser


def main(argv=None) -> None:
    args = _build_parser().parse_args(argv)
    if args.command == "resolve":
        resolved = resolve_resume_state(
            checkpoint=args.checkpoint,
            fallback_effective_batch=args.effective_batch,
            override_sample_cursor=args.override_sample_cursor,
            current_shard_manifest_path=args.shard_manifest,
            current_streaming_reader=args.streaming_reader,
            current_shuffle_buffer=args.shuffle_buffer,
            current_shuffle_seed=args.shuffle_seed,
            current_world_size=args.world_size,
            current_dataloader_num_workers=args.dataloader_num_workers,
        )
        print(f"{resolved.global_step}\t{resolved.next_sample_cursor}\t{resolved.source}")
        return

    global_step = load_trainer_global_step(args.checkpoint)
    state = build_checkpoint_state(
        global_step=global_step,
        resume_global_step=global_step,
        resume_sample_cursor=args.next_sample_cursor,
        effective_batch=args.effective_batch,
        streaming_reader=args.streaming_reader,
        shuffle_buffer=args.shuffle_buffer,
        shuffle_seed=args.shuffle_seed,
        world_size=args.world_size,
        dataloader_num_workers=args.dataloader_num_workers,
        shard_manifest_path=args.shard_manifest,
        resume_cursor_source="legacy-bootstrap",
    )
    path = write_checkpoint_state(args.checkpoint, state)
    print(path)


if __name__ == "__main__":
    main()
