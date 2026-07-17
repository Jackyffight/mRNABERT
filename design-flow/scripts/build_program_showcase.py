#!/usr/bin/env python3
"""Build a self-contained bilingual showcase from audited VaxFlow artifacts."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import html
import json
from collections import Counter
from pathlib import Path
from typing import Any


DESIGN_FLOW_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = DESIGN_FLOW_ROOT.parent
DEFAULT_MODEL_DATA = REPO_ROOT / "docs/reports/model-comparison-data-20260716.json"
DEFAULT_MODEL_FIGURE = REPO_ROOT / "figures/model-comparison-20260716.png"
DEFAULT_EVO2_DATA = DESIGN_FLOW_ROOT / "docs/reports/stage7-evo2-sensitivity-20260716.json"


STAGES = (
    (
        1,
        "program_and_source_intake",
        "Program & source intake",
        "项目与来源审计",
        "Freeze the question and verify exact AA/CDS identity.",
        "冻结课题，并验证 AA/CDS 身份与翻译一致性。",
    ),
    (
        2,
        "candidate_specification",
        "Candidate exploration",
        "候选空间探索",
        "Enumerate a bounded, attributable search space.",
        "枚举有边界、可追溯的候选空间。",
    ),
    (
        3,
        "protein_structure_assessment",
        "Structure assessment",
        "结构评估",
        "Attach sequence-bound structure confidence and geometry evidence.",
        "绑定序列身份，接入结构置信度与几何证据。",
    ),
    (
        4,
        "immune_evidence_assessment",
        "Immune evidence",
        "免疫证据",
        "Normalize host-facing presentation evidence without claiming efficacy.",
        "标准化宿主相关呈递证据，但不宣称保护效果。",
    ),
    (
        5,
        "developability_assessment",
        "Developability",
        "可开发性",
        "Expose intrinsic sequence and expression-context liabilities.",
        "暴露序列内在风险与表达环境缺口。",
    ),
    (
        6,
        "protein_product_design",
        "Product realization",
        "产品实现",
        "Branch one antigen lineage into protein and mRNA product drafts.",
        "将同一抗原谱系分支为蛋白与 mRNA 产品草案。",
    ),
    (
        7,
        "integrated_ranking",
        "Integrated portfolio",
        "组合排序",
        "Rank transparently and preserve controls and diversity under budget.",
        "透明排序，并在预算下保留对照与多样性。",
    ),
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage7-run", type=Path, required=True)
    parser.add_argument("--research-run", type=Path)
    parser.add_argument("--model-comparison", type=Path, default=DEFAULT_MODEL_DATA)
    parser.add_argument("--model-figure", type=Path, default=DEFAULT_MODEL_FIGURE)
    parser.add_argument("--evo2-sensitivity", type=Path, default=DEFAULT_EVO2_DATA)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--generated-at",
        help="ISO timestamp override; defaults to the frozen Stage 7 run timestamp",
    )
    return parser.parse_args(argv)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def summary(run_dir: Path, node_id: str) -> dict[str, Any]:
    return load_json(run_dir / "nodes" / node_id / "summary.json")


def count_by(values: list[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def frozen_generated_at(manifest: dict[str, Any], override: str | None) -> str:
    value = override or manifest.get("created_at_utc")
    if not value:
        raise ValueError(
            "Stage 7 manifest has no created_at_utc; pass --generated-at explicitly"
        )
    return str(value)


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def candidate_composition(candidate: dict[str, Any]) -> str:
    counts = Counter(
        str(component.get("source_protein_id"))
        for component in candidate.get("inferred_components", [])
        if isinstance(component, dict) and component.get("source_protein_id")
    )
    if not counts:
        return "unresolved"
    return "+".join(
        f"{source}x{count}" if count > 1 else source
        for source, count in sorted(counts.items())
    )


def _best_spearman(model_data: dict[str, Any], model_id: str) -> float:
    rows = [
        row["test"]["spearman_mean"]
        for row in model_data["mrfp"]["full_finetune_lr_sweep"]
        if row["model_id"] == model_id and row["test"]["spearman_mean"] is not None
    ]
    if not rows:
        raise ValueError(f"No mRFP Spearman values for {model_id}")
    return max(float(value) for value in rows)


def _worst_spearman(model_data: dict[str, Any], model_id: str) -> float:
    rows = [
        row["test"]["spearman_mean"]
        for row in model_data["mrfp"]["full_finetune_lr_sweep"]
        if row["model_id"] == model_id and row["test"]["spearman_mean"] is not None
    ]
    if not rows:
        raise ValueError(f"No mRFP Spearman values for {model_id}")
    return min(float(value) for value in rows)


def build_snapshot(
    run_dir: Path,
    research_dir: Path | None,
    model_data_path: Path,
    evo2_path: Path,
    generated_at: str | None,
) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    manifest_path = run_dir / "manifest.json"
    candidate_path = run_dir / "nodes/candidate_specification/candidate_batch.json"
    immune_path = run_dir / "nodes/immune_evidence_assessment/immune_evidence.json"
    mrna_path = run_dir / "nodes/mrna_product_design/mrna_products.json"
    ranking_path = run_dir / "nodes/integrated_ranking/ranking_result.json"
    portfolio_path = run_dir / "nodes/integrated_ranking/provisional_portfolios.csv"

    required = [
        manifest_path,
        candidate_path,
        immune_path,
        mrna_path,
        ranking_path,
        portfolio_path,
        model_data_path,
        evo2_path,
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing showcase inputs: {missing}")

    manifest = load_json(manifest_path)
    generated_at = frozen_generated_at(manifest, generated_at)
    summaries = {node_id: summary(run_dir, node_id) for _, node_id, *_ in STAGES}
    mrna_summary = summary(run_dir, "mrna_product_design")
    candidate_batch = load_json(candidate_path)
    candidates = candidate_batch.get("candidates", [])
    if not isinstance(candidates, list):
        raise ValueError("candidate_batch.json has no candidates array")
    by_candidate_id = {str(row["candidate_id"]): row for row in candidates}

    immune = load_json(immune_path)
    mrna = load_json(mrna_path)
    ranking = load_json(ranking_path)
    portfolio_rows = read_csv(portfolio_path)
    model_data = load_json(model_data_path)
    evo2 = load_json(evo2_path)

    candidate_types = count_by([str(row.get("candidate_type", "unknown")) for row in candidates])
    generators = count_by(
        [
            str(row.get("proposal", {}).get("generator", {}).get("id", "unknown"))
            for row in candidates
        ]
    )
    confidence = {
        "higher": int(summaries["protein_structure_assessment"]["higher_confidence_count"]),
        "mixed": int(summaries["protein_structure_assessment"]["mixed_confidence_count"]),
        "low": int(summaries["protein_structure_assessment"]["low_confidence_count"]),
    }
    routing = dict(mrna_summary["routing_counts"])

    provisional = ranking.get("provisional_portfolios", {})
    if not isinstance(provisional, dict):
        raise ValueError("ranking_result provisional_portfolios must be an object")
    flattened_portfolio = [
        {**row, "modality": modality}
        for modality, rows in provisional.items()
        for row in rows
    ]
    unique_portfolio_ids = sorted({str(row["candidate_id"]) for row in flattened_portfolio})
    portfolio_compositions = count_by(
        [
            candidate_composition(by_candidate_id[candidate_id])
            for candidate_id in unique_portfolio_ids
            if candidate_id in by_candidate_id
        ]
    )
    ranking_rows = ranking.get("rankings", [])
    unique_ranked = {str(row["candidate_id"]) for row in ranking_rows}

    models = {str(row["id"]): row for row in model_data["models"]}
    proxy = {str(row["model_id"]): row for row in model_data["proxy_mlm"]["results"]}
    internal_params = int(models["internal-600k"]["parameter_count"])
    public_params = int(models["public-mrnabert"]["parameter_count"])
    internal_sps = float(proxy["internal-600k"]["samples_per_second"])
    public_sps = float(proxy["public-mrnabert"]["samples_per_second"])

    research: dict[str, Any] = {
        "status": "not_supplied",
        "source_count": 0,
        "independent_sources": 0,
        "direct_sources": 0,
        "full_text_sources": 0,
        "abstract_only_sources": 0,
        "database_records": 0,
        "claim_status": "not_started",
        "hypothesis_status": "not_started",
        "impact_status": "not_started",
    }
    research_sources_path: Path | None = None
    if research_dir is not None:
        research_dir = research_dir.resolve()
        research_sources_path = research_dir / "02-retrieval/sources.json"
        if research_sources_path.is_file():
            source_inventory = load_json(research_sources_path)
            counts = source_inventory["counts"]
            research.update(
                {
                    "status": str(source_inventory["status"]),
                    "run_id": research_dir.name,
                    "source_count": int(source_inventory["source_count"]),
                    "independent_sources": int(counts["by_arm"].get("independent-prior", 0)),
                    "direct_sources": int(counts["by_arm"].get("direct-prior", 0)),
                    "full_text_sources": int(counts["by_access_level"].get("full_text", 0)),
                    "abstract_only_sources": int(
                        counts["by_access_level"].get("abstract_only", 0)
                    ),
                    "database_records": int(
                        counts["by_access_level"].get("database_record", 0)
                    ),
                    "claim_status": (
                        "proposed"
                        if (research_dir / "03-claims/independent-claims.proposed.json").is_file()
                        else "raw_run_only"
                        if (research_dir / "03-claims/raw/independent-codex-events.jsonl").is_file()
                        else "not_started"
                    ),
                    "hypothesis_status": (
                        "proposed"
                        if (research_dir / "04-hypotheses/hypotheses.json").is_file()
                        else "not_started"
                    ),
                    "impact_status": (
                        "ready_for_review"
                        if (research_dir / "05-impact/candidate-impact.json").is_file()
                        else "not_started"
                    ),
                }
            )

    input_paths = [
        manifest_path,
        candidate_path,
        immune_path,
        mrna_path,
        ranking_path,
        portfolio_path,
        model_data_path.resolve(),
        evo2_path.resolve(),
    ]
    if research_sources_path is not None and research_sources_path.is_file():
        input_paths.append(research_sources_path)
    input_artifacts = [
        {"path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size}
        for path in input_paths
    ]

    stage_metrics = {
        "program_and_source_intake": [
            ["Accepted source pairs", str(summaries["program_and_source_intake"]["accepted_candidates"])],
            ["Audit errors", str(summaries["program_and_source_intake"]["errors"])],
        ],
        "candidate_specification": [
            ["Tracked candidates", f"{len(candidates):,}"],
            ["Fusion / truncation", f"{candidate_types.get('fusion', 0):,} / {candidate_types.get('truncation', 0):,}"],
        ],
        "protein_structure_assessment": [
            ["Assessed candidates", f"{sum(confidence.values()):,}"],
            ["Higher / mixed", f"{confidence['higher']} / {confidence['mixed']}"],
        ],
        "immune_evidence_assessment": [
            ["Candidates", f"{summaries['immune_evidence_assessment']['candidate_count']:,}"],
            ["MHC observations", f"{immune['adapter_states']['mhc_binding']['observation_count']:,}"],
        ],
        "developability_assessment": [
            ["Evaluated adapters", str(summaries["developability_assessment"]["evaluated_adapter_count"])],
            ["Review liabilities", f"{summaries['developability_assessment']['review_liability_count']:,}"],
        ],
        "protein_product_design": [
            ["Protein drafts", f"{summaries['protein_product_design']['design_count']:,}"],
            ["mRNA designs", f"{mrna_summary['design_count']:,}"],
        ],
        "integrated_ranking": [
            ["Ranking rows", f"{len(ranking_rows):,}"],
            ["Provisional slots", str(len(flattened_portfolio))],
        ],
    }

    snapshot = {
        "schema_version": "vaxflow.program-showcase.v1",
        "generated_at_utc": generated_at,
        "project": {
            "id": manifest["project_id"],
            "mode": manifest.get("context", {}).get("project_mode", "mock_workflow_validation"),
            "target": manifest.get("context", {}).get(
                "target_indication", "Prevention of lumpy skin disease caused by LSDV"
            ),
            "host": manifest.get("context", {}).get("intended_host_species", "cattle"),
            "modalities": manifest.get("context", {}).get(
                "product_modalities", ["recombinant_protein", "mrna"]
            ),
        },
        "run": {
            "run_id": manifest["run_id"],
            "path": str(run_dir),
            "status": manifest["status"],
            "current_stage": manifest["current_stage"],
        },
        "stages": [
            {
                "number": number,
                "node_id": node_id,
                "label_en": label_en,
                "label_zh": label_zh,
                "purpose_en": purpose_en,
                "purpose_zh": purpose_zh,
                "status": summaries[node_id]["status"],
                "audit_status": summaries[node_id]["computational_audit_status"],
                "run_id": summaries[node_id]["run_id"],
                "metrics": stage_metrics[node_id],
                "report_href": f"/three-protein/runs/{manifest['run_id']}/nodes/{node_id}/report.html",
            }
            for number, node_id, label_en, label_zh, purpose_en, purpose_zh in STAGES
        ],
        "search": {
            "candidate_count": len(candidates),
            "candidate_types": candidate_types,
            "generators": generators,
            "active_count": int(routing["active"]),
            "active_fraction": int(routing["active"]) / len(candidates),
        },
        "structure": {
            "candidate_count": sum(confidence.values()),
            "confidence_bands": confidence,
            "review_flag_count": int(
                summaries["protein_structure_assessment"]["review_flag_count"]
            ),
        },
        "evidence": {
            "mhc_observation_count": int(
                immune["adapter_states"]["mhc_binding"]["observation_count"]
            ),
            "developability_adapter_count": int(
                summaries["developability_assessment"]["evaluated_adapter_count"]
            ),
            "developability_liability_count": int(
                summaries["developability_assessment"]["review_liability_count"]
            ),
            "missing_immune_requirements": int(
                summaries["immune_evidence_assessment"]["missing_requirement_count"]
            ),
            "missing_developability_requirements": int(
                summaries["developability_assessment"]["missing_requirement_count"]
            ),
        },
        "products": {
            "protein_design_count": int(summaries["protein_product_design"]["design_count"]),
            "mrna_design_count": int(mrna_summary["design_count"]),
            "mrna_rejected_count": len(mrna.get("rejected_designs", [])),
            "evo2_observation_count": int(
                mrna.get("adapter_states", {}).get("evo2_sequence_score", {}).get(
                    "observation_count", 0
                )
            ),
            "routing": routing,
            "followup_fraction": int(routing["expensive_followup"]) / int(routing["active"]),
        },
        "ranking": {
            "status": ranking["status"],
            "mode": ranking["mode"],
            "ranking_rows": len(ranking_rows),
            "unique_ranked_candidates": len(unique_ranked),
            "provisional_slots": len(flattened_portfolio),
            "unique_portfolio_candidates": len(unique_portfolio_ids),
            "formal_portfolio_count": len(ranking.get("formal_portfolio", [])),
            "missing_requirements": [row["description"] for row in ranking["requirements"]],
            "portfolio": portfolio_rows,
            "portfolio_compositions": portfolio_compositions,
        },
        "evo2_sensitivity": {
            "candidate_count": int(evo2["source"]["candidate_count"]),
            "design_count": int(evo2["source"]["design_count"]),
            "spearman": float(evo2["mrna_rank_comparison"]["spearman_rank_correlation"]),
            "mean_rank_change": float(evo2["mrna_rank_comparison"]["mean_absolute_rank_change"]),
            "max_rank_change": int(evo2["mrna_rank_comparison"]["maximum_absolute_rank_change"]),
            "top_10_overlap": int(evo2["mrna_rank_comparison"]["top_10_overlap_count"]),
            "interpretation": evo2["interpretation"]["incremental_signal"],
        },
        "model_research": {
            "internal_best_spearman": _best_spearman(model_data, "internal-600k"),
            "public_best_spearman": _best_spearman(model_data, "public-mrnabert"),
            "retained_best_fraction": _best_spearman(model_data, "internal-600k")
            / _best_spearman(model_data, "public-mrnabert"),
            "internal_worst_spearman": _worst_spearman(model_data, "internal-600k"),
            "public_worst_spearman": _worst_spearman(model_data, "public-mrnabert"),
            "parameter_reduction": 1 - internal_params / public_params,
            "throughput_gain": internal_sps / public_sps - 1,
            "time_reduction": 1 - public_sps / internal_sps,
            "esmfold_native_agreement": model_data["pipeline_evidence"][
                "esmfold2_fast_native_agreement"
            ],
            "proteinmpnn_refold_status": model_data["pipeline_evidence"][
                "proteinmpnn_paired_refold"
            ]["status"],
            "three_model_shared_probe_status": model_data["mrfp"][
                "shared_ridge_three_model"
            ]["status"],
        },
        "research": research,
        "capability_boundary": {
            "deterministic_core": "L2_replayable_workflow",
            "research_skill": "L1_audited_llm_workflow",
            "claim_authority": "proposal_only",
            "release_authority": "human_only",
        },
        "input_artifacts": input_artifacts,
        "limitations": [
            "This is a Mock workflow-validation program, not an experiment-ready vaccine design.",
            "The provisional portfolio is not a formal release and currently contains only B5-family candidates under incomplete evidence.",
            "MHC binding, structure confidence, developability rules, Evo2 likelihood, and language-model scores are proxy evidence, not efficacy.",
            "The research Skill has completed source inventory only; atomic claims, hypotheses, and candidate impact are not yet frozen.",
            "No wet-lab labels have been ingested, so the loop has not been biologically calibrated.",
        ],
    }
    validate_snapshot(snapshot)
    return snapshot


def validate_snapshot(data: dict[str, Any]) -> None:
    if data.get("schema_version") != "vaxflow.program-showcase.v1":
        raise ValueError("Unsupported showcase schema")
    if len(data.get("stages", [])) != 7:
        raise ValueError("The showcase must contain exactly seven stages")
    search = data["search"]
    if sum(search["candidate_types"].values()) != search["candidate_count"]:
        raise ValueError("Candidate type counts do not cover the search pool")
    if sum(search["generators"].values()) != search["candidate_count"]:
        raise ValueError("Generator counts do not cover the search pool")
    structure = data["structure"]
    if sum(structure["confidence_bands"].values()) != structure["candidate_count"]:
        raise ValueError("Structure confidence bands do not cover the active set")
    routing = data["products"]["routing"]
    if routing["priority"] + routing["diversity_rescue"] != routing["expensive_followup"]:
        raise ValueError("Expensive follow-up routing does not partition correctly")
    if routing["priority"] + routing["diversity_rescue"] + routing["archive"] != routing["active"]:
        raise ValueError("Routing lanes do not cover the active set")
    ranking = data["ranking"]
    if ranking["formal_portfolio_count"] != 0:
        raise ValueError("Mock showcase cannot present a formal portfolio")
    if ranking["provisional_slots"] != len(ranking["portfolio"]):
        raise ValueError("Portfolio rows do not match provisional slot count")
    research = data["research"]
    if research["source_count"]:
        if research["independent_sources"] + research["direct_sources"] != research["source_count"]:
            raise ValueError("Research evidence arms do not cover the source inventory")
        if (
            research["full_text_sources"]
            + research["abstract_only_sources"]
            + research["database_records"]
            != research["source_count"]
        ):
            raise ValueError("Research access levels do not cover the source inventory")
    for artifact in data.get("input_artifacts", []):
        digest = artifact.get("sha256", "")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("Invalid input artifact SHA-256")


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _status_class(status: str) -> str:
    if status in {"complete", "pass", "ready", "evaluated"}:
        return "ok"
    if status in {"needs_data", "needs_human_input", "raw_run_only", "proposed"}:
        return "warn"
    return "muted"


def _metric_cards(items: list[tuple[str, str, str]]) -> str:
    return "".join(
        f'<div class="metric"><strong>{_e(value)}</strong><span>{_e(label)}</span>'
        f'<small>{_e(note)}</small></div>'
        for value, label, note in items
    )


def _stage_cards(data: dict[str, Any]) -> str:
    cards = []
    for stage in data["stages"]:
        metrics = "".join(
            f'<div><span>{_e(label)}</span><strong>{_e(value)}</strong></div>'
            for label, value in stage["metrics"]
        )
        cards.append(
            f'''<article class="stage-card">
              <header><span class="stage-num">{stage['number']}</span><div><h3>{_e(stage['label_zh'])}</h3><p>{_e(stage['label_en'])}</p></div></header>
              <p class="stage-purpose">{_e(stage['purpose_zh'])}<span>{_e(stage['purpose_en'])}</span></p>
              <div class="stage-metrics">{metrics}</div>
              <footer><span class="status {_status_class(stage['status'])}">{_e(stage['status'])}</span><a href="{_e(stage['report_href'])}">查看节点报告 / Open report</a></footer>
            </article>'''
        )
    return "".join(cards)


def _portfolio_rows(data: dict[str, Any]) -> str:
    rows = []
    for row in data["ranking"]["portfolio"]:
        rows.append(
            "<tr>"
            f"<td>{_e(row['modality'])}</td>"
            f"<td><code>{_e(row['candidate_key'])}</code></td>"
            f"<td>{_e(row['rank'])}</td>"
            f"<td>{float(row['score']):.3f}</td>"
            f"<td>{_e(row['selection_reason'])}</td>"
            "</tr>"
        )
    return "".join(rows)


def _provenance_rows(data: dict[str, Any]) -> str:
    return "".join(
        "<tr>"
        f"<td><code>{_e(Path(row['path']).name)}</code></td>"
        f"<td>{int(row['bytes']):,}</td>"
        f"<td><code>{_e(row['sha256'][:16])}...</code></td>"
        "</tr>"
        for row in data["input_artifacts"]
    )


def image_data_uri(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def render_html(data: dict[str, Any], model_figure_uri: str | None = None) -> str:
    validate_snapshot(data)
    search = data["search"]
    structure = data["structure"]
    products = data["products"]
    ranking = data["ranking"]
    research = data["research"]
    models = data["model_research"]
    evo2 = data["evo2_sensitivity"]
    active_width = max(3.0, search["active_count"] / search["candidate_count"] * 100)
    followup_width = max(3.0, products["routing"]["expensive_followup"] / search["candidate_count"] * 100)
    confidence_total = structure["candidate_count"]
    higher_width = structure["confidence_bands"]["higher"] / confidence_total * 100
    mixed_width = structure["confidence_bands"]["mixed"] / confidence_total * 100
    low_width = structure["confidence_bands"]["low"] / confidence_total * 100
    evidence_sha = hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    model_figure = (
        f'<img class="research-figure" src="{model_figure_uri}" alt="Audited mRNABERT model comparison">'
        if model_figure_uri
        else '<div class="figure-missing">Model comparison figure unavailable</div>'
    )
    composition_text = ", ".join(
        f"{key}: {value}" for key, value in ranking["portfolio_compositions"].items()
    )
    limitations = "".join(f"<li>{_e(item)}</li>" for item in data["limitations"])
    requirements = "".join(
        f"<li>{_e(item)}</li>" for item in ranking["missing_requirements"]
    )
    stage_cards = _stage_cards(data)
    portfolio_rows = _portfolio_rows(data)
    provenance_rows = _provenance_rows(data)

    return f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>VaxFlow | Three-protein Mock program</title>
  <style>
    :root {{ --ink:#1f2927; --muted:#64706d; --line:#d7ddda; --paper:#ffffff; --wash:#f4f6f3; --green:#167563; --blue:#376b9b; --amber:#b87925; --red:#a64e45; --graphite:#3e4846; }}
    * {{ box-sizing:border-box; }}
    html {{ scroll-behavior:smooth; }}
    body {{ margin:0; color:var(--ink); background:var(--wash); font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; line-height:1.55; letter-spacing:0; }}
    a {{ color:var(--blue); text-decoration:none; }} a:hover {{ text-decoration:underline; }}
    code {{ font-family:"SFMono-Regular",Consolas,monospace; font-size:.87em; overflow-wrap:anywhere; }}
    .inner {{ width:min(1180px,calc(100% - 40px)); margin:0 auto; }}
    .topbar {{ position:sticky; top:0; z-index:20; background:rgba(255,255,255,.96); border-bottom:1px solid var(--line); }}
    .topbar .inner {{ min-height:54px; display:flex; align-items:center; justify-content:space-between; gap:20px; }}
    .brand {{ font-weight:760; color:var(--ink); }}
    nav {{ display:flex; gap:18px; overflow-x:auto; white-space:nowrap; font-size:13px; }}
    .masthead {{ background:var(--paper); border-bottom:1px solid var(--line); padding:38px 0 30px; }}
    .eyebrow {{ margin:0 0 8px; color:var(--green); font-size:13px; font-weight:750; text-transform:uppercase; }}
    h1 {{ margin:0; font-size:36px; line-height:1.14; letter-spacing:0; max-width:900px; }}
    .subtitle {{ max-width:880px; margin:14px 0 20px; color:var(--muted); font-size:17px; }}
    .badges {{ display:flex; flex-wrap:wrap; gap:8px; }}
    .status {{ display:inline-flex; align-items:center; min-height:26px; padding:3px 8px; border:1px solid var(--line); border-radius:4px; background:#fff; font-size:12px; font-weight:700; }}
    .status.ok {{ color:var(--green); border-color:#9ac8bd; background:#f3faf8; }} .status.warn {{ color:#895a1c; border-color:#ddbd87; background:#fff8ec; }} .status.muted {{ color:var(--muted); }}
    .metric-band {{ background:#263330; color:#fff; }}
    .metric-grid {{ display:grid; grid-template-columns:repeat(6,1fr); }}
    .metric {{ min-height:128px; padding:22px 18px; border-right:1px solid #4b5754; }} .metric:last-child {{ border-right:0; }}
    .metric strong {{ display:block; font-size:27px; line-height:1.1; }} .metric span {{ display:block; margin-top:8px; font-size:13px; font-weight:700; }} .metric small {{ display:block; margin-top:4px; color:#bdc7c4; font-size:11px; }}
    section {{ padding:48px 0; border-bottom:1px solid var(--line); }} section.white {{ background:var(--paper); }}
    .section-head {{ display:flex; justify-content:space-between; gap:28px; align-items:end; margin-bottom:24px; }}
    .section-head h2 {{ margin:0; font-size:25px; letter-spacing:0; }} .section-head p {{ margin:0; max-width:610px; color:var(--muted); }}
    .stage-flow {{ display:grid; grid-template-columns:repeat(7,minmax(140px,1fr)); gap:10px; overflow-x:auto; padding-bottom:8px; }}
    .flow-node {{ position:relative; min-height:132px; padding:15px; background:#fff; border:1px solid var(--line); border-top:4px solid var(--blue); border-radius:6px; }}
    .flow-node:nth-child(1),.flow-node:nth-child(7) {{ border-top-color:var(--green); }} .flow-node:nth-child(4),.flow-node:nth-child(5),.flow-node:nth-child(6) {{ border-top-color:var(--amber); }}
    .flow-node b {{ display:block; font-size:12px; color:var(--muted); }} .flow-node strong {{ display:block; margin-top:6px; }} .flow-node span {{ display:block; margin-top:9px; color:var(--muted); font-size:12px; }}
    .stage-grid {{ display:grid; grid-template-columns:repeat(2,1fr); gap:14px; }}
    .stage-card {{ background:#fff; border:1px solid var(--line); border-radius:6px; overflow:hidden; }}
    .stage-card header {{ display:flex; gap:12px; align-items:center; padding:16px 18px 10px; }} .stage-num {{ width:32px; height:32px; display:grid; place-items:center; background:var(--graphite); color:#fff; border-radius:4px; font-weight:800; flex:0 0 auto; }}
    .stage-card h3 {{ margin:0; font-size:16px; }} .stage-card header p {{ margin:1px 0 0; color:var(--muted); font-size:12px; }}
    .stage-purpose {{ margin:0; padding:0 18px 14px; font-size:13px; }} .stage-purpose span {{ display:block; color:var(--muted); font-size:12px; }}
    .stage-metrics {{ display:grid; grid-template-columns:repeat(2,1fr); border-top:1px solid var(--line); border-bottom:1px solid var(--line); }} .stage-metrics div {{ padding:11px 18px; }} .stage-metrics div+div {{ border-left:1px solid var(--line); }} .stage-metrics span {{ display:block; color:var(--muted); font-size:11px; }} .stage-metrics strong {{ font-size:15px; }}
    .stage-card footer {{ display:flex; justify-content:space-between; gap:12px; align-items:center; padding:11px 18px; font-size:12px; }}
    .two-col {{ display:grid; grid-template-columns:1.15fr .85fr; gap:28px; align-items:start; }}
    .panel {{ background:#fff; border:1px solid var(--line); border-radius:6px; padding:20px; }} .panel h3 {{ margin:0 0 14px; font-size:17px; }}
    .funnel-row {{ display:grid; grid-template-columns:155px 1fr 76px; gap:12px; align-items:center; margin:14px 0; }} .funnel-row label {{ font-size:12px; color:var(--muted); }} .bar-track {{ height:18px; background:#e8ecea; border-radius:3px; overflow:hidden; }} .bar {{ height:100%; min-width:3px; background:var(--blue); }} .bar.active {{ background:var(--amber); }} .bar.followup {{ background:var(--green); }} .funnel-row strong {{ text-align:right; }}
    .stack {{ display:flex; height:34px; overflow:hidden; border-radius:4px; background:#e8ecea; }} .stack span {{ min-width:2px; }} .stack .higher {{ background:var(--green); width:{higher_width:.4f}%; }} .stack .mixed {{ background:var(--amber); width:{mixed_width:.4f}%; }} .stack .low {{ background:#aeb7b4; width:{low_width:.4f}%; }}
    .legend {{ display:flex; flex-wrap:wrap; gap:14px; margin-top:12px; color:var(--muted); font-size:12px; }} .legend i {{ width:9px; height:9px; display:inline-block; margin-right:5px; border-radius:2px; }}
    .callout {{ border-left:4px solid var(--amber); padding:15px 18px; background:#fff8ec; margin-top:18px; }} .callout strong {{ display:block; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }} th {{ color:var(--muted); font-weight:700; text-align:left; background:#f3f5f3; }} th,td {{ padding:10px 11px; border-bottom:1px solid var(--line); vertical-align:top; }}
    .research-figure {{ display:block; width:100%; border:1px solid var(--line); border-radius:6px; background:#fff; }} .figure-missing {{ padding:60px; text-align:center; color:var(--muted); border:1px dashed var(--line); }}
    .research-grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin:18px 0 24px; }} .research-step {{ border-top:4px solid var(--line); background:#fff; padding:15px; min-height:125px; }} .research-step.done {{ border-color:var(--green); }} .research-step.running {{ border-color:var(--amber); }} .research-step b {{ display:block; }} .research-step span {{ display:block; color:var(--muted); font-size:12px; margin-top:6px; }}
    .boundary {{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }} .boundary article {{ padding:18px; border:1px solid var(--line); border-radius:6px; background:#fff; }} .boundary h3 {{ margin:0 0 8px; font-size:16px; }} .boundary p {{ margin:0; color:var(--muted); font-size:13px; }}
    .limits {{ columns:2; column-gap:36px; }} .limits li {{ break-inside:avoid; margin:0 0 11px; }}
    .provenance {{ font-size:12px; }}
    footer.page-footer {{ padding:28px 0 42px; color:var(--muted); font-size:12px; }}
    @media (max-width:900px) {{ .metric-grid {{ grid-template-columns:repeat(3,1fr); }} .metric:nth-child(3) {{ border-right:0; }} .stage-grid,.two-col,.boundary {{ grid-template-columns:1fr; }} .research-grid {{ grid-template-columns:repeat(2,1fr); }} .stage-flow {{ grid-template-columns:repeat(7,150px); }} }}
    @media (max-width:600px) {{ .inner {{ width:min(100% - 24px,1180px); }} h1 {{ font-size:29px; }} .topbar nav {{ display:none; }} .metric-grid {{ grid-template-columns:repeat(2,1fr); }} .metric {{ min-height:112px; border-bottom:1px solid #4b5754; }} .metric:nth-child(2n) {{ border-right:0; }} .stage-grid,.research-grid {{ grid-template-columns:1fr; }} .section-head {{ display:block; }} .section-head p {{ margin-top:8px; }} .funnel-row {{ grid-template-columns:112px 1fr 58px; }} .limits {{ columns:1; }} .table-wrap {{ overflow-x:auto; }} }}
    @media print {{ .topbar {{ display:none; }} body {{ background:#fff; }} section {{ break-inside:avoid; }} a {{ color:var(--ink); }} }}
  </style>
</head>
<body>
  <div class="topbar"><div class="inner"><a class="brand" href="#top">VaxFlow</a><nav><a href="#workflow">Stage 1-7</a><a href="#results">计算结果</a><a href="#portfolio">组合排序</a><a href="#models">模型研究</a><a href="#research">研究循环</a><a href="#limits">边界</a></nav></div></div>
  <header class="masthead" id="top"><div class="inner">
    <p class="eyebrow">Audited computational program / 可审计计算项目</p>
    <h1>Three-protein vaccine design flow</h1>
    <p class="subtitle">从 3 个来源蛋白出发，完成候选探索、结构与免疫证据、双产品设计和临时组合排序。该页面展示系统已经计算了什么，也明确展示尚未获得的科学证据。</p>
    <div class="badges"><span class="status ok">Stage 1-7 executed</span><span class="status warn">Formal release blocked</span><span class="status muted">Mock workflow validation</span><span class="status muted">Run {_e(data['run']['run_id'])}</span></div>
  </div></header>
  <div class="metric-band"><div class="inner metric-grid">{_metric_cards([
      (f"{search['candidate_count']:,}", "可追踪候选", "Tracked proposals"),
      (f"{structure['candidate_count']:,}", "结构评估", "Structure assessments"),
      (f"{data['evidence']['mhc_observation_count']:,}", "MHC 观测", "MHC observations"),
      (f"{products['mrna_design_count']:,}", "mRNA 设计", "Synonymous designs"),
      (str(ranking['provisional_slots']), "临时组合槽位", "Provisional slots"),
      ("0", "正式放行", "Formal releases"),
  ])}</div></div>

  <section id="workflow" class="white"><div class="inner">
    <div class="section-head"><div><h2>Stage 1-7 已完成计算前环</h2></div><p>每个节点都保留输入、处理、输出、人工问题和下一节点交接。状态为 <code>needs_data</code> 表示计算完成但证据不完整，不代表执行失败。</p></div>
    <div class="stage-flow">{''.join(f'<div class="flow-node"><b>STAGE {s["number"]}</b><strong>{_e(s["label_zh"])}</strong><span>{_e(s["status"])}</span></div>' for s in data['stages'])}</div>
    <div class="callout"><strong>准确结论 / Accurate conclusion</strong>计算工作流已经走到 Stage 7，并产生可重算的临时排序；正式实验组合仍为空，湿实验和学习回流尚未开始。</div>
  </div></section>

  <section><div class="inner"><div class="stage-grid">{stage_cards}</div></div></section>

  <section id="results" class="white"><div class="inner">
    <div class="section-head"><div><h2>搜索广度与计算收敛</h2></div><p>候选漏斗表示计算预算分配，不是生物学淘汰。未进入昂贵复核的记录仍保留身份、谱系和已有证据。</p></div>
    <div class="two-col">
      <div class="panel"><h3>Candidate funnel / 候选漏斗</h3>
        <div class="funnel-row"><label>Tracked proposals</label><div class="bar-track"><div class="bar" style="width:100%"></div></div><strong>{search['candidate_count']:,}</strong></div>
        <div class="funnel-row"><label>Active evidence set</label><div class="bar-track"><div class="bar active" style="width:{active_width:.3f}%"></div></div><strong>{search['active_count']:,}</strong></div>
        <div class="funnel-row"><label>Expensive follow-up</label><div class="bar-track"><div class="bar followup" style="width:{followup_width:.3f}%"></div></div><strong>{products['routing']['expensive_followup']:,}</strong></div>
        <p><strong>{products['routing']['archive']:,}</strong> 条 active candidate 延后昂贵复核，候选级 deferral 为 <strong>{pct(products['routing']['archive']/products['routing']['active'])}</strong>。</p>
      </div>
      <div class="panel"><h3>Structure confidence / 结构置信度</h3>
        <div class="stack"><span class="higher"></span><span class="mixed"></span><span class="low"></span></div>
        <div class="legend"><span><i style="background:var(--green)"></i>Higher {structure['confidence_bands']['higher']}</span><span><i style="background:var(--amber)"></i>Mixed {structure['confidence_bands']['mixed']}</span><span><i style="background:#aeb7b4"></i>Low {structure['confidence_bands']['low']}</span></div>
        <p>大多数探索性 fusion 仍处于低结构置信区间。系统将其保留为待复核证据，而不是隐藏或自动判死。</p>
      </div>
    </div>
  </div></section>

  <section><div class="inner"><div class="section-head"><div><h2>证据与产品分支</h2></div><p>结构、MHC、可开发性、Evo2 和密码子指标属于不同证据类别，不能相加成未经校准的“成功概率”。</p></div>
    <div class="metric-grid panel">{_metric_cards([
      (f"{data['evidence']['mhc_observation_count']:,}", "MHC-I/II observations", "Stage 4"),
      (str(data['evidence']['developability_adapter_count']), "Developability adapters", "Stage 5"),
      (f"{data['evidence']['developability_liability_count']:,}", "Review liabilities", "Not automatic failures"),
      (f"{products['protein_design_count']:,}", "Protein drafts", "Recombinant branch"),
      (f"{products['mrna_design_count']:,}", "mRNA designs", "Translation-safe branch"),
      (f"{products['evo2_observation_count']:,}", "Evo2 observations", "52-candidate subset"),
    ])}</div>
  </div></section>

  <section id="portfolio" class="white"><div class="inner">
    <div class="section-head"><div><h2>Stage 7 临时组合</h2></div><p>104 条 modality-specific 排序记录覆盖 52 个候选；8 个槽位对应 4 个唯一候选。正式 portfolio 仍为空。</p></div>
    <div class="table-wrap"><table><thead><tr><th>Modality</th><th>Candidate</th><th>Rank</th><th>Score</th><th>Selection reason</th></tr></thead><tbody>{portfolio_rows}</tbody></table></div>
    <div class="callout"><strong>当前组合偏差 / Current portfolio bias</strong>4 个唯一候选全部属于 <code>{_e(composition_text)}</code>。这说明排序代码已经工作，但在免疫、群体覆盖和实验标签缺失时，当前特征会偏向结构/可开发性较强的 B5 家族，不能把它解释为三抗原疫苗的最终答案。</div>
    <h3>正式放行前仍缺少</h3><ul>{requirements}</ul>
  </div></section>

  <section id="models"><div class="inner">
    <div class="section-head"><div><h2>模型与工具研究</h2></div><p>这些实验验证工具位置、运行成本和代理任务表现；不同任务的指标不进入同一个模型排行榜。</p></div>
    <div class="research-grid">
      <div class="research-step done"><b>Internal mRNABERT</b><span>最佳 mRFP Spearman {models['internal_best_spearman']:.4f}，保留公开模型 {pct(models['retained_best_fraction'])}。</span></div>
      <div class="research-step done"><b>Efficiency</b><span>参数少 {pct(models['parameter_reduction'])}，同批吞吐高 {pct(models['throughput_gain'])}。</span></div>
      <div class="research-step done"><b>ESMFold2-Fast</b><span>40-record native CA lDDT {models['esmfold_native_agreement']['ca_lddt_mean']:.4f}。</span></div>
      <div class="research-step running"><b>ProteinMPNN</b><span>{_e(models['proteinmpnn_refold_status'])}，工程通过但模型未晋级。</span></div>
    </div>
    {model_figure}
    <div class="callout"><strong>Evo2 Stage 7 sensitivity</strong>在 52 个共同有 Evo2 证据的候选上，权重 0.25 前后排名 Spearman 为 {evo2['spearman']:.4f}，Top-10 重合 {evo2['top_10_overlap']}/10，平均名次变化 {evo2['mean_rank_change']:.2f}。它是可测但较小的辅助信号，不是主裁判。</div>
  </div></section>

  <section id="research" class="white"><div class="inner">
    <div class="section-head"><div><h2>开放式研究循环仍在原型阶段</h2></div><p>Stage 1-7 是可重放的确定性系统；文献 Claim、类比迁移和 Hypothesis 目前仍由 LLM 提案，尚未成为确定性能力。</p></div>
    <div class="research-grid">
      <div class="research-step done"><b>Source audit</b><span>{research['source_count']} sources: {research['independent_sources']} independent, {research['direct_sources']} direct.</span></div>
      <div class="research-step {'running' if research['claim_status'] != 'not_started' else ''}"><b>Atomic claims</b><span>{_e(research['claim_status'])}; raw model output is not an accepted fact.</span></div>
      <div class="research-step"><b>Hypotheses</b><span>{_e(research['hypothesis_status'])}; analogy transfer remains pending.</span></div>
      <div class="research-step"><b>Candidate impact</b><span>{_e(research['impact_status'])}; no grammar patch has been applied.</span></div>
    </div>
    <div class="boundary"><article><h3>确定性核心 / Deterministic core</h3><p>身份、哈希、候选生成、模型适配、状态、排序、报告和重放校验。</p></article><article><h3>LLM 提案层 / LLM proposal plane</h3><p>研究问题、Claim 抽取、类比迁移、Hypothesis 和未建模风险发现。</p></article><article><h3>人工权限 / Human authority</h3><p>确认课题事实、处置高影响提案、批准 grammar patch 和实验 release。</p></article></div>
  </div></section>

  <section id="limits"><div class="inner"><div class="section-head"><div><h2>展示边界与未完成事项</h2></div><p>页面有意同时展示成果与缺口。任何代理指标都不能替代真实表达、免疫、安全和保护性实验。</p></div><ul class="limits">{limitations}</ul></div></section>

  <section class="white"><div class="inner"><div class="section-head"><div><h2>Evidence provenance</h2></div><p>页面由以下输入文件生成。完整 SHA-256 保存在同目录的 <code>evidence.json</code>。</p></div><div class="table-wrap"><table class="provenance"><thead><tr><th>Artifact</th><th>Bytes</th><th>SHA-256 prefix</th></tr></thead><tbody>{provenance_rows}</tbody></table></div></div></section>
  <footer class="page-footer"><div class="inner">Generated {_e(data['generated_at_utc'])} · Evidence snapshot <code>{evidence_sha[:20]}...</code> · No formal experiment release.</div></footer>
</body>
</html>
'''


def write_showcase(
    snapshot: dict[str, Any], output_dir: Path, model_figure_path: Path | None
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = output_dir / "evidence.json"
    evidence_path.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    report_path = output_dir / "index.html"
    report_path.write_text(
        render_html(snapshot, image_data_uri(model_figure_path)), encoding="utf-8"
    )
    return report_path, evidence_path


def main() -> None:
    args = parse_args()
    snapshot = build_snapshot(
        args.stage7_run,
        args.research_run,
        args.model_comparison.resolve(),
        args.evo2_sensitivity.resolve(),
        args.generated_at,
    )
    report_path, evidence_path = write_showcase(
        snapshot,
        args.output_dir.resolve(),
        args.model_figure.resolve() if args.model_figure else None,
    )
    print(f"Showcase report: {report_path}")
    print(f"Evidence snapshot: {evidence_path}")


if __name__ == "__main__":
    main()
