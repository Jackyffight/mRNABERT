#!/usr/bin/env python3
"""Render the audited mRNABERT model-comparison evidence figure."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = REPO_ROOT / "docs" / "reports" / "model-comparison-data-20260716.json"
DEFAULT_OUTPUT = REPO_ROOT / "figures" / "model-comparison-20260716"

COLORS = {
    "internal-600k": "#157A6E",
    "public-mrnabert": "#365F91",
    "evo2-7b": "#B45F3C",
    "random-init": "#777777",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument(
        "--three-model-results",
        type=Path,
        help="Optional results.json from run_three_model_frozen_probe_nas.sh",
    )
    parser.add_argument(
        "--output-prefix",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output path without extension; both PNG and SVG are written",
    )
    parser.add_argument("--dpi", type=int, default=200)
    return parser.parse_args(argv)


def load_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def validate_evidence(data: dict) -> None:
    if data.get("schema") != "mrnabert.model-comparison-evidence.v1":
        raise ValueError("Unsupported evidence schema")
    expected_models = {"internal-600k", "public-mrnabert", "evo2-7b", "random-init"}
    models = {model["id"]: model for model in data.get("models", [])}
    if set(models) != expected_models:
        raise ValueError(f"Unexpected model IDs: {sorted(models)}")
    if models["internal-600k"].get("parameter_count") != 86493002:
        raise ValueError("Internal parameter count does not match the training record")
    if models["public-mrnabert"].get("parameter_count") != 113981258:
        raise ValueError("Public parameter count does not match the pinned checkpoint audit")

    sweep = data.get("mrfp", {}).get("full_finetune_lr_sweep", [])
    for model_id in ("internal-600k", "public-mrnabert"):
        rows = [row for row in sweep if row.get("model_id") == model_id]
        rates = {row.get("learning_rate") for row in rows}
        if rates != {2e-5, 5e-5, 1e-4}:
            raise ValueError(f"Incomplete learning-rate sweep for {model_id}: {rates}")
        for row in rows:
            mean = row["test"]["spearman_mean"]
            sd = row["test"]["spearman_sd"]
            if not (0 <= mean <= 1 and 0 <= sd <= 1):
                raise ValueError(f"Invalid Spearman summary for {model_id}")


def model_labels(data: dict) -> dict[str, str]:
    return {model["id"]: model["label"] for model in data["models"]}


def model_index(data: dict) -> dict[str, dict]:
    return {model["id"]: model for model in data["models"]}


def sweep_row(data: dict, model_id: str, learning_rate: float) -> dict:
    matches = [
        row
        for row in data["mrfp"]["full_finetune_lr_sweep"]
        if row["model_id"] == model_id
        and math.isclose(row["learning_rate"], learning_rate, rel_tol=0, abs_tol=1e-12)
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected one row for {model_id} at LR={learning_rate:g}")
    return matches[0]


def classify_three_model_name(name: str) -> str:
    lowered = name.lower()
    if "internal" in lowered and "600000" in lowered:
        return "internal-600k"
    if "public" in lowered or "yyly66" in lowered:
        return "public-mrnabert"
    if "evo2" in lowered or "evo-2" in lowered:
        return "evo2-7b"
    raise ValueError(f"Cannot map shared-probe model name: {name}")


def load_three_model_results(path: Path | None) -> dict[str, dict] | None:
    if path is None:
        return None
    payload = load_json(path)
    expected_protocol = "frozen-mean-pooled-l2-embedding-train-pca-ridge-v1"
    if payload.get("protocol") != expected_protocol:
        raise ValueError("Unexpected three-model probe protocol")
    expected_counts = {"train": 1018, "dev": 219, "test": 219}
    if payload.get("split_counts") != expected_counts:
        raise ValueError(f"Unexpected shared-probe split counts: {payload.get('split_counts')}")
    mapped = {}
    for result in payload.get("results", []):
        model_id = classify_three_model_name(result["model"])
        if model_id in mapped:
            raise ValueError(f"Duplicate shared-probe model: {model_id}")
        mapped[model_id] = result
    expected = {"internal-600k", "public-mrnabert", "evo2-7b"}
    if set(mapped) != expected:
        raise ValueError(f"Shared probe must contain exactly {sorted(expected)}")
    return mapped


def style_axis(axis) -> None:
    axis.grid(axis="y", color="#D8DDE3", linewidth=0.8, alpha=0.8)
    axis.set_axisbelow(True)
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    axis.spines["left"].set_color("#AAB2BC")
    axis.spines["bottom"].set_color("#AAB2BC")
    axis.tick_params(colors="#34404C", labelsize=9)


def add_bar_labels(axis, bars, values, errors=None, digits=3, offset=0.025) -> None:
    for index, (bar, value) in enumerate(zip(bars, values)):
        error = errors[index] if errors else 0
        axis.text(
            bar.get_x() + bar.get_width() / 2,
            value + error + offset,
            f"{value:.{digits}f}",
            ha="center",
            va="bottom",
            fontsize=9,
            fontweight="bold",
            color="#202830",
        )


def plot_full_finetune(axis, data: dict, labels: dict[str, str]) -> None:
    selected = data["mrfp"]["presentation_selection"]
    model_ids = ["internal-600k", "public-mrnabert"]
    rows = [sweep_row(data, model_id, selected[model_id]) for model_id in model_ids]
    values = [row["test"]["spearman_mean"] for row in rows]
    errors = [row["test"]["spearman_sd"] for row in rows]
    bars = axis.bar(
        range(len(model_ids)),
        values,
        yerr=errors,
        capsize=5,
        width=0.62,
        color=[COLORS[model_id] for model_id in model_ids],
        edgecolor="white",
        linewidth=1.2,
    )
    axis.set_xticks(range(len(model_ids)), [labels[model_id] for model_id in model_ids])
    axis.set_ylim(0, 1.0)
    axis.set_ylabel("Test Spearman (mean +/- SD)")
    axis.set_title("A. Competitive public mRFP quality", loc="left", fontweight="bold")
    add_bar_labels(axis, bars, values, errors)
    ratio = values[0] / values[1]
    axis.text(
        0.5,
        0.70,
        f"Internal retains {ratio:.1%} of public rank quality\nwith independently owned weights",
        transform=axis.transAxes,
        ha="center",
        va="center",
        fontsize=9,
        color="#34404C",
        bbox={"facecolor": "#F4F6F8", "edgecolor": "#CBD2D9", "pad": 5},
    )
    axis.text(
        0.02,
        0.04,
        "Random-init control: rank undefined, R2 = -12.85",
        transform=axis.transAxes,
        fontsize=8.5,
        color="#5B6570",
    )
    style_axis(axis)


def plot_lr_robustness(axis, data: dict, labels: dict[str, str]) -> None:
    learning_rates = [2e-5, 5e-5, 1e-4]
    for model_id, marker in (("internal-600k", "o"), ("public-mrnabert", "s")):
        rows = [sweep_row(data, model_id, learning_rate) for learning_rate in learning_rates]
        means = [row["test"]["spearman_mean"] for row in rows]
        errors = [row["test"]["spearman_sd"] for row in rows]
        axis.errorbar(
            learning_rates,
            means,
            yerr=errors,
            marker=marker,
            markersize=6,
            linewidth=2,
            capsize=4,
            color=COLORS[model_id],
            label=labels[model_id],
        )
        value_range = max(means) - min(means)
        axis.text(
            0.98,
            0.90 if model_id == "internal-600k" else 0.82,
            f"{labels[model_id]} range: {value_range:.3f}",
            transform=axis.transAxes,
            ha="right",
            fontsize=8.5,
            color=COLORS[model_id],
        )
    axis.set_xscale("log")
    axis.set_xticks(learning_rates, ["2e-5", "5e-5", "1e-4"])
    axis.tick_params(axis="x", which="minor", labelbottom=False)
    axis.set_ylim(0.1, 0.95)
    axis.set_xlabel("Full-fine-tuning learning rate")
    axis.set_ylabel("Test Spearman (mean +/- SD)")
    axis.set_title("B. Stronger optimization robustness", loc="left", fontweight="bold")
    axis.legend(loc="lower left", frameon=False, fontsize=9)
    axis.text(
        0.98,
        0.69,
        "Worst tested LR advantage: +0.313",
        transform=axis.transAxes,
        ha="right",
        fontsize=9,
        fontweight="bold",
        color=COLORS["internal-600k"],
    )
    style_axis(axis)


def plot_efficiency(axis, data: dict, labels: dict[str, str]) -> None:
    models = model_index(data)
    proxy = {row["model_id"]: row for row in data["proxy_mlm"]["results"]}
    model_ids = ["internal-600k", "public-mrnabert"]
    public_parameters = models["public-mrnabert"]["parameter_count"]
    public_time = data["proxy_mlm"]["evaluation_records"] / proxy["public-mrnabert"]["samples_per_second"]
    normalized = {
        "internal-600k": [
            models["internal-600k"]["parameter_count"] / public_parameters,
            (data["proxy_mlm"]["evaluation_records"] / proxy["internal-600k"]["samples_per_second"])
            / public_time,
        ],
        "public-mrnabert": [1.0, 1.0],
    }
    centers = [0, 1]
    width = 0.34
    for offset, model_id in ((-width / 2, "internal-600k"), (width / 2, "public-mrnabert")):
        bars = axis.bar(
            [center + offset for center in centers],
            normalized[model_id],
            width=width,
            color=COLORS[model_id],
            edgecolor="white",
            linewidth=1.0,
            label=labels[model_id],
        )
        add_bar_labels(axis, bars, normalized[model_id], digits=2, offset=0.025)
    axis.set_xticks(
        centers,
        ["Parameter count\n(lower is better)", "Evaluation time / 100k\n(lower is better)"],
    )
    axis.set_ylim(0, 1.18)
    axis.set_ylabel("Normalized to public mRNABERT = 1.0")
    axis.set_title("C. Lower model and compute cost", loc="left", fontweight="bold")
    axis.legend(loc="lower right", frameon=False, fontsize=9)
    parameter_reduction = 1 - normalized["internal-600k"][0]
    time_reduction = 1 - normalized["internal-600k"][1]
    axis.text(
        0.02,
        0.92,
        f"Internal: {parameter_reduction:.1%} fewer parameters; {time_reduction:.1%} less evaluation time",
        transform=axis.transAxes,
        fontsize=9,
        fontweight="bold",
        color=COLORS["internal-600k"],
    )
    axis.text(
        0.02,
        0.05,
        "Actual: 86.5M vs 114.0M parameters; 737s vs 802s per 100k samples",
        transform=axis.transAxes,
        fontsize=8.5,
        color="#5B6570",
    )
    style_axis(axis)


def plot_frozen_signal(axis, data: dict, labels: dict[str, str], shared_results) -> None:
    if shared_results is not None:
        model_ids = ["internal-600k", "public-mrnabert", "evo2-7b"]
        values = [shared_results[model_id]["test_metrics"]["spearman"] for model_id in model_ids]
        bars = axis.bar(
            range(len(model_ids)),
            values,
            width=0.62,
            color=[COLORS[model_id] for model_id in model_ids],
            edgecolor="white",
            linewidth=1.2,
        )
        axis.set_xticks(range(len(model_ids)), [labels[model_id] for model_id in model_ids])
        axis.set_title("D. Shared frozen PCA-256 Ridge probe", loc="left", fontweight="bold")
        add_bar_labels(axis, bars, values)
        note = "Same train-only PCA/scaling and Ridge protocol; alpha selected on dev"
    else:
        existing = {row["model_id"]: row for row in data["mrfp"]["frozen_encoder_head"]}
        model_ids = ["internal-600k", "public-mrnabert"]
        values = [existing[model_id]["test"]["spearman_mean"] for model_id in model_ids]
        errors = [existing[model_id]["test"]["spearman_sd"] for model_id in model_ids]
        bars = axis.bar(
            [0, 1],
            values,
            yerr=errors,
            capsize=5,
            width=0.62,
            color=[COLORS[model_id] for model_id in model_ids],
            edgecolor="white",
            linewidth=1.2,
        )
        axis.scatter([2], [0.5], marker="x", s=90, linewidth=2, color="#7A838C")
        axis.text(2, 0.43, "shared probe\npending", ha="center", va="top", fontsize=9, color="#5B6570")
        axis.set_xticks([0, 1, 2], [labels[model_id] for model_id in model_ids] + [labels["evo2-7b"]])
        axis.set_title("D. Frozen signal; Evo 2 result pending", loc="left", fontweight="bold")
        add_bar_labels(axis, bars, values, errors)
        note = "Current bars use the BERT-specific frozen-head probe; Evo requires the shared Ridge protocol"
    axis.set_ylim(0, 1.0)
    axis.set_ylabel("Test Spearman")
    axis.text(
        0.5,
        0.04,
        note,
        transform=axis.transAxes,
        ha="center",
        va="bottom",
        fontsize=8,
        color="#5B6570",
        wrap=True,
    )
    style_axis(axis)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.dpi < 72:
        raise SystemExit("--dpi must be at least 72")
    data = load_json(args.data)
    validate_evidence(data)
    shared_results = load_three_model_results(args.three_model_results)

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as error:
        raise SystemExit("matplotlib is required: python -m pip install matplotlib") from error

    labels = model_labels(data)
    figure, axes = plt.subplots(2, 2, figsize=(13.5, 9.2))
    figure.patch.set_facecolor("#FFFFFF")
    plot_full_finetune(axes[0, 0], data, labels)
    plot_lr_robustness(axes[0, 1], data, labels)
    plot_efficiency(axes[1, 0], data, labels)
    plot_frozen_signal(axes[1, 1], data, labels, shared_results)

    figure.suptitle(
        "Internal mRNABERT 600k: stable, effective, and compute-efficient",
        x=0.06,
        y=0.98,
        ha="left",
        fontsize=17,
        fontweight="bold",
        color="#202830",
    )
    figure.text(
        0.06,
        0.945,
        "Public mRFP benchmark; seeds 13/42/73. Error bars are sample SD. LR sweep is exploratory because test was inspected.",
        ha="left",
        fontsize=9.5,
        color="#5B6570",
    )
    figure.text(
        0.06,
        0.015,
        "Evidence snapshot 2026-07-16 | Data: docs/reports/model-comparison-data-20260716.json",
        ha="left",
        fontsize=8.5,
        color="#69737D",
    )
    figure.subplots_adjust(left=0.08, right=0.98, top=0.90, bottom=0.09, hspace=0.34, wspace=0.24)

    args.output_prefix.parent.mkdir(parents=True, exist_ok=True)
    png_path = args.output_prefix.with_suffix(".png")
    svg_path = args.output_prefix.with_suffix(".svg")
    figure.savefig(png_path, dpi=args.dpi, facecolor=figure.get_facecolor())
    figure.savefig(svg_path, facecolor=figure.get_facecolor())
    plt.close(figure)
    svg_text = svg_path.read_text(encoding="utf-8")
    svg_path.write_text(
        "\n".join(line.rstrip() for line in svg_text.splitlines()) + "\n",
        encoding="utf-8",
    )
    print(f"png: {png_path}")
    print(f"svg: {svg_path}")
    print(f"three_model_probe: {'loaded' if shared_results is not None else 'pending'}")


if __name__ == "__main__":
    main()
