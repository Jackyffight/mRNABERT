#!/usr/bin/env python3
"""Render the audited VaxFlow Stage 1-7 closed-loop evidence figure."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


DESIGN_FLOW_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = DESIGN_FLOW_ROOT / "docs" / "reports" / "stage-closed-loop-data-20260716.json"
DEFAULT_OUTPUT = DESIGN_FLOW_ROOT / "figures" / "stage-closed-loop-20260716"

COLORS = {
    "ink": "#202830",
    "muted": "#5B6570",
    "line": "#CBD2D9",
    "panel": "#F5F7F9",
    "executed": "#157A6E",
    "evaluated": "#365F91",
    "needs_data": "#D08C2F",
    "pending": "#8A939D",
    "priority": "#157A6E",
    "rescue": "#4C78A8",
    "archive": "#B9C0C8",
    "accent": "#B45F3C",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
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


def _require_sha256(value: object, field: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")


def validate_evidence(data: dict) -> None:
    if data.get("schema") != "vaxflow.stage-closed-loop-evidence.v1":
        raise ValueError("Unsupported evidence schema")

    project = data.get("project", {})
    if project.get("id") != "three-protein-vaccine":
        raise ValueError("Unexpected project evidence")
    if len(project.get("source_proteins", [])) != 3:
        raise ValueError("Expected exactly three source proteins")

    workflow = data.get("workflow", {})
    if workflow.get("version") != 2 or workflow.get("architecture_version") != 2:
        raise ValueError("Expected workflow and architecture version 2")
    _require_sha256(workflow.get("contract_sha256"), "workflow.contract_sha256")

    execution = data.get("execution", {})
    expected_stages = {f"stage{number}" for number in range(1, 8)} | {"wet_lab_loop"}
    if set(execution) != expected_stages:
        raise ValueError(f"Unexpected execution keys: {sorted(execution)}")

    stage1 = execution["stage1"]
    if stage1.get("input_proteins") != 3 or stage1.get("exact_translation_matches") != 3:
        raise ValueError("Stage 1 translation audit is incomplete")

    stage2 = execution["stage2"]
    proposals = stage2.get("candidate_count")
    if proposals != sum(stage2.get("generators", {}).values()):
        raise ValueError("Stage 2 generator counts do not sum to candidate_count")
    if proposals != sum(stage2.get("candidate_types", {}).values()):
        raise ValueError("Stage 2 candidate types do not sum to candidate_count")

    stage3 = execution["stage3"]
    active = stage3.get("structure_candidates")
    if active != sum(stage3.get("confidence_bands", {}).values()):
        raise ValueError("Stage 3 confidence bands do not cover all structure candidates")

    stage4 = execution["stage4"]
    stage5 = execution["stage5"]
    if stage4.get("candidate_count") != active or stage5.get("candidate_count") != active:
        raise ValueError("Stage 4/5 candidate lineage does not match Stage 3")
    if stage4.get("mhc_candidate_count") != active:
        raise ValueError("Stage 4 MHC adapter does not cover the active set")
    if stage5.get("evaluated_adapter_count") != len(stage5.get("evaluated_adapters", [])):
        raise ValueError("Stage 5 adapter count is inconsistent")

    stage6 = execution["stage6"]
    routing = stage6.get("routing", {})
    routed = routing.get("priority", 0) + routing.get("diversity_rescue", 0) + routing.get("archive", 0)
    if routing.get("active") != active or routed != active:
        raise ValueError("Stage 6 routing does not partition the active candidates")
    if routing.get("expensive_followup") != routing.get("priority") + routing.get("diversity_rescue"):
        raise ValueError("Stage 6 expensive follow-up count is inconsistent")
    if routing.get("diversity_feature_covered") != routing.get("diversity_feature_count"):
        raise ValueError("Stage 6 diversity rescue does not cover all tracked features")
    if stage6.get("protein_draft_count") != active:
        raise ValueError("Stage 6 protein draft lineage is incomplete")

    verification = stage6.get("verification", {})
    if verification != {"checks_passed": 20, "errors": 0, "status": "pass", "warnings": 0}:
        raise ValueError("Stage 6 verification evidence is not the audited passing result")
    if execution["stage7"].get("execution_state") != "pending":
        raise ValueError("Stage 7 must remain pending for this evidence snapshot")
    if any(value != "not_started" for value in execution["wet_lab_loop"].values()):
        raise ValueError("Wet-lab loop must remain not_started for this evidence snapshot")

    hash_fields = [
        (stage1, "artifact_sha256"),
        (stage2, "artifact_sha256"),
        (stage3, "artifact_sha256"),
        (stage4, "artifact_sha256"),
        (stage5, "artifact_sha256"),
        (stage6, "protein_artifact_sha256"),
        (stage6, "mrna_artifact_sha256"),
        (routing, "routing_artifact_sha256"),
        (routing, "routing_id"),
    ]
    for container, field in hash_fields:
        _require_sha256(container.get(field), field)

    roles = data.get("model_and_authority_roles", [])
    if [row.get("stage") for row in roles] != [
        "Stage 1",
        "Stage 2",
        "Stage 3",
        "Stage 4",
        "Stage 5",
        "Stage 6",
        "Stage 7+",
    ]:
        raise ValueError("Authority-role rows are incomplete or out of order")


def derive_advantage_metrics(data: dict) -> dict[str, dict[str, float | int]]:
    execution = data["execution"]
    active = execution["stage6"]["routing"]["active"]
    routing = execution["stage6"]["routing"]
    checks = execution["stage6"]["verification"]["checks_passed"]
    return {
        "lineage": {
            "numerator": execution["stage6"]["protein_draft_count"],
            "denominator": active,
            "ratio": execution["stage6"]["protein_draft_count"] / active,
        },
        "diversity": {
            "numerator": routing["diversity_feature_covered"],
            "denominator": routing["diversity_feature_count"],
            "ratio": routing["diversity_feature_covered"] / routing["diversity_feature_count"],
        },
        "verification": {
            "numerator": checks,
            "denominator": checks + execution["stage6"]["verification"]["errors"],
            "ratio": checks / (checks + execution["stage6"]["verification"]["errors"]),
        },
        "followup_deferral": {
            "numerator": routing["archive"],
            "denominator": active,
            "ratio": routing["archive"] / active,
        },
    }


def _style_panel(axis) -> None:
    axis.set_facecolor("white")
    for spine in axis.spines.values():
        spine.set_color("#DDE2E7")
        spine.set_linewidth(0.8)


def _draw_box(axis, x, y, width, height, title, body, facecolor, edgecolor=None) -> None:
    from matplotlib.patches import FancyBboxPatch

    edgecolor = edgecolor or facecolor
    box = FancyBboxPatch(
        (x, y),
        width,
        height,
        boxstyle="round,pad=0.008,rounding_size=0.012",
        facecolor=facecolor,
        edgecolor=edgecolor,
        linewidth=1.1,
        alpha=0.98,
    )
    axis.add_patch(box)
    axis.text(
        x + width / 2,
        y + height * 0.66,
        title,
        ha="center",
        va="center",
        color="white" if facecolor != "#E5E9ED" else COLORS["ink"],
        fontsize=8.2,
        fontweight="bold",
    )
    axis.text(
        x + width / 2,
        y + height * 0.27,
        body,
        ha="center",
        va="center",
        color="white" if facecolor != "#E5E9ED" else COLORS["muted"],
        fontsize=6.8,
        linespacing=1.05,
    )


def _arrow(axis, start, end, color="#77818C", connectionstyle="arc3") -> None:
    from matplotlib.patches import FancyArrowPatch

    axis.add_patch(
        FancyArrowPatch(
            start,
            end,
            arrowstyle="-|>",
            mutation_scale=10,
            linewidth=1.2,
            color=color,
            connectionstyle=connectionstyle,
            shrinkA=2,
            shrinkB=2,
        )
    )


def plot_closed_loop(axis, data: dict) -> None:
    execution = data["execution"]
    _style_panel(axis)
    axis.set_xlim(0, 1)
    axis.set_ylim(0, 1)
    axis.set_xticks([])
    axis.set_yticks([])
    axis.set_title("A. Immutable Stage 1-7 closed loop", loc="left", fontweight="bold", pad=10)

    width, height = 0.135, 0.145
    positions = {
        "s1": (0.025, 0.68),
        "s2": (0.19, 0.68),
        "s3": (0.355, 0.68),
        "s4": (0.52, 0.77),
        "s5": (0.52, 0.57),
        "s6": (0.69, 0.68),
        "s7": (0.855, 0.68),
        "wet": (0.69, 0.27),
        "learn": (0.44, 0.27),
        "round": (0.19, 0.27),
    }
    _draw_box(axis, *positions["s1"], width, height, "S1 Intake", "3 AA/CDS\n3 exact", COLORS["executed"])
    _draw_box(axis, *positions["s2"], width, height, "S2 Explore", "2,276 tracked\nproposals", COLORS["evaluated"])
    _draw_box(axis, *positions["s3"], width, height, "S3 Structure", "384 ESMFold2\nassessments", COLORS["evaluated"])
    _draw_box(axis, *positions["s4"], width, height, "S4 Biology", "881,853 MHC\nobservations", COLORS["needs_data"])
    _draw_box(axis, *positions["s5"], width, height, "S5 Develop", "3 adapters\n2 pending", COLORS["needs_data"])
    _draw_box(axis, *positions["s6"], width, height, "S6 Products", "384 protein\n7 mRNA drafts", COLORS["needs_data"])
    _draw_box(axis, *positions["s7"], 0.12, height, "S7 Portfolio", "multi-objective\nrelease", "#E5E9ED", COLORS["pending"])
    _draw_box(axis, *positions["wet"], width, height, "Experiment", "wet-lab\nrelease + assay", "#E5E9ED", COLORS["pending"])
    _draw_box(axis, *positions["learn"], width, height, "Learn", "calibrate heads\nupdate evidence", "#E5E9ED", COLORS["pending"])
    _draw_box(axis, *positions["round"], width, height, "New round", "new immutable\ncontract", "#E5E9ED", COLORS["pending"])

    _arrow(axis, (0.16, 0.752), (0.19, 0.752))
    _arrow(axis, (0.325, 0.752), (0.355, 0.752))
    _arrow(axis, (0.49, 0.752), (0.52, 0.842))
    _arrow(axis, (0.49, 0.752), (0.52, 0.642))
    _arrow(axis, (0.655, 0.842), (0.69, 0.772))
    _arrow(axis, (0.655, 0.642), (0.69, 0.72))
    _arrow(axis, (0.825, 0.752), (0.855, 0.752))
    _arrow(axis, (0.915, 0.68), (0.79, 0.415), connectionstyle="arc3,rad=-0.18")
    _arrow(axis, (0.69, 0.342), (0.575, 0.342))
    _arrow(axis, (0.44, 0.342), (0.325, 0.342))
    _arrow(axis, (0.19, 0.342), (0.092, 0.68), connectionstyle="arc3,rad=-0.24")

    legend_items = [
        (0.035, "Executed", COLORS["executed"]),
        (0.245, "Executed / review", COLORS["evaluated"]),
        (0.515, "Needs evidence", COLORS["needs_data"]),
        (0.765, "Pending", COLORS["pending"]),
    ]
    for x, label, color in legend_items:
        axis.scatter([x], [0.095], s=28, color=color, marker="s", clip_on=False)
        axis.text(x + 0.018, 0.095, label, va="center", color=COLORS["muted"], fontsize=7.2)
    axis.text(
        0.98,
        0.035,
        f"Snapshot: {data['as_of']}",
        ha="right",
        color=COLORS["muted"],
        fontsize=7.5,
    )
    assert execution["stage7"]["execution_state"] == "pending"


def plot_candidate_funnel(axis, data: dict) -> None:
    execution = data["execution"]
    proposals = execution["stage2"]["candidate_count"]
    active = execution["stage6"]["routing"]["active"]
    followup = execution["stage6"]["routing"]["expensive_followup"]
    route = execution["stage6"]["routing"]
    values = [proposals, active, followup]
    labels = ["Tracked Stage 2 proposals", "Active Stage 3-6 set", "Expensive follow-up set"]
    colors = [COLORS["evaluated"], COLORS["needs_data"], COLORS["executed"]]

    _style_panel(axis)
    bars = axis.barh([2, 1, 0], values, height=0.55, color=colors, edgecolor="white")
    axis.set_yticks([2, 1, 0], labels)
    axis.set_xlim(0, proposals * 1.12)
    axis.set_xlabel("Candidate records")
    axis.set_title("B. Broad search, controlled expensive compute", loc="left", fontweight="bold", pad=10)
    axis.grid(axis="x", color="#E1E5E9", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    axis.tick_params(colors="#34404C", labelsize=8.5)
    for bar, value in zip(bars, values):
        axis.text(
            value + proposals * 0.018,
            bar.get_y() + bar.get_height() / 2,
            f"{value:,}",
            va="center",
            fontsize=9,
            fontweight="bold",
            color=COLORS["ink"],
        )

    axis.text(
        0.98,
        0.58,
        f"Route: {route['priority']} priority + {route['diversity_rescue']} diversity rescue\n"
        f"{route['archive']} deferred ({route['archive'] / active:.1%})",
        transform=axis.transAxes,
        ha="right",
        va="center",
        fontsize=8.5,
        color=COLORS["ink"],
        bbox={"facecolor": COLORS["panel"], "edgecolor": COLORS["line"], "pad": 5},
    )
    axis.text(
        0.02,
        0.035,
        "2,276 -> 384 is compute allocation, not a claim that other proposals are biologically invalid.",
        transform=axis.transAxes,
        fontsize=7.7,
        color=COLORS["muted"],
    )


def plot_authority_table(axis, data: dict) -> None:
    _style_panel(axis)
    axis.set_xticks([])
    axis.set_yticks([])
    axis.set_title("C. Models are replaceable tools, not authority", loc="left", fontweight="bold", pad=10)

    rows = [
        ["S1-2", "Contracts, IDs,\ndedup, lineage", "Optional generators", "Human scopes; LLM\nproposals attributed"],
        ["S3", "Sequence-structure\nidentity", "ESMFold2-Fast", "Human reviews\nconfidence"],
        ["S4", "Normalize evidence;\nretain missing", "NetMHCpan / IIpan", "Human sets host\nand panels"],
        ["S5", "Liability contracts", "TMbed, metapredict", "Human sets expression\ncontext"],
        ["S6", "Exact assembly,\nrouting, verification", "Protein / RNA adapters", "Human approves\nassumptions"],
        ["S7+", "Portfolio +\nfeedback records", "Task heads / scorers", "Human releases\nexperiments"],
    ]
    table = axis.table(
        cellText=rows,
        colLabels=["Stage", "Deterministic core", "Scientific tools", "Release authority"],
        colWidths=[0.09, 0.30, 0.26, 0.35],
        cellLoc="left",
        loc="upper center",
        bbox=[0.01, 0.22, 0.98, 0.69],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(7.25)
    for (row, column), cell in table.get_celld().items():
        cell.set_edgecolor("#D9DEE3")
        cell.set_linewidth(0.7)
        cell.PAD = 0.08
        if row == 0:
            cell.set_facecolor("#34404C")
            cell.get_text().set_color("white")
            cell.get_text().set_fontweight("bold")
        else:
            cell.set_facecolor("#F7F8FA" if row % 2 else "white")
            cell.get_text().set_color(COLORS["ink"])
            if column == 0:
                cell.get_text().set_fontweight("bold")
    axis.text(
        0.5,
        0.095,
        "Models = evidence  |  Versioned code = state and control\nHumans = experimental release authority",
        ha="center",
        va="center",
        fontsize=8.2,
        fontweight="bold",
        color=COLORS["executed"],
    )


def plot_advantages(axis, data: dict) -> None:
    metrics = derive_advantage_metrics(data)
    keys = ["lineage", "diversity", "verification", "followup_deferral"]
    labels = [
        "Active candidate lineage",
        "Tracked diversity coverage",
        "Stage 6 verification",
        "Expensive follow-up deferred",
    ]
    values = [metrics[key]["ratio"] * 100 for key in keys]
    colors = [COLORS["executed"], COLORS["evaluated"], "#4E8B57", COLORS["needs_data"]]

    _style_panel(axis)
    bars = axis.barh([3, 2, 1, 0], values, height=0.58, color=colors, edgecolor="white")
    axis.set_yticks([3, 2, 1, 0], labels)
    axis.set_xlim(0, 112)
    axis.set_ylim(-0.72, 3.48)
    axis.set_xlabel("Audited coverage / candidate-level deferral (%)")
    axis.set_title("D. Quantified system advantages", loc="left", fontweight="bold", pad=10)
    axis.grid(axis="x", color="#E1E5E9", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    axis.tick_params(colors="#34404C", labelsize=8.5)
    for bar, key, value in zip(bars, keys, values):
        metric = metrics[key]
        axis.text(
            min(value + 1.2, 104.0),
            bar.get_y() + bar.get_height() / 2,
            f"{metric['numerator']}/{metric['denominator']}  ({value:.1f}%)",
            va="center",
            fontsize=8.4,
            fontweight="bold",
            color=COLORS["ink"],
        )
    axis.text(
        2,
        -0.57,
        "No wet-lab efficacy, safety, or manufacturability claim: Stage 7 and assay feedback are pending.",
        fontsize=7.7,
        color=COLORS["accent"],
        fontweight="bold",
    )


def render_figure(data: dict, output_prefix: Path, dpi: int = 200) -> tuple[Path, Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    validate_evidence(data)
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    png_path = output_prefix.with_suffix(".png")
    svg_path = output_prefix.with_suffix(".svg")

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9,
            "axes.titlecolor": COLORS["ink"],
            "axes.labelcolor": "#34404C",
            "figure.facecolor": "white",
            "savefig.facecolor": "white",
            "svg.hashsalt": "vaxflow-stage-closed-loop-20260716",
        }
    )
    figure, axes = plt.subplots(2, 2, figsize=(13.5, 9.5))
    figure.subplots_adjust(left=0.075, right=0.975, top=0.865, bottom=0.09, wspace=0.22, hspace=0.30)

    plot_closed_loop(axes[0, 0], data)
    plot_candidate_funnel(axes[0, 1], data)
    plot_authority_table(axes[1, 0], data)
    plot_advantages(axes[1, 1], data)

    figure.suptitle(
        "VaxFlow: broad exploration, audited evidence, and a closed learning loop",
        x=0.075,
        y=0.965,
        ha="left",
        fontsize=18,
        fontweight="bold",
        color=COLORS["ink"],
    )
    figure.text(
        0.075,
        0.92,
        "Three-protein cattle Mock run | Stages 1-6 executed | Stage 7 and wet-lab loop pending",
        ha="left",
        fontsize=10.5,
        color=COLORS["muted"],
    )
    figure.text(
        0.075,
        0.03,
        "Evidence: versioned JSON derived from pinned runtime artifacts; exact run IDs and SHA-256 digests are listed in the report.",
        ha="left",
        fontsize=8,
        color=COLORS["muted"],
    )
    figure.savefig(png_path, dpi=dpi, bbox_inches="tight")
    figure.savefig(svg_path, format="svg", bbox_inches="tight", metadata={"Date": None})
    plt.close(figure)

    svg_text = svg_path.read_text(encoding="utf-8")
    normalized_svg = "\n".join(line.rstrip() for line in svg_text.splitlines()) + "\n"
    svg_path.write_text(normalized_svg, encoding="utf-8")
    return png_path, svg_path


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    data = load_json(args.data)
    png_path, svg_path = render_figure(data, args.output_prefix, args.dpi)
    print(f"Wrote {png_path}")
    print(f"Wrote {svg_path}")


if __name__ == "__main__":
    main()
