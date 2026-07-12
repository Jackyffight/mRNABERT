#!/usr/bin/env python3
"""Compare frozen model representations with one shared ridge-regression probe."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
from pathlib import Path

from frozen_embedding_common import SPLITS


DEFAULT_ALPHAS = "1e-6,1e-5,1e-4,1e-3,1e-2,1e-1,1,10,100,1000"


def parse_model_spec(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("Model must be NAME=EMBEDDING_DIR")
    name, path = value.split("=", 1)
    if not name or not path:
        raise argparse.ArgumentTypeError("Model must be NAME=EMBEDDING_DIR")
    if re.fullmatch(r"[A-Za-z0-9._-]+", name) is None:
        raise argparse.ArgumentTypeError("Model NAME may contain only letters, numbers, dot, dash, underscore")
    return name, Path(path)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", action="append", required=True, type=parse_model_spec)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--alphas", default=DEFAULT_ALPHAS)
    parser.add_argument("--probe-dim", type=int, default=256)
    return parser.parse_args(argv)


def regression_metrics(labels, predictions) -> dict[str, float]:
    from scipy.stats import pearsonr, spearmanr
    from sklearn.metrics import mean_squared_error, r2_score

    return {
        "spearman": float(spearmanr(labels, predictions)[0]),
        "pearson": float(pearsonr(labels, predictions)[0]),
        "r2": float(r2_score(labels, predictions)),
        "mse": float(mean_squared_error(labels, predictions)),
    }


def load_embedding_dir(path: Path) -> dict:
    import numpy as np

    manifest_path = path / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Missing embedding manifest: {manifest_path}")
    result = {"manifest": json.loads(manifest_path.read_text(encoding="utf-8"))}
    for split in SPLITS:
        split_path = path / f"{split}.npz"
        if not split_path.is_file():
            raise FileNotFoundError(f"Missing embedding split: {split_path}")
        with np.load(split_path) as data:
            result[split] = {
                "embeddings": data["embeddings"].astype(np.float32),
                "labels": data["labels"].astype(np.float64),
                "sequence_sha256": data["sequence_sha256"].astype(str),
            }
        rows = result[split]["embeddings"].shape[0]
        if result[split]["embeddings"].ndim != 2:
            raise ValueError(f"Embeddings must be rank 2: {split_path}")
        if rows != result[split]["labels"].shape[0] or rows != result[split]["sequence_sha256"].shape[0]:
            raise ValueError(f"Embedding/label/hash row mismatch: {split_path}")
    return result


def validate_alignment(models: list[tuple[str, dict]]) -> None:
    import numpy as np

    reference_name, reference = models[0]
    for name, data in models[1:]:
        for split in SPLITS:
            if not np.array_equal(data[split]["sequence_sha256"], reference[split]["sequence_sha256"]):
                raise ValueError(f"Sequence alignment mismatch: {reference_name} vs {name}, split={split}")
            if not np.allclose(data[split]["labels"], reference[split]["labels"], rtol=0, atol=1e-7):
                raise ValueError(f"Label alignment mismatch: {reference_name} vs {name}, split={split}")


def atomic_write_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main(argv=None) -> None:
    args = parse_args(argv)

    import numpy as np
    from sklearn.decomposition import PCA
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler

    alphas = [float(value) for value in args.alphas.split(",")]
    if not alphas or any(not math.isfinite(value) or value <= 0 for value in alphas):
        raise SystemExit("--alphas must contain positive finite values")
    if args.probe_dim < 1:
        raise SystemExit("--probe-dim must be positive")

    loaded_models = [(name, load_embedding_dir(path)) for name, path in args.model]
    validate_alignment(loaded_models)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for name, data in loaded_models:
        train = data["train"]
        dev = data["dev"]
        test = data["test"]
        maximum_probe_dim = min(train["embeddings"].shape[0] - 1, train["embeddings"].shape[1])
        if args.probe_dim > maximum_probe_dim:
            raise ValueError(
                f"probe_dim={args.probe_dim} exceeds maximum {maximum_probe_dim} for {name}"
            )
        projector = PCA(n_components=args.probe_dim, svd_solver="randomized", random_state=42)
        train_features = projector.fit_transform(train["embeddings"])
        dev_features = projector.transform(dev["embeddings"])
        test_features = projector.transform(test["embeddings"])
        scaler = StandardScaler()
        train_features = scaler.fit_transform(train_features)
        dev_features = scaler.transform(dev_features)
        test_features = scaler.transform(test_features)
        candidates = []
        for alpha in alphas:
            probe = Ridge(alpha=alpha, solver="lsqr")
            probe.fit(train_features, train["labels"])
            predictions = probe.predict(dev_features)
            metrics = regression_metrics(dev["labels"], predictions)
            candidates.append({"alpha": alpha, **metrics})

        finite_candidates = [candidate for candidate in candidates if math.isfinite(candidate["spearman"])]
        if not finite_candidates:
            raise RuntimeError(f"Every dev Spearman is non-finite for: {name}")
        selected = max(
            finite_candidates,
            key=lambda candidate: (candidate["spearman"], -candidate["mse"], -candidate["alpha"]),
        )
        probe = Ridge(alpha=selected["alpha"], solver="lsqr")
        probe.fit(train_features, train["labels"])
        test_predictions = probe.predict(test_features)
        test_metrics = regression_metrics(test["labels"], test_predictions)

        prediction_path = args.output_dir / f"{name}-test-predictions.npz"
        with prediction_path.open("wb") as handle:
            np.savez_compressed(
                handle,
                labels=test["labels"],
                predictions=test_predictions,
                sequence_sha256=test["sequence_sha256"],
            )
        results.append(
            {
                "model": name,
                "embedding_dimensions": int(train["embeddings"].shape[1]),
                "probe_dimensions": args.probe_dim,
                "pca_explained_variance_ratio": float(projector.explained_variance_ratio_.sum()),
                "selected_alpha": selected["alpha"],
                "dev_metrics": {key: selected[key] for key in ("spearman", "pearson", "r2", "mse")},
                "test_metrics": test_metrics,
                "dev_candidates": candidates,
                "embedding_manifest": data["manifest"],
                "prediction_path": str(prediction_path),
            }
        )

    payload = {
        "version": 1,
        "protocol": "frozen-mean-pooled-l2-embedding-train-pca-ridge-v1",
        "selection": "Train-only PCA/scaling; Ridge alpha selected only by dev Spearman; test evaluated once",
        "interpretation": "Exploratory: this mRFP test split was inspected in earlier experiments",
        "alphas": alphas,
        "probe_dimensions": args.probe_dim,
        "split_counts": {
            split: int(loaded_models[0][1][split]["labels"].shape[0]) for split in SPLITS
        },
        "results": results,
    }
    atomic_write_json(args.output_dir / "results.json", payload)

    print("model\tembed_dim\tprobe_dim\talpha\tdev_spearman\ttest_spearman\ttest_pearson\ttest_r2\ttest_mse")
    for result in results:
        dev = result["dev_metrics"]
        test = result["test_metrics"]
        print(
            f'{result["model"]}\t{result["embedding_dimensions"]}\t{result["probe_dimensions"]}\t'
            f'{result["selected_alpha"]:g}\t'
            f'{dev["spearman"]:.6f}\t{test["spearman"]:.6f}\t{test["pearson"]:.6f}\t'
            f'{test["r2"]:.6f}\t{test["mse"]:.6f}'
        )
    print(f"results: {args.output_dir / 'results.json'}")


if __name__ == "__main__":
    main()
