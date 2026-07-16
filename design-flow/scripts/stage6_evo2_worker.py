#!/usr/bin/env python3
"""Run a checksum-bound Stage 6 Evo 2 scoring job on one GPU."""

from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time


DESIGN_FLOW_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESIGN_FLOW_ROOT / "src"))

from design_flow.evo2_adapter import (  # noqa: E402
    EVO2_MODEL_SHA256,
    EVO2_MODEL_SIZE,
    EVO2_PACKAGE_VERSION,
    load_evo2_job_archive,
    load_evo2_result_archive,
    write_evo2_result_archive,
)


PARTIAL_SCHEMA = "vaxflow.evo2-sequence-score-partial.v1"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-archive", type=Path, required=True)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--use-kernels", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args(argv)


def sha256_large(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_partial(path: Path, job: dict) -> list[dict]:
    if not path.is_file():
        return []
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot load Evo 2 partial result {path}: {error}") from error
    scores = document.get("scores") if isinstance(document, dict) else None
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != PARTIAL_SCHEMA
        or document.get("job_identity") != job["job_identity"]
        or not isinstance(scores, list)
        or len(scores) > len(job["records"])
    ):
        raise RuntimeError(f"Stale or invalid Evo 2 partial result: {path}")
    for score, record in zip(scores, job["records"]):
        if (
            not isinstance(score, dict)
            or score.get("design_id") != record["design_id"]
            or score.get("coding_sequence_sha256") != record["coding_sequence_sha256"]
        ):
            raise RuntimeError(f"Evo 2 partial result order differs from the job: {path}")
    return scores


def score_sequence(model, sequence: str, device, torch) -> tuple[dict, str]:
    token_ids = model.tokenizer.tokenize(sequence)
    if len(token_ids) != len(sequence) or len(token_ids) < 2:
        raise RuntimeError(
            f"Evo 2 tokenizer produced {len(token_ids)} tokens for {len(sequence)} nucleotides"
        )
    input_ids = torch.tensor(token_ids, dtype=torch.int, device=device).unsqueeze(0)
    with torch.inference_mode():
        outputs, _ = model(input_ids)
    logits = outputs[0]
    if logits.ndim != 3 or logits.shape[0] != 1:
        raise RuntimeError(f"Unexpected Evo 2 logits shape: {tuple(logits.shape)}")
    if logits.shape[1] == input_ids.shape[1]:
        token_logits = logits
    elif logits.shape[2] == input_ids.shape[1]:
        token_logits = logits.transpose(1, 2)
    else:
        raise RuntimeError(
            f"Evo 2 logits length does not match input: {tuple(logits.shape)}"
        )
    scoring_dtype = str(token_logits.dtype).replace("torch.", "")
    targets = input_ids[:, 1:].long()
    log_probabilities = torch.log_softmax(token_logits[:, :-1, :].float(), dim=-1)
    selected = log_probabilities.gather(-1, targets.unsqueeze(-1)).squeeze(-1)
    total = float(selected.sum().item())
    count = int(selected.numel())
    mean = total / count
    perplexity = math.exp(-mean)
    if not all(math.isfinite(value) for value in (total, mean, perplexity)):
        raise RuntimeError("Evo 2 produced a non-finite likelihood")
    return (
        {
            "sequence_length_nt": len(sequence),
            "predicted_token_count": count,
            "total_log_likelihood": total,
            "mean_log_likelihood": mean,
            "perplexity": perplexity,
        },
        scoring_dtype,
    )


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    job, fasta = load_evo2_job_archive(args.job_archive)
    args.output_root.mkdir(parents=True, exist_ok=True)

    existing_archives = sorted(
        args.output_root.glob(f"{job['job_identity']}-result-*.tar.gz")
    )
    if existing_archives and not args.overwrite:
        if len(existing_archives) != 1:
            raise SystemExit(
                f"Multiple Evo 2 result archives already exist for this job: {args.output_root}"
            )
        loaded_job, _, evidence, run_manifest, _ = load_evo2_result_archive(
            existing_archives[0]
        )
        if loaded_job["job_identity"] != job["job_identity"]:
            raise SystemExit("Existing Evo 2 result belongs to another job")
        print(
            f"reuse result={run_manifest['result_identity']} "
            f"observations={len(evidence['observations'])}",
            flush=True,
        )
        print(f"Result archive: {existing_archives[0]}", flush=True)
        return

    model_path = args.model_path.expanduser().resolve()
    if not model_path.is_file() or model_path.stat().st_size != EVO2_MODEL_SIZE:
        raise SystemExit(f"Pinned Evo 2 checkpoint is missing or has the wrong size: {model_path}")
    print(f"Verifying Evo 2 checkpoint SHA256: {model_path}", flush=True)
    checkpoint_sha256 = sha256_large(model_path)
    if checkpoint_sha256 != EVO2_MODEL_SHA256:
        raise SystemExit(
            f"Evo 2 checkpoint SHA256 mismatch: {checkpoint_sha256} != {EVO2_MODEL_SHA256}"
        )
    installed_version = version("evo2")
    if installed_version != EVO2_PACKAGE_VERSION:
        raise SystemExit(
            f"Evo 2 package version mismatch: {installed_version} != {EVO2_PACKAGE_VERSION}"
        )

    import torch
    from evo2 import Evo2

    if not torch.cuda.is_available() or not args.device.startswith("cuda"):
        raise SystemExit("Stage 6 Evo 2 scoring requires a CUDA GPU")
    requested_device = torch.device(args.device)
    torch.cuda.set_device(requested_device)
    started = time.monotonic()
    print(
        f"Loading {job['model']['name']} records={len(job['records'])} "
        f"use_kernels={args.use_kernels}",
        flush=True,
    )
    model = Evo2(
        job["model"]["name"],
        local_path=str(model_path),
        use_kernels=args.use_kernels,
    )
    model.model.eval()
    device = next(model.model.parameters()).device
    if device.type != "cuda":
        raise RuntimeError(f"Evo 2 loaded on {device}; CUDA placement is required")

    partial_path = args.output_root / f".{job['job_identity']}.scores.partial.json"
    if args.overwrite:
        partial_path.unlink(missing_ok=True)
    scores = load_partial(partial_path, job)
    if scores:
        print(f"resume scored={len(scores)}/{len(job['records'])}", flush=True)
    scoring_dtypes: set[str] = set()
    for index in range(len(scores), len(job["records"])):
        record = job["records"][index]
        metrics, scoring_dtype = score_sequence(
            model, record["coding_sequence_dna"], device, torch
        )
        scoring_dtypes.add(scoring_dtype)
        scores.append(
            {
                "design_id": record["design_id"],
                "coding_sequence_sha256": record["coding_sequence_sha256"],
                **metrics,
            }
        )
        atomic_json(
            partial_path,
            {
                "schema_version": PARTIAL_SCHEMA,
                "job_identity": job["job_identity"],
                "scores": scores,
            },
        )
        print(
            f"evo2_score_progress records={index + 1}/{len(job['records'])} "
            f"design={record['design_id']} "
            f"mean_log_likelihood={metrics['mean_log_likelihood']:.6f}",
            flush=True,
        )

    torch.cuda.synchronize(device)
    execution = {
        "device": str(device),
        "gpu_name": torch.cuda.get_device_name(device),
        "torch_version": torch.__version__,
        "evo2_package_version": installed_version,
        "use_kernels": args.use_kernels,
        "checkpoint_sha256": checkpoint_sha256,
        "scoring_dtype": ",".join(sorted(scoring_dtypes)) or "resumed",
        "elapsed_seconds": round(time.monotonic() - started, 6),
    }
    result = write_evo2_result_archive(
        job,
        fasta,
        scores,
        tool_version=installed_version,
        output_root=args.output_root,
        execution=execution,
    )
    partial_path.unlink(missing_ok=True)
    print(
        f"Evo 2 Stage 6 scoring finished: records={result['records']} "
        f"result={result['result_identity']}",
        flush=True,
    )
    print(f"Result directory: {result['result_dir']}", flush=True)
    print(f"Result archive: {result['archive']}", flush=True)
    print(f"Result SHA256: {result['archive_sha256']}", flush=True)


if __name__ == "__main__":
    main()
