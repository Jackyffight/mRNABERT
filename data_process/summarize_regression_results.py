"""Summarize multi-seed regression metrics emitted by regression.py."""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from pathlib import Path


METRICS = (
    "eval_spearman_corr",
    "eval_pearson_corr",
    "eval_r2_score",
    "eval_mse_loss",
)
SUMMARY_METRICS = ("dev_best_spearman",) + METRICS
RUN_PATTERN = re.compile(r"^(?P<model>.+)-seed(?P<seed>\d+)$")
LEGACY_MODEL_PREFIXES = (
    "internal-checkpoint-",
    "public-YYLY66-",
    "random-init-internal-architecture",
)


def normalize_model_name(model: str) -> str:
    if model.startswith(LEGACY_MODEL_PREFIXES) and not re.search(r"-(?:full|frozen)-lr", model):
        return f"{model}-full-lr1e-4"
    return model


def load_best_dev_spearman(run_root: Path) -> float:
    latest_step = -1
    best_metric = math.nan
    for path in run_root.glob("checkpoint-*/trainer_state.json"):
        with path.open("r", encoding="utf-8") as handle:
            state = json.load(handle)
        global_step = int(state.get("global_step") or 0)
        value = state.get("best_metric")
        if global_step >= latest_step and value is not None:
            latest_step = global_step
            best_metric = float(value)
    return best_metric


def load_results(root: Path) -> list[dict]:
    rows = []
    for path in sorted(root.glob("*/results/*/eval_results.json")):
        run_dir = path.relative_to(root).parts[0]
        match = RUN_PATTERN.fullmatch(run_dir)
        if match is None:
            continue
        with path.open("r", encoding="utf-8") as handle:
            metrics = json.load(handle)
        missing = [metric for metric in METRICS if metric not in metrics]
        if missing:
            raise ValueError(f"Missing metrics in {path}: {', '.join(missing)}")
        rows.append(
            {
                "model": normalize_model_name(match.group("model")),
                "seed": int(match.group("seed")),
                "dev_best_spearman": load_best_dev_spearman(root / run_dir),
                **{metric: float(metrics[metric]) for metric in METRICS},
            }
        )
    return rows


def summarize(rows: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["model"], []).append(row)

    summaries = []
    for model, model_rows in sorted(grouped.items()):
        summary = {"model": model, "seeds": len(model_rows)}
        for metric in SUMMARY_METRICS:
            values = [row[metric] for row in model_rows]
            finite_values = [value for value in values if math.isfinite(value)]
            summary[f"{metric}_valid"] = len(finite_values)
            if finite_values:
                summary[f"{metric}_mean"] = statistics.fmean(finite_values)
                summary[f"{metric}_std"] = (
                    statistics.stdev(finite_values) if len(finite_values) > 1 else 0.0
                )
            else:
                summary[f"{metric}_mean"] = math.nan
                summary[f"{metric}_std"] = math.nan
        summaries.append(summary)
    return summaries


def _format(value: float) -> str:
    return f"{value:.6f}"


def print_report(rows: list[dict], summaries: list[dict]) -> None:
    print("per_seed")
    print("model\tseed\tdev_best_spearman\ttest_spearman\ttest_pearson\ttest_r2\ttest_mse")
    for row in rows:
        print(
            "\t".join(
                (
                    row["model"],
                    str(row["seed"]),
                    _format(row["dev_best_spearman"]),
                    _format(row["eval_spearman_corr"]),
                    _format(row["eval_pearson_corr"]),
                    _format(row["eval_r2_score"]),
                    _format(row["eval_mse_loss"]),
                )
            )
        )

    print("\naggregate")
    print(
        "model\tseeds\t"
        "dev_spearman_valid\tdev_spearman_mean\tdev_spearman_std\t"
        "spearman_valid\tspearman_mean\tspearman_std\t"
        "pearson_valid\tpearson_mean\tpearson_std\t"
        "r2_valid\tr2_mean\tr2_std\t"
        "mse_valid\tmse_mean\tmse_std"
    )
    for row in summaries:
        print(
            "\t".join(
                (
                    row["model"],
                    str(row["seeds"]),
                    f'{row["dev_best_spearman_valid"]}/{row["seeds"]}',
                    _format(row["dev_best_spearman_mean"]),
                    _format(row["dev_best_spearman_std"]),
                    f'{row["eval_spearman_corr_valid"]}/{row["seeds"]}',
                    _format(row["eval_spearman_corr_mean"]),
                    _format(row["eval_spearman_corr_std"]),
                    f'{row["eval_pearson_corr_valid"]}/{row["seeds"]}',
                    _format(row["eval_pearson_corr_mean"]),
                    _format(row["eval_pearson_corr_std"]),
                    f'{row["eval_r2_score_valid"]}/{row["seeds"]}',
                    _format(row["eval_r2_score_mean"]),
                    _format(row["eval_r2_score_std"]),
                    f'{row["eval_mse_loss_valid"]}/{row["seeds"]}',
                    _format(row["eval_mse_loss_mean"]),
                    _format(row["eval_mse_loss_std"]),
                )
            )
        )


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_root", type=Path)
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    rows = load_results(args.result_root)
    if not rows:
        raise SystemExit(f"No multi-seed eval results found under: {args.result_root}")
    print_report(rows, summarize(rows))


if __name__ == "__main__":
    main()
