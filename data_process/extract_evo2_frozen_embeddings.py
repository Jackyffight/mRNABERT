#!/usr/bin/env python3
"""Extract resumable mean-pooled Evo 2 embeddings for regression splits."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from frozen_embedding_common import SPLITS, load_regression_records


DEFAULT_LAYER = "blocks.28.mlp.l3"


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-name", default="evo2_7b")
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--layer", default=DEFAULT_LAYER)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--checkpoint-every", type=int, default=25)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args(argv)


def atomic_save_npz(path: Path, **arrays) -> None:
    import numpy as np

    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("wb") as handle:
        np.savez_compressed(handle, **arrays)
    os.replace(temporary, path)


def pool_hook_output(output, sequence_length: int):
    import torch
    import torch.nn.functional as functional

    if output.ndim == 2:
        token_embeddings = output
    elif output.ndim == 3 and output.shape[0] == 1:
        token_embeddings = output[0]
    elif output.ndim == 3 and output.shape[1] == 1:
        token_embeddings = output[:, 0]
    else:
        raise RuntimeError(f"Unexpected Evo 2 hook output shape: {tuple(output.shape)}")
    if token_embeddings.shape[0] != sequence_length:
        raise RuntimeError(
            f"Evo 2 embedding length {token_embeddings.shape[0]} != input length {sequence_length}"
        )
    pooled = token_embeddings.float().mean(dim=0, keepdim=True)
    return functional.normalize(pooled, p=2, dim=1).squeeze(0)


def main(argv=None) -> None:
    args = parse_args(argv)

    import numpy as np
    import torch
    from evo2 import Evo2

    if not args.model_path.is_file():
        raise SystemExit(f"Evo 2 checkpoint not found: {args.model_path}")
    if not torch.cuda.is_available():
        raise SystemExit("Evo 2 7B extraction requires CUDA")
    if args.checkpoint_every < 1:
        raise SystemExit("--checkpoint-every must be positive")

    model = Evo2(args.model_name, local_path=str(args.model_path), use_kernels=False)
    model.model.eval()
    device = next(model.model.parameters()).device
    if device.type != "cuda":
        raise RuntimeError(f"Evo 2 model loaded on {device}; CUDA placement is required")
    started = time.monotonic()
    split_counts = {}

    for split in SPLITS:
        output_path = args.output_dir / f"{split}.npz"
        partial_path = args.output_dir / f".{split}.partial.npz"
        if args.overwrite:
            output_path.unlink(missing_ok=True)
            partial_path.unlink(missing_ok=True)
        if output_path.exists():
            print(f"reuse {output_path}", flush=True)
            with np.load(output_path) as existing:
                split_counts[split] = int(existing["labels"].shape[0])
            continue

        records = load_regression_records(args.data_dir / f"{split}.csv")
        expected_hashes = np.asarray([record.sequence_sha256 for record in records])
        rows = []
        start_index = 0
        if partial_path.exists():
            with np.load(partial_path) as partial:
                partial_hashes = partial["sequence_sha256"]
                if not np.array_equal(partial_hashes, expected_hashes[: len(partial_hashes)]):
                    raise RuntimeError(f"Stale partial extraction does not match {split}: {partial_path}")
                rows.extend(partial["embeddings"])
                start_index = len(partial_hashes)
            print(f"resume {split} from record {start_index}", flush=True)

        for index in range(start_index, len(records)):
            sequence = records[index].normalized_sequence
            input_ids = torch.tensor(
                model.tokenizer.tokenize(sequence),
                dtype=torch.int,
                device=device,
            ).unsqueeze(0)
            with torch.inference_mode():
                _, embedding_outputs = model(
                    input_ids,
                    return_embeddings=True,
                    layer_names=[args.layer],
                )
            pooled = pool_hook_output(embedding_outputs[args.layer], input_ids.shape[1])
            rows.append(pooled.cpu().numpy())

            completed = index + 1
            print(
                f"evo2_embedding_progress split={split} records={completed}/{len(records)}",
                flush=True,
            )
            if completed % args.checkpoint_every == 0 and completed < len(records):
                atomic_save_npz(
                    partial_path,
                    embeddings=np.asarray(rows, dtype=np.float32),
                    labels=np.asarray([record.label for record in records[:completed]], dtype=np.float32),
                    sequence_sha256=expected_hashes[:completed],
                )

        atomic_save_npz(
            output_path,
            embeddings=np.asarray(rows, dtype=np.float32),
            labels=np.asarray([record.label for record in records], dtype=np.float32),
            sequence_sha256=expected_hashes,
        )
        partial_path.unlink(missing_ok=True)
        split_counts[split] = len(records)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": 1,
        "model_name": args.model_name,
        "model_revision": args.model_revision,
        "model_path": str(args.model_path),
        "encoder_family": "evo2",
        "embedding_layer": args.layer,
        "pooling": "token-mean-l2",
        "split_counts": split_counts,
        "elapsed_seconds": time.monotonic() - started,
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
