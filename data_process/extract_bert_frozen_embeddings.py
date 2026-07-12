#!/usr/bin/env python3
"""Extract normalized mean-pooled embeddings from a frozen BERT-family encoder."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from frozen_embedding_common import SPLITS, load_regression_records


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-length", type=int, default=250)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args(argv)


def atomic_save_npz(path: Path, **arrays) -> None:
    import numpy as np

    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("wb") as handle:
        np.savez_compressed(handle, **arrays)
    os.replace(temporary, path)


def main(argv=None) -> None:
    args = parse_args(argv)

    import numpy as np
    import torch
    import torch.nn.functional as functional
    from transformers import AutoConfig, AutoModel, AutoTokenizer

    if not args.model_path.is_dir():
        raise SystemExit(f"Model directory not found: {args.model_path}")
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be positive")
    if not torch.cuda.is_available() and args.device.startswith("cuda"):
        raise SystemExit(f"CUDA is unavailable, cannot use --device {args.device}")

    config = AutoConfig.from_pretrained(args.model_path, trust_remote_code=False)
    auto_map = getattr(config, "auto_map", None) or {}
    if auto_map and getattr(config, "attention_probs_dropout_prob", None) == 0:
        # The public remote implementation uses this switch to avoid its legacy Triton path.
        config.attention_probs_dropout_prob = 1e-12

    tokenizer = AutoTokenizer.from_pretrained(
        args.model_path,
        model_max_length=args.max_length,
        padding_side="right",
        use_fast=True,
        trust_remote_code=bool(auto_map),
    )
    model = AutoModel.from_pretrained(
        args.model_path,
        config=config,
        trust_remote_code=bool(auto_map),
    )
    device = torch.device(args.device)
    model.to(device)
    model.eval()

    started = time.monotonic()
    split_counts = {}
    for split in SPLITS:
        output_path = args.output_dir / f"{split}.npz"
        if output_path.exists() and not args.overwrite:
            print(f"reuse {output_path}", flush=True)
            with np.load(output_path) as existing:
                split_counts[split] = int(existing["labels"].shape[0])
            continue

        records = load_regression_records(args.data_dir / f"{split}.csv")
        embeddings = []
        for start in range(0, len(records), args.batch_size):
            batch = records[start : start + args.batch_size]
            encoded = tokenizer(
                [record.sequence for record in batch],
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=args.max_length,
            )
            encoded = {key: value.to(device) for key, value in encoded.items()}
            with torch.inference_mode(), torch.autocast(
                device_type=device.type,
                dtype=torch.bfloat16,
                enabled=device.type == "cuda",
            ):
                hidden = model(**encoded, return_dict=True).last_hidden_state

            valid = encoded["attention_mask"].bool()
            for token_id in tokenizer.all_special_ids:
                valid &= encoded["input_ids"].ne(token_id)
            if not torch.all(valid.any(dim=1)):
                raise RuntimeError(f"A sequence in {split} has no non-special tokens")
            weights = valid.unsqueeze(-1).to(hidden.dtype)
            pooled = (hidden * weights).sum(dim=1) / weights.sum(dim=1)
            pooled = functional.normalize(pooled.float(), p=2, dim=1)
            embeddings.append(pooled.cpu().numpy())
            print(
                f"bert_embedding_progress model={args.model_name} split={split} "
                f"records={min(start + len(batch), len(records))}/{len(records)}",
                flush=True,
            )

        matrix = np.concatenate(embeddings, axis=0)
        atomic_save_npz(
            output_path,
            embeddings=matrix,
            labels=np.asarray([record.label for record in records], dtype=np.float32),
            sequence_sha256=np.asarray([record.sequence_sha256 for record in records]),
        )
        split_counts[split] = len(records)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": 1,
        "model_name": args.model_name,
        "model_path": str(args.model_path),
        "encoder_family": "bert",
        "pooling": "non-special-token-mean-l2",
        "max_length": args.max_length,
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
