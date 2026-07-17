#!/usr/bin/env python3
"""Build a self-contained bilingual showcase from audited VaxFlow artifacts."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import shutil
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any


DESIGN_FLOW_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = DESIGN_FLOW_ROOT.parent
DEFAULT_MODEL_DATA = REPO_ROOT / "docs/reports/model-comparison-data-20260716.json"
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


STAGE_STORIES = {
    "program_and_source_intake": {
        "question_zh": "输入能不能信？",
        "question_en": "Can the supplied sequences be trusted?",
        "why_zh": "任何 AA/CDS 身份错误都会污染后续结构、免疫和密码子结果，因此先冻结课题和序列身份。",
        "why_en": "Any AA/CDS identity error would contaminate every downstream result, so identity is frozen first.",
        "decision_zh": "3 组 AA/CDS 精确翻译一致，作为不可变来源对照进入搜索。",
        "decision_en": "Three exact AA/CDS pairs became immutable source controls.",
    },
    "candidate_specification": {
        "question_zh": "怎样摆脱人工直觉，又不让搜索无限膨胀？",
        "question_en": "How do we escape manual intuition without making search unbounded?",
        "why_zh": "保留人工种子，同时用有版本的边界、顺序、linker 和多家族规则扩展，并为每条候选保留父子谱系。",
        "why_en": "Preserve manual seeds while expanding versioned boundaries, orders, linkers, and families with full lineage.",
        "decision_zh": "2,276 条规范候选被保留；384 条按对照、分层基线、边界和融合家族分配结构计算预算。",
        "decision_en": "2,276 canonical candidates were retained; 384 received structure-compute budget across controls and search families.",
    },
    "protein_structure_assessment": {
        "question_zh": "这些新构型是否至少具有可分析的预测形态？",
        "question_en": "Do the proposed constructs have an analyzable predicted shape?",
        "why_zh": "结构置信度、边界稳定性和几何异常可在昂贵实验前暴露明显风险，但不能代替功能验证。",
        "why_en": "Confidence, boundary stability, and geometry can expose obvious risk before experiments, without claiming function.",
        "decision_zh": "384/384 结构成功回收；低置信度候选保留为证据与复核对象，不被静默删除。",
        "decision_en": "All 384 structures were recovered; low-confidence candidates remain traceable review hypotheses.",
    },
    "immune_evidence_assessment": {
        "question_zh": "候选是否保留了可呈递、面向宿主的免疫线索？",
        "question_en": "Do candidates retain host-facing presentation evidence?",
        "why_zh": "结构好看不等于免疫有效，因此独立接入 MHC 与表面代理证据；技术 panel 只验证链路，不代表群体覆盖。",
        "why_en": "A plausible fold is not immunity, so MHC and surface evidence are tracked separately; the technical panel is not population coverage.",
        "decision_zh": "881,853 条 MHC 观测完成技术验证，但宿主 panel 与保守性不足，暂不进入正式排名权重。",
        "decision_en": "881,853 MHC observations validated plumbing, but incomplete host and conservation context keeps them out of formal weighting.",
    },
    "developability_assessment": {
        "question_zh": "序列是否带有表达、拓扑或无序风险？",
        "question_en": "Does the sequence carry expression, topology, or disorder liabilities?",
        "why_zh": "结构与免疫信号之外，跨膜区、信号肽、无序和内在序列风险会直接影响产品可实现性。",
        "why_en": "Transmembrane, signal-peptide, disorder, and intrinsic sequence liabilities independently affect product realization.",
        "decision_zh": "TMbed、metapredict 与内在规则产生 2,462 条复核 liability；它们是复核项，不是自动淘汰。",
        "decision_en": "TMbed, metapredict, and intrinsic rules produced 2,462 review liabilities, not automatic failures.",
    },
    "protein_product_design": {
        "question_zh": "同一抗原怎样落成蛋白和 mRNA 两条产品路线？",
        "question_en": "How does one antigen lineage become protein and mRNA product drafts?",
        "why_zh": "抗原优先级和产品实现是两个问题；必须分别保留蛋白构建、同义 CDS、翻译校验和制造上下文。",
        "why_en": "Antigen choice and product realization are distinct, so protein constructs and synonymous CDS designs retain separate audits.",
        "decision_zh": "生成 384 条蛋白草案和 1,543 条翻译一致 mRNA CDS；52 条进入 Evo2 等昂贵复核。",
        "decision_en": "The branch produced 384 protein drafts and 1,543 translation-safe mRNA CDS designs; 52 received expensive follow-up.",
    },
    "integrated_ranking": {
        "question_zh": "预算有限时，哪些候选最值得进入下一轮？",
        "question_en": "Under a fixed budget, which candidates deserve the next round?",
        "why_zh": "用透明归一化权重排序，同时强制保留来源对照、人工对照和序列多样性，避免单一代理指标垄断。",
        "why_en": "Transparent normalized weights are combined with source controls, manual controls, and sequence-diversity constraints.",
        "decision_zh": "52 条共同证据候选形成 104 条双模态排名；8 个临时槽位对应 4 条 B5 家族候选，正式放行为 0。",
        "decision_en": "Fifty-two common-evidence candidates yielded 104 modality rows and four unique provisional B5-family members; formal release remains zero.",
    },
}


FEATURE_LABELS = {
    "structure_mean_plddt": ("结构局部置信度", "Mean pLDDT"),
    "structure_ptm": ("整体拓扑置信度", "pTM"),
    "developability_review_liability_count": ("可开发性复核项", "Developability liabilities"),
    "protein_product_translation_verified": ("蛋白 CDS 翻译校验", "Protein translation verified"),
    "mrna_best_cai_proxy": ("最佳密码子适配代理", "Best CAI proxy"),
    "mrna_full_construct_available": ("完整 mRNA 构建可用", "Full mRNA construct available"),
    "mrna_evo2_mean_score": ("Evo2 序列似然", "Evo2 sequence score"),
    "immune_surface_proxy_exposed_fraction": ("表面暴露代理", "Surface exposure proxy"),
    "pathogen_conservation_mean": ("病原保守性", "Pathogen conservation"),
    "immune_mhc_supported_fraction": ("MHC 支持比例", "MHC-supported fraction"),
    "developability_external_risk_count": ("外部可开发性风险", "External developability risk"),
    "protein_expression_supported_fraction": ("蛋白表达支持", "Protein expression support"),
    "mrna_rna_structure_mean_score": ("RNA 结构评分", "RNA structure score"),
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage7-run", type=Path, required=True)
    parser.add_argument("--research-run", type=Path)
    parser.add_argument("--model-comparison", type=Path, default=DEFAULT_MODEL_DATA)
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


def _source_ranges(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "source_protein_id": str(component["source_protein_id"]),
            "source_start": int(component["source_start"]),
            "source_end": int(component["source_end"]),
        }
        for component in candidate.get("inferred_components", [])
        if isinstance(component, dict)
        and component.get("source_protein_id")
        and component.get("source_start") is not None
        and component.get("source_end") is not None
    ]


def _score_component(row: dict[str, Any] | None, feature_id: str) -> dict[str, Any] | None:
    if row is None:
        return None
    return next(
        (component for component in row["components"] if component["feature_id"] == feature_id),
        None,
    )


def _stage_operations(process: dict[str, Any]) -> list[dict[str, str]]:
    operations = []
    for item in process.get("operations", []):
        if isinstance(item, str):
            operations.append({"operation": item, "behavior": ""})
        elif isinstance(item, dict):
            operations.append(
                {
                    "operation": str(item.get("operation", "")),
                    "behavior": str(item.get("behavior", "")),
                }
            )
    return operations


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
    structure_path = (
        run_dir / "nodes/protein_structure_assessment/structure_assessments.json"
    )
    structure_job_path = (
        run_dir / "nodes/protein_structure_assessment/inputs/job-manifest.json"
    )
    immune_path = run_dir / "nodes/immune_evidence_assessment/immune_evidence.json"
    protein_path = run_dir / "nodes/protein_product_design/protein_products.json"
    mrna_path = run_dir / "nodes/mrna_product_design/mrna_products.json"
    ranking_path = run_dir / "nodes/integrated_ranking/ranking_result.json"
    ranking_spec_path = (
        run_dir / "nodes/integrated_ranking/inputs/ranking_specification.json"
    )
    portfolio_path = run_dir / "nodes/integrated_ranking/provisional_portfolios.csv"

    required = [
        manifest_path,
        candidate_path,
        structure_path,
        structure_job_path,
        immune_path,
        protein_path,
        mrna_path,
        ranking_path,
        ranking_spec_path,
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
    processes = {
        node_id: load_json(run_dir / "nodes" / node_id / "process_record.json")
        for _, node_id, *_ in STAGES
    }
    mrna_process = load_json(run_dir / "nodes/mrna_product_design/process_record.json")
    candidate_batch = load_json(candidate_path)
    candidates = candidate_batch.get("candidates", [])
    if not isinstance(candidates, list):
        raise ValueError("candidate_batch.json has no candidates array")
    by_candidate_id = {str(row["candidate_id"]): row for row in candidates}

    structure_document = load_json(structure_path)
    structure_by_id = {
        str(row["candidate_id"]): row for row in structure_document["assessments"]
    }
    structure_job = load_json(structure_job_path)
    immune = load_json(immune_path)
    protein = load_json(protein_path)
    protein_by_id = {str(row["candidate_id"]): row for row in protein["products"]}
    mrna = load_json(mrna_path)
    mrna_by_id: dict[str, list[dict[str, Any]]] = {}
    for design in mrna["designs"]:
        mrna_by_id.setdefault(str(design["candidate_id"]), []).append(design)
    ranking = load_json(ranking_path)
    ranking_spec = load_json(ranking_spec_path)
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
    rank_by_candidate_modality = {
        (str(row["candidate_id"]), str(row["modality"])): row for row in ranking_rows
    }

    selected_candidate_ids = [str(row["candidate_id"]) for row in structure_job["records"]]
    selected_generators = count_by(
        [
            str(
                by_candidate_id[candidate_id]
                .get("proposal", {})
                .get("generator", {})
                .get("id", "unknown")
            )
            for candidate_id in selected_candidate_ids
        ]
    )

    top_candidates = []
    for candidate_id in sorted(
        unique_portfolio_ids,
        key=lambda value: (
            min(
                int(rank_by_candidate_modality[(value, modality)]["rank"])
                for modality in ("protein", "mrna")
                if (value, modality) in rank_by_candidate_modality
            ),
            value,
        ),
    ):
        candidate = by_candidate_id[candidate_id]
        assessment = structure_by_id[candidate_id]
        protein_product = protein_by_id[candidate_id]
        designs = sorted(
            mrna_by_id.get(candidate_id, []),
            key=lambda row: (-float(row["metrics"].get("cai_proxy") or 0.0), row["design_id"]),
        )
        rankings = {
            modality: rank_by_candidate_modality.get((candidate_id, modality))
            for modality in ("protein", "mrna")
        }
        selections = {
            row["modality"]: row["selection_reason"]
            for row in flattened_portfolio
            if row["candidate_id"] == candidate_id
        }
        top_candidates.append(
            {
                "candidate_id": candidate_id,
                "candidate_key": candidate["candidate_key"],
                "display_name": candidate["display_name"],
                "candidate_type": candidate["candidate_type"],
                "sequence": candidate["amino_acid_sequence"],
                "sequence_sha256": candidate["amino_acid_sha256"],
                "length": len(candidate["amino_acid_sequence"]),
                "source_ranges": _source_ranges(candidate),
                "generator_id": candidate["proposal"]["generator"]["id"],
                "proposal_rationale": candidate["proposal"]["rationale"],
                "structure": {
                    "mean_plddt": float(assessment["mean_plddt"]),
                    "ptm": float(assessment["ptm"]),
                    "confidence_band": assessment["confidence_band"],
                    "fraction_plddt_at_least_90": float(
                        assessment["fraction_plddt_at_least_90"]
                    ),
                    "fraction_plddt_below_70": float(
                        assessment["fraction_plddt_below_70"]
                    ),
                    "principal_axis_vectors": assessment["geometry"][
                        "principal_axis_vectors"
                    ],
                    "review_flags": assessment["review_flags"],
                    "pdb_sha256": assessment["pdb_sha256"],
                    "source_path": str(
                        run_dir
                        / "nodes/protein_structure_assessment"
                        / assessment["structure_artifact"]["path"]
                    ),
                },
                "protein_product": {
                    "design_id": protein_product["design_id"],
                    "sequence": protein_product["final_product_sequence"],
                    "translation_verified": bool(protein_product["translation_verified"]),
                    "status": protein_product["status"],
                },
                "mrna_designs": designs,
                "best_mrna_design": designs[0] if designs else None,
                "rankings": rankings,
                "selection_reasons": selections,
                "deliverables": {
                    "pdb": f"deliverables/structures/{candidate_id}.pdb",
                    "projection": f"deliverables/structures/{candidate_id}.svg",
                },
            }
        )

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
        structure_path,
        structure_job_path,
        immune_path,
        protein_path,
        mrna_path,
        ranking_path,
        ranking_spec_path,
        portfolio_path,
        model_data_path.resolve(),
        evo2_path.resolve(),
    ]
    if research_sources_path is not None and research_sources_path.is_file():
        input_paths.append(research_sources_path)
    input_paths.extend(Path(row["structure"]["source_path"]) for row in top_candidates)
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
                "question_zh": STAGE_STORIES[node_id]["question_zh"],
                "question_en": STAGE_STORIES[node_id]["question_en"],
                "why_zh": STAGE_STORIES[node_id]["why_zh"],
                "why_en": STAGE_STORIES[node_id]["why_en"],
                "decision_zh": STAGE_STORIES[node_id]["decision_zh"],
                "decision_en": STAGE_STORIES[node_id]["decision_en"],
                "operations": (
                    _stage_operations(processes[node_id])
                    + (
                        _stage_operations(mrna_process)
                        if node_id == "protein_product_design"
                        else []
                    )
                ),
                "report_href": f"/three-protein/runs/{manifest['run_id']}/nodes/{node_id}/report.html",
            }
            for number, node_id, label_en, label_zh, purpose_en, purpose_zh in STAGES
        ],
        "search": {
            "candidate_count": len(candidates),
            "candidate_types": candidate_types,
            "generators": generators,
            "structure_panel_generators": selected_generators,
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
            "features": [
                {
                    **feature,
                    "label_zh": FEATURE_LABELS.get(feature["feature_id"], (feature["feature_id"], ""))[0],
                    "label_en": FEATURE_LABELS.get(feature["feature_id"], ("", feature["feature_id"]))[1],
                }
                for feature in ranking_spec["features"]
            ],
            "hard_gate_count": len(ranking_spec["hard_gates"]),
            "policy": ranking_spec["policy"],
            "portfolio_policy": ranking_spec["portfolio"],
        },
        "top_candidates": top_candidates,
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
    if sum(search.get("structure_panel_generators", {}).values()) != data["structure"][
        "candidate_count"
    ]:
        raise ValueError("Structure-panel generator counts do not cover the folded set")
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
    if len(data.get("top_candidates", [])) != ranking["unique_portfolio_candidates"]:
        raise ValueError("Top-candidate records do not cover the provisional portfolio")
    if not any(float(feature["weight"]) > 0 for feature in ranking.get("features", [])):
        raise ValueError("Delivery report has no positive ranking features")
    for candidate in data.get("top_candidates", []):
        pdb_path = Path(candidate["structure"]["source_path"])
        if not pdb_path.is_file():
            raise ValueError(f"Top candidate has no PDB: {candidate['candidate_id']}")
        if sha256_file(pdb_path) != candidate["structure"]["pdb_sha256"]:
            raise ValueError(f"Top-candidate PDB hash mismatch: {candidate['candidate_id']}")
        if not candidate["mrna_designs"]:
            raise ValueError(f"Top candidate has no mRNA coding design: {candidate['candidate_id']}")
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


def _provenance_rows(data: dict[str, Any]) -> str:
    return "".join(
        "<tr>"
        f"<td><code>{_e(Path(row['path']).name)}</code></td>"
        f"<td>{int(row['bytes']):,}</td>"
        f"<td><code>{_e(row['sha256'][:16])}...</code></td>"
        "</tr>"
        for row in data["input_artifacts"]
    )


def _pdb_ca_records(path: Path) -> list[tuple[float, float, float, float]]:
    records = []
    for line in path.read_text(encoding="ascii").splitlines():
        if not line.startswith("ATOM") or line[12:16].strip() != "CA":
            continue
        confidence = float(line[60:66])
        if confidence <= 1.5:
            confidence *= 100.0
        records.append(
            (
                float(line[30:38]),
                float(line[38:46]),
                float(line[46:54]),
                confidence,
            )
        )
    if len(records) < 2:
        raise ValueError(f"PDB has fewer than two C-alpha atoms: {path}")
    return records


def _confidence_color(value: float) -> str:
    if value >= 90:
        return "#36c69a"
    if value >= 70:
        return "#4f91c7"
    if value >= 50:
        return "#d79b3d"
    return "#c95e59"


def structure_projection_svg(candidate: dict[str, Any]) -> str:
    records = _pdb_ca_records(Path(candidate["structure"]["source_path"]))
    axes = candidate["structure"]["principal_axis_vectors"]
    center = tuple(
        sum(record[index] for record in records) / len(records) for index in range(3)
    )
    projected = []
    for x, y, z, confidence in records:
        centered = (x - center[0], y - center[1], z - center[2])
        px = sum(centered[index] * float(axes[0][index]) for index in range(3))
        py = -sum(centered[index] * float(axes[1][index]) for index in range(3))
        projected.append((px, py, confidence))

    width, height, padding = 640.0, 400.0, 34.0
    x_values = [point[0] for point in projected]
    y_values = [point[1] for point in projected]
    x_span = max(max(x_values) - min(x_values), 1.0)
    y_span = max(max(y_values) - min(y_values), 1.0)
    scale = min((width - 2 * padding) / x_span, (height - 2 * padding) / y_span)
    x_mid = (max(x_values) + min(x_values)) / 2
    y_mid = (max(y_values) + min(y_values)) / 2
    screen = [
        (
            width / 2 + (x - x_mid) * scale,
            height / 2 + (y - y_mid) * scale,
            confidence,
        )
        for x, y, confidence in projected
    ]
    segments = []
    for left, right in zip(screen, screen[1:]):
        confidence = (left[2] + right[2]) / 2
        segments.append(
            f'<line x1="{left[0]:.2f}" y1="{left[1]:.2f}" '
            f'x2="{right[0]:.2f}" y2="{right[1]:.2f}" '
            f'stroke="{_confidence_color(confidence)}" />'
        )
    start, end = screen[0], screen[-1]
    title = html.escape(candidate["display_name"])
    digest = html.escape(candidate["structure"]["pdb_sha256"])
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400" role="img" aria-labelledby="title desc">
  <title id="title">{title} C-alpha structure projection</title>
  <desc id="desc">Principal-axis projection from checksum-bound ESMFold2 PDB {digest}</desc>
  <rect width="640" height="400" fill="#111b1d"/>
  <g opacity="0.12" stroke="#dce7e4" stroke-width="1"><path d="M32 100H608M32 200H608M32 300H608"/><path d="M160 28V372M320 28V372M480 28V372"/></g>
  <g fill="none" stroke-width="4.2" stroke-linecap="round" stroke-linejoin="round">{''.join(segments)}</g>
  <circle cx="{start[0]:.2f}" cy="{start[1]:.2f}" r="7" fill="#f3f7f5" stroke="#111b1d" stroke-width="2"/>
  <circle cx="{end[0]:.2f}" cy="{end[1]:.2f}" r="7" fill="#d79b3d" stroke="#111b1d" stroke-width="2"/>
</svg>'''


def _wrapped_sequence(sequence: str, width: int = 80) -> str:
    return "\n".join(sequence[index : index + width] for index in range(0, len(sequence), width))


def _write_fasta(path: Path, records: list[tuple[str, str]]) -> None:
    path.write_text(
        "".join(f">{header}\n{_wrapped_sequence(sequence)}\n" for header, sequence in records),
        encoding="ascii",
    )


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_deterministic_zip(zip_path: Path, root: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path == zip_path:
                continue
            info = zipfile.ZipInfo(path.relative_to(root).as_posix())
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())


def write_delivery_assets(snapshot: dict[str, Any], output_dir: Path) -> Path:
    root = output_dir / "deliverables"
    structures = root / "structures"
    structures.mkdir(parents=True, exist_ok=True)

    antigen_records = []
    protein_records = []
    mrna_records = []
    best_mrna_records = []
    ranking_rows = []
    for candidate in snapshot["top_candidates"]:
        candidate_id = candidate["candidate_id"]
        key = candidate["candidate_key"]
        antigen_records.append(
            (
                f"{candidate_id} key={key} type={candidate['candidate_type']} sha256={candidate['sequence_sha256']}",
                candidate["sequence"],
            )
        )
        protein_product = candidate["protein_product"]
        protein_records.append(
            (
                f"{protein_product['design_id']} candidate={candidate_id} key={key} status={protein_product['status']}",
                protein_product["sequence"],
            )
        )
        for design in candidate["mrna_designs"]:
            mrna_records.append(
                (
                    f"{design['design_id']} candidate={candidate_id} key={key} type={design['design_type']} cai={design['metrics'].get('cai_proxy')}",
                    design["coding_sequence_dna"],
                )
            )
        if candidate["best_mrna_design"] is not None:
            design = candidate["best_mrna_design"]
            best_mrna_records.append(
                (
                    f"{design['design_id']} candidate={candidate_id} key={key} selected=best_cai cai={design['metrics'].get('cai_proxy')}",
                    design["coding_sequence_dna"],
                )
            )
        protein_rank = candidate["rankings"]["protein"]
        mrna_rank = candidate["rankings"]["mrna"]
        ranking_rows.append(
            {
                "candidate_id": candidate_id,
                "candidate_key": key,
                "display_name": candidate["display_name"],
                "protein_rank": protein_rank["rank"] if protein_rank else "",
                "protein_score": protein_rank["score"] if protein_rank else "",
                "mrna_rank": mrna_rank["rank"] if mrna_rank else "",
                "mrna_score": mrna_rank["score"] if mrna_rank else "",
                "mean_plddt": candidate["structure"]["mean_plddt"],
                "ptm": candidate["structure"]["ptm"],
                "best_cai": (
                    candidate["best_mrna_design"]["metrics"].get("cai_proxy")
                    if candidate["best_mrna_design"]
                    else ""
                ),
                "formal_release": "false",
            }
        )
        source_pdb = Path(candidate["structure"]["source_path"])
        shutil.copyfile(source_pdb, structures / f"{candidate_id}.pdb")
        (structures / f"{candidate_id}.svg").write_text(
            structure_projection_svg(candidate), encoding="utf-8"
        )

    _write_fasta(root / "top_candidates_antigen_aa.fasta", antigen_records)
    _write_fasta(root / "top_protein_products_aa.fasta", protein_records)
    _write_fasta(root / "top_mrna_coding_designs.fasta", mrna_records)
    _write_fasta(root / "top_mrna_best_cai.fasta", best_mrna_records)
    _write_csv(
        root / "top_candidates_ranking.csv",
        [
            "candidate_id",
            "candidate_key",
            "display_name",
            "protein_rank",
            "protein_score",
            "mrna_rank",
            "mrna_score",
            "mean_plddt",
            "ptm",
            "best_cai",
            "formal_release",
        ],
        ranking_rows,
    )
    (root / "README.txt").write_text(
        "VaxFlow exploratory delivery package\n"
        "\n"
        "All candidates are provisional Mock workflow outputs. The package contains "
        "predicted structures and coding drafts, not experiment-release authorization.\n"
        "Full mRNA constructs are unavailable because approved UTR, cap, poly(A), delivery, "
        "and RNA-structure inputs remain unresolved.\n",
        encoding="ascii",
    )

    artifact_rows = [
        {
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name not in {"manifest.json", "vaxflow-top4-delivery.zip"}
    ]
    manifest = {
        "schema_version": "vaxflow.delivery-package.v1",
        "run_id": snapshot["run"]["run_id"],
        "status": "provisional_mock_delivery",
        "formal_release": False,
        "candidate_count": len(snapshot["top_candidates"]),
        "artifacts": artifact_rows,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    zip_path = root / "vaxflow-top4-delivery.zip"
    _write_deterministic_zip(zip_path, root)
    return zip_path


def _selection_reason(value: str) -> tuple[str, str]:
    return {
        "required_source_control": ("来源对照", "Required source control"),
        "required_manual_or_generated_control": (
            "人工/生成对照",
            "Required manual or generated control",
        ),
        "rank_and_diversity": ("分数与多样性", "Rank and sequence diversity"),
    }.get(value, (value, value.replace("_", " ").title()))


def _source_range_text(candidate: dict[str, Any]) -> str:
    if not candidate["source_ranges"]:
        return "Unresolved lineage"
    return " + ".join(
        f"{row['source_protein_id']} {row['source_start']}-{row['source_end']}"
        for row in candidate["source_ranges"]
    )


def _raw_component(row: dict[str, Any] | None, feature_id: str) -> Any:
    component = _score_component(row, feature_id)
    return None if component is None else component["raw_value"]


def _display_number(value: Any, digits: int = 3) -> str:
    if value is None:
        return "not evaluated"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return f"{float(value):.{digits}f}"
    return str(value)


def _stage_chapters(data: dict[str, Any]) -> str:
    chapters = []
    for stage in data["stages"]:
        operations = "".join(
            f'''<li><code>{_e(item['operation'])}</code>{f"<span>{_e(item['behavior'])}</span>" if item['behavior'] else ""}</li>'''
            for item in stage["operations"]
        )
        metrics = "".join(
            f'<div><span>{_e(label)}</span><strong>{_e(value)}</strong></div>'
            for label, value in stage["metrics"]
        )
        chapters.append(
            f'''<section class="decision-chapter" id="stage-{stage['number']}">
  <div class="inner chapter-grid">
    <aside><span>0{stage['number']}</span><b>{_e(stage['label_en'])}</b><em class="state {_status_class(stage['status'])}">{_e(stage['status'])}</em></aside>
    <div class="chapter-main">
      <p class="chapter-kicker">研究问题 / Research question</p>
      <h2>{_e(stage['question_zh'])}</h2>
      <p class="english-lead">{_e(stage['question_en'])}</p>
      <div class="reason-block"><h3>为什么这样做 / Why</h3><p>{_e(stage['why_zh'])}</p><small>{_e(stage['why_en'])}</small></div>
      <div class="decision-block"><h3>本轮结论 / Decision</h3><p>{_e(stage['decision_zh'])}</p><small>{_e(stage['decision_en'])}</small></div>
    </div>
    <div class="chapter-method">
      <h3>怎么做 / Method</h3>
      <ol>{operations}</ol>
      <div class="chapter-metrics">{metrics}</div>
      <a class="text-link" href="{_e(stage['report_href'])}">打开节点审计报告 / Open node audit</a>
    </div>
  </div>
</section>'''
        )
    return "".join(chapters)


def _candidate_cards(data: dict[str, Any]) -> str:
    cards = []
    for index, candidate in enumerate(data["top_candidates"], 1):
        protein = candidate["rankings"]["protein"]
        mrna = candidate["rankings"]["mrna"]
        reasons = []
        for modality in ("protein", "mrna"):
            if modality in candidate["selection_reasons"]:
                zh, en = _selection_reason(candidate["selection_reasons"][modality])
                reasons.append(f"{modality}: {zh} / {en}")
        flags = candidate["structure"]["review_flags"]
        flag_text = ", ".join(str(flag["code"]) for flag in flags) if flags else "none"
        best_mrna = candidate["best_mrna_design"]
        cards.append(
            f'''<article class="candidate-card">
  <div class="structure-visual">
    <img src="{_e(candidate['deliverables']['projection'])}" width="640" height="400" alt="{_e(candidate['display_name'])} checksum-bound C-alpha structure projection">
    <div class="structure-key"><span class="c90">pLDDT ≥90</span><span class="c70">70-90</span><span class="c50">50-70</span><span class="c0">&lt;50</span></div>
  </div>
  <div class="candidate-copy">
    <p class="candidate-index">PORTFOLIO MEMBER 0{index}</p>
    <h3>{_e(candidate['display_name'])}</h3>
    <code>{_e(candidate['candidate_key'])}</code>
    <p class="lineage">{_e(_source_range_text(candidate))} · {candidate['length']} aa · {_e(candidate['generator_id'])}</p>
    <div class="dual-rank">
      <div><span>Protein rank</span><strong>#{protein['rank']}</strong><small>score {float(protein['score']):.3f}</small></div>
      <div><span>mRNA rank</span><strong>#{mrna['rank']}</strong><small>score {float(mrna['score']):.3f}</small></div>
    </div>
    <div class="candidate-facts">
      <div><span>Mean pLDDT</span><b>{candidate['structure']['mean_plddt']:.2f}</b></div>
      <div><span>pTM</span><b>{candidate['structure']['ptm']:.3f}</b></div>
      <div><span>Best CAI</span><b>{float(best_mrna['metrics']['cai_proxy']):.3f}</b></div>
      <div><span>CDS variants</span><b>{len(candidate['mrna_designs'])}</b></div>
    </div>
    <p class="candidate-rationale"><b>生成依据：</b>{_e(candidate['proposal_rationale'])}</p>
    <p class="candidate-rationale"><b>入选依据：</b>{_e('；'.join(reasons))}</p>
    <p class="review-note"><b>仍需复核：</b>{_e(flag_text)}</p>
    <div class="artifact-links"><a href="{_e(candidate['deliverables']['pdb'])}">PDB</a><a href="deliverables/top_candidates_antigen_aa.fasta">AA FASTA</a><a href="deliverables/top_mrna_coding_designs.fasta">mRNA CDS</a></div>
  </div>
</article>'''
        )
    return "".join(cards)


def _ranking_feature_rows(data: dict[str, Any], modality: str, active: bool) -> str:
    features = [
        feature
        for feature in data["ranking"]["features"]
        if modality in feature["modalities"]
        and (float(feature["weight"]) > 0) == active
    ]
    total = sum(float(feature["weight"]) for feature in features) if active else 0.0
    return "".join(
        f'''<tr>
  <td><b>{_e(feature['label_zh'])}</b><small>{_e(feature['label_en'])}</small></td>
  <td>{_e(feature['direction'])}</td>
  <td>{float(feature['weight']):.2f}</td>
  <td>{(float(feature['weight']) / total * 100):.1f}%</td>
  <td>{'required' if feature['required'] else 'optional'}</td>
</tr>'''
        for feature in features
    )


def _top_ranking_rows(data: dict[str, Any]) -> str:
    rows = []
    for candidate in data["top_candidates"]:
        protein = candidate["rankings"]["protein"]
        mrna = candidate["rankings"]["mrna"]
        rows.append(
            f'''<tr>
  <td><b>{_e(candidate['candidate_key'])}</b><small>{_e(_source_range_text(candidate))}</small></td>
  <td>#{protein['rank']} / {float(protein['score']):.3f}</td>
  <td>#{mrna['rank']} / {float(mrna['score']):.3f}</td>
  <td>{_display_number(_raw_component(protein, 'structure_mean_plddt'), 2)}</td>
  <td>{_display_number(_raw_component(protein, 'structure_ptm'), 3)}</td>
  <td>{_display_number(_raw_component(protein, 'developability_review_liability_count'), 0)}</td>
  <td>{_display_number(_raw_component(protein, 'protein_product_translation_verified'), 0)}</td>
  <td>{_display_number(_raw_component(mrna, 'mrna_best_cai_proxy'), 3)}</td>
  <td>{_display_number(_raw_component(mrna, 'mrna_evo2_mean_score'), 3)}</td>
</tr>'''
        )
    return "".join(rows)


def render_html(data: dict[str, Any]) -> str:
    validate_snapshot(data)
    search = data["search"]
    structure = data["structure"]
    products = data["products"]
    ranking = data["ranking"]
    research = data["research"]
    models = data["model_research"]
    evo2 = data["evo2_sensitivity"]
    evidence_sha = hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    cover_structures = "".join(
        f'''<figure><img src="{_e(candidate['deliverables']['projection'])}" width="640" height="400" alt="{_e(candidate['display_name'])} structure"><figcaption><b>{_e(candidate['candidate_key'])}</b><span>pLDDT {candidate['structure']['mean_plddt']:.1f}</span></figcaption></figure>'''
        for candidate in data["top_candidates"]
    )
    generator_rows = "".join(
        f'<tr><td><code>{_e(name)}</code></td><td>{count:,}</td></tr>'
        for name, count in sorted(search["generators"].items(), key=lambda item: -item[1])
    )
    panel_rows = "".join(
        f'<tr><td><code>{_e(name)}</code></td><td>{count:,}</td></tr>'
        for name, count in sorted(
            search["structure_panel_generators"].items(), key=lambda item: -item[1]
        )
    )
    zero_weight_features = "".join(
        f'<li><b>{_e(feature["label_zh"])}</b><span>{_e(feature["label_en"])}</span></li>'
        for feature in ranking["features"]
        if float(feature["weight"]) == 0
    )
    requirements = "".join(
        f"<li>{_e(item)}</li>" for item in ranking["missing_requirements"]
    )
    limitations = "".join(f"<li>{_e(item)}</li>" for item in data["limitations"])
    provenance_rows = _provenance_rows(data)

    return f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>VaxFlow Research Delivery Dossier</title>
  <style>
    :root {{ --ink:#17201f; --muted:#65716f; --line:#d6ddda; --paper:#fff; --wash:#f2f5f2; --night:#101b1d; --night2:#1c292a; --green:#15806c; --mint:#36c69a; --blue:#3f76a8; --amber:#b87521; --gold:#d79b3d; --red:#a94e48; }}
    * {{ box-sizing:border-box; }} html {{ scroll-behavior:smooth; }}
    body {{ margin:0; color:var(--ink); background:var(--wash); font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; line-height:1.58; letter-spacing:0; }}
    a {{ color:var(--blue); text-decoration:none; }} a:hover {{ text-decoration:underline; }}
    code {{ font-family:"SFMono-Regular",Consolas,monospace; font-size:.86em; overflow-wrap:anywhere; }}
    h1,h2,h3,p {{ margin-top:0; }} h1,h2,h3 {{ letter-spacing:0; }}
    .inner {{ width:min(1220px,calc(100% - 48px)); margin:0 auto; }}
    .topbar {{ position:sticky; top:0; z-index:30; background:rgba(255,255,255,.96); border-bottom:1px solid var(--line); }}
    .topbar .inner {{ min-height:58px; display:flex; align-items:center; justify-content:space-between; gap:24px; }}
    .brand {{ color:var(--ink); font-weight:820; }} .brand small {{ color:var(--muted); font-weight:550; margin-left:9px; }}
    nav {{ display:flex; gap:19px; overflow-x:auto; white-space:nowrap; font-size:12px; }}
    .cover {{ background:var(--night); color:#f5f8f7; padding:54px 0 30px; border-bottom:8px solid var(--gold); }}
    .cover-grid {{ display:grid; grid-template-columns:1fr auto; gap:32px; align-items:start; }}
    .eyebrow {{ margin:0 0 10px; color:var(--mint); font-size:12px; font-weight:800; text-transform:uppercase; }}
    h1 {{ max-width:900px; margin:0; font-size:48px; line-height:1.08; }}
    .cover h1 span {{ display:block; color:#b9c6c3; font-size:22px; margin-top:12px; font-weight:500; }}
    .cover-copy {{ max-width:870px; margin:20px 0 0; color:#c5d0cd; font-size:16px; }}
    .delivery-stamp {{ width:172px; border:1px solid #516361; padding:15px; text-align:center; }} .delivery-stamp b {{ display:block; color:var(--mint); font-size:24px; }} .delivery-stamp span {{ color:#b9c6c3; font-size:11px; }}
    .cover-status {{ display:flex; flex-wrap:wrap; gap:8px; margin:22px 0 28px; }} .cover-status span {{ border:1px solid #4a5e5a; padding:5px 9px; border-radius:4px; font-size:12px; }} .cover-status .blocked {{ color:#ffd79b; border-color:#82643c; }}
    .cover-structures {{ display:grid; grid-template-columns:repeat(4,1fr); gap:8px; }} .cover-structures figure {{ margin:0; background:#162326; border:1px solid #334446; }} .cover-structures img {{ display:block; width:100%; aspect-ratio:16/9; object-fit:cover; }} .cover-structures figcaption {{ display:flex; justify-content:space-between; gap:10px; padding:8px 10px; font-size:11px; color:#b8c6c2; }} .cover-structures b {{ color:#fff; overflow:hidden; text-overflow:ellipsis; }}
    .executive {{ background:#fff; border-bottom:1px solid var(--line); }} .executive-grid {{ display:grid; grid-template-columns:1.4fr repeat(3,1fr); }} .executive-grid>div {{ padding:28px 22px; border-right:1px solid var(--line); }} .executive-grid>div:last-child {{ border-right:0; }} .executive h2 {{ font-size:23px; margin-bottom:8px; }} .executive b {{ display:block; font-size:23px; }} .executive span,.executive p {{ color:var(--muted); font-size:12px; }}
    section {{ padding:62px 0; border-bottom:1px solid var(--line); }} .white {{ background:#fff; }} .dark {{ background:var(--night); color:#f4f7f6; }}
    .section-head {{ display:grid; grid-template-columns:.75fr 1.25fr; gap:48px; margin-bottom:34px; }} .section-head .kicker,.chapter-kicker {{ color:var(--green); font-size:11px; font-weight:850; text-transform:uppercase; }} .section-head h2 {{ font-size:31px; line-height:1.18; margin:4px 0 0; }} .section-head p {{ color:var(--muted); margin:0; }}
    .research-spine {{ display:grid; grid-template-columns:repeat(6,1fr); background:#fff; border:1px solid var(--line); }} .spine-node {{ position:relative; min-height:132px; padding:21px 17px; border-right:1px solid var(--line); }} .spine-node:last-child {{ border-right:0; }} .spine-node:after {{ content:""; position:absolute; right:-5px; top:54px; width:9px; height:9px; background:#fff; border-top:1px solid var(--line); border-right:1px solid var(--line); transform:rotate(45deg); z-index:2; }} .spine-node:last-child:after {{ display:none; }} .spine-node b {{ display:block; font-size:27px; }} .spine-node span {{ display:block; font-size:12px; font-weight:750; }} .spine-node small {{ display:block; color:var(--muted); margin-top:6px; font-size:11px; }} .spine-node.final {{ background:#fff7ea; }}
    .scope-note {{ margin:18px 0 0; padding-left:15px; border-left:3px solid var(--amber); color:var(--muted); }}
    .search-audit {{ display:grid; grid-template-columns:1fr 1fr; gap:28px; margin-top:34px; }} .audit-table {{ background:#fff; border-top:4px solid var(--blue); }} .audit-table h3 {{ padding:17px 18px 5px; margin:0; font-size:16px; }}
    table {{ width:100%; border-collapse:collapse; font-size:12px; }} th {{ text-align:left; color:var(--muted); background:#edf1ee; }} th,td {{ padding:10px 12px; border-bottom:1px solid var(--line); vertical-align:top; }} td small,td span {{ display:block; color:var(--muted); }}
    .decision-chapter {{ background:#fff; }} .decision-chapter:nth-of-type(even) {{ background:var(--wash); }} .chapter-grid {{ display:grid; grid-template-columns:130px 1fr 1.05fr; gap:42px; align-items:start; }} .chapter-grid aside {{ position:sticky; top:82px; }} .chapter-grid aside>span {{ display:block; font-size:52px; line-height:1; font-weight:850; color:#c5cfcc; }} .chapter-grid aside>b {{ display:block; margin:9px 0; font-size:12px; }} .state {{ display:inline-block; font-style:normal; font-size:10px; padding:3px 7px; border:1px solid var(--line); border-radius:3px; }} .state.ok {{ color:var(--green); border-color:#86bfb4; }} .state.warn {{ color:#895b1f; border-color:#d7b77e; }}
    .chapter-main h2 {{ font-size:28px; line-height:1.2; margin:6px 0; }} .english-lead {{ color:var(--muted); font-size:14px; }} .reason-block,.decision-block {{ margin-top:24px; padding-top:16px; border-top:1px solid var(--line); }} .reason-block h3,.decision-block h3,.chapter-method h3 {{ margin:0 0 6px; font-size:12px; text-transform:uppercase; }} .reason-block p,.decision-block p {{ margin-bottom:5px; }} .reason-block small,.decision-block small {{ display:block; color:var(--muted); }} .decision-block {{ border-top-color:var(--gold); }}
    .chapter-method {{ border-left:1px solid var(--line); padding-left:30px; }} .chapter-method ol {{ margin:10px 0 20px; padding-left:20px; }} .chapter-method li {{ margin-bottom:9px; }} .chapter-method li span {{ display:block; color:var(--muted); font-size:11px; }} .chapter-metrics {{ display:grid; grid-template-columns:1fr 1fr; border-top:1px solid var(--line); border-bottom:1px solid var(--line); margin:20px 0; }} .chapter-metrics div {{ padding:12px 4px; }} .chapter-metrics span {{ display:block; color:var(--muted); font-size:10px; }} .chapter-metrics strong {{ font-size:16px; }} .text-link {{ font-size:12px; font-weight:750; }}
    .ranking-formula {{ background:var(--night2); color:#fff; padding:24px; border-left:5px solid var(--mint); margin-bottom:32px; }} .ranking-formula code {{ display:block; font-size:18px; color:#d9e5e2; }} .ranking-formula p {{ margin:9px 0 0; color:#abbab7; }}
    .weight-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:24px; }} .weight-table {{ background:#fff; border-top:4px solid var(--green); }} .weight-table h3 {{ padding:17px 14px 5px; font-size:17px; }}
    .inactive-evidence {{ margin-top:26px; display:grid; grid-template-columns:260px 1fr; gap:24px; padding:22px; background:#fff7ea; border-left:5px solid var(--amber); }} .inactive-evidence h3 {{ margin:0; }} .inactive-evidence ul {{ display:grid; grid-template-columns:repeat(3,1fr); gap:8px 20px; margin:0; padding:0; list-style:none; }} .inactive-evidence li {{ font-size:12px; }} .inactive-evidence li span {{ display:block; color:var(--muted); }}
    .table-wrap {{ overflow-x:auto; }} .rank-detail {{ margin-top:34px; min-width:980px; }}
    .portfolio-intro {{ display:grid; grid-template-columns:.8fr 1.2fr; gap:48px; margin-bottom:32px; }} .portfolio-intro h2 {{ font-size:34px; margin-bottom:10px; }} .portfolio-intro p {{ color:var(--muted); }} .portfolio-warning {{ border-left:5px solid var(--red); padding:18px 20px; background:#fff; }}
    .candidate-list {{ display:grid; gap:22px; }} .candidate-card {{ display:grid; grid-template-columns:1fr 1fr; background:#fff; border:1px solid var(--line); border-radius:6px; overflow:hidden; }} .structure-visual {{ background:var(--night); display:flex; flex-direction:column; }} .structure-visual img {{ display:block; width:100%; aspect-ratio:16/10; object-fit:cover; flex:1; }} .structure-key {{ display:flex; flex-wrap:wrap; gap:12px; padding:10px 13px; color:#aebcb8; font-size:10px; }} .structure-key span:before {{ content:""; display:inline-block; width:8px; height:8px; margin-right:4px; }} .structure-key .c90:before {{ background:#36c69a; }} .structure-key .c70:before {{ background:#4f91c7; }} .structure-key .c50:before {{ background:#d79b3d; }} .structure-key .c0:before {{ background:#c95e59; }}
    .candidate-copy {{ padding:27px 30px; }} .candidate-index {{ color:var(--green); font-size:10px; font-weight:850; }} .candidate-copy h3 {{ font-size:24px; margin:5px 0 4px; }} .lineage {{ color:var(--muted); font-size:12px; margin:8px 0 18px; }} .dual-rank {{ display:grid; grid-template-columns:1fr 1fr; border-top:1px solid var(--line); border-bottom:1px solid var(--line); }} .dual-rank div {{ padding:14px 0; }} .dual-rank div+div {{ border-left:1px solid var(--line); padding-left:18px; }} .dual-rank span,.dual-rank small {{ display:block; color:var(--muted); font-size:10px; }} .dual-rank strong {{ font-size:28px; }} .candidate-facts {{ display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin:17px 0; }} .candidate-facts span {{ display:block; color:var(--muted); font-size:9px; }} .candidate-facts b {{ font-size:14px; }} .candidate-rationale,.review-note {{ font-size:12px; margin:8px 0; }} .review-note {{ color:#7e5149; }} .artifact-links {{ display:flex; gap:8px; margin-top:17px; }} .artifact-links a {{ padding:5px 9px; border:1px solid var(--line); border-radius:4px; font-size:11px; font-weight:750; }}
    .delivery-band {{ background:#152224; color:#fff; }} .delivery-grid {{ display:grid; grid-template-columns:1fr 1.1fr; gap:56px; align-items:start; }} .delivery-grid h2 {{ font-size:34px; }} .delivery-grid p {{ color:#b9c7c3; }} .download-primary {{ display:inline-block; margin:12px 0 24px; padding:12px 16px; background:var(--gold); color:#121b1c; border-radius:4px; font-weight:850; }} .download-list {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; }} .download-list a {{ color:#dce7e4; border-bottom:1px solid #405254; padding:8px 0; font-size:12px; }} .delivery-note {{ border-left:4px solid var(--gold); padding:17px 20px; background:#202f31; }} .delivery-note b {{ display:block; color:#ffd28c; }}
    .assurance-grid {{ display:grid; grid-template-columns:repeat(4,1fr); border:1px solid var(--line); background:#fff; }} .assurance-grid article {{ padding:21px; border-right:1px solid var(--line); }} .assurance-grid article:last-child {{ border-right:0; }} .assurance-grid b {{ display:block; font-size:20px; }} .assurance-grid span {{ color:var(--muted); font-size:11px; }}
    .research-loop {{ display:grid; grid-template-columns:repeat(4,1fr); gap:14px; }} .research-loop article {{ padding:18px; background:#fff; border-top:4px solid #c7cfcc; }} .research-loop .done {{ border-color:var(--green); }} .research-loop .running {{ border-color:var(--amber); }} .research-loop span {{ color:var(--muted); font-size:12px; }}
    .limits {{ columns:2; column-gap:38px; }} .limits li {{ break-inside:avoid; margin-bottom:11px; }} .provenance {{ min-width:700px; }}
    .page-footer {{ padding:28px 0 44px; background:#fff; color:var(--muted); font-size:11px; }}
    @media(max-width:980px) {{ .cover-grid,.section-head,.portfolio-intro,.delivery-grid {{ grid-template-columns:1fr; }} .cover-structures {{ grid-template-columns:1fr 1fr; }} .executive-grid {{ grid-template-columns:1fr 1fr; }} .executive-grid>div:nth-child(2) {{ border-right:0; }} .research-spine {{ grid-template-columns:repeat(3,1fr); }} .spine-node:nth-child(3) {{ border-right:0; }} .search-audit,.weight-grid {{ grid-template-columns:1fr; }} .chapter-grid {{ grid-template-columns:90px 1fr; }} .chapter-method {{ grid-column:2; }} .candidate-card {{ grid-template-columns:1fr; }} .assurance-grid {{ grid-template-columns:1fr 1fr; }} .assurance-grid article:nth-child(2) {{ border-right:0; }} .research-loop {{ grid-template-columns:1fr 1fr; }} }}
    @media(max-width:620px) {{ .inner {{ width:min(100% - 24px,1220px); }} .topbar nav,.brand small {{ display:none; }} .cover {{ padding-top:34px; }} h1 {{ font-size:35px; }} .cover h1 span {{ font-size:17px; }} .delivery-stamp {{ width:100%; text-align:left; }} .cover-structures {{ grid-template-columns:1fr; }} .executive-grid,.research-spine,.assurance-grid,.research-loop {{ grid-template-columns:1fr; }} .executive-grid>div,.spine-node,.assurance-grid article {{ border-right:0; border-bottom:1px solid var(--line); }} section {{ padding:44px 0; }} .section-head h2,.portfolio-intro h2,.delivery-grid h2 {{ font-size:27px; }} .chapter-grid {{ grid-template-columns:1fr; gap:18px; }} .chapter-grid aside {{ position:static; display:flex; align-items:center; gap:12px; }} .chapter-grid aside>span {{ font-size:36px; }} .chapter-method {{ grid-column:auto; border-left:0; border-top:1px solid var(--line); padding:20px 0 0; }} .candidate-copy {{ padding:22px 18px; }} .candidate-facts {{ grid-template-columns:1fr 1fr; }} .inactive-evidence {{ grid-template-columns:1fr; }} .inactive-evidence ul {{ grid-template-columns:1fr; }} .download-list {{ grid-template-columns:1fr; }} .limits {{ columns:1; }} }}
    @media print {{ .topbar {{ display:none; }} .cover {{ background:#fff; color:var(--ink); border-top:8px solid var(--ink); }} .cover-copy,.cover h1 span {{ color:var(--muted); }} section,.candidate-card {{ break-inside:avoid; }} }}
  </style>
</head>
<body>
  <header class="topbar"><div class="inner"><a class="brand" href="#top">VaxFlow <small>Research Delivery Dossier</small></a><nav><a href="#journey">研究路径</a><a href="#decisions">决策记录</a><a href="#ranking">排名依据</a><a href="#portfolio">候选结构</a><a href="#delivery">交付包</a><a href="#boundary">边界</a></nav></div></header>

  <main id="top">
    <section class="cover"><div class="inner">
      <div class="cover-grid"><div><p class="eyebrow">Mock program · audited computational delivery</p><h1>三蛋白疫苗设计研究交付<span>Three-protein vaccine design research dossier</span></h1><p class="cover-copy">从 3 组已审计来源序列出发，系统扩大候选空间、分配结构计算、接入免疫与可开发性证据、生成蛋白和 mRNA 双产品草案，并以透明权重形成可复算的临时候选组合。</p></div><div class="delivery-stamp"><b>Stage 1-7</b><span>COMPUTATION EXECUTED<br>FORMAL RELEASE BLOCKED</span></div></div>
      <div class="cover-status"><span>Run {_e(data['run']['run_id'])}</span><span>4 unique portfolio members</span><span class="blocked">0 formal releases</span><span>Mock workflow validation</span></div>
      <div class="cover-structures">{cover_structures}</div>
    </div></section>

    <section class="executive"><div class="inner executive-grid"><div><h2>交付结论</h2><p>系统已把一个依赖人工直觉的起始命题，转换成可追踪搜索、可计算证据和可下载候选。当前结果适合进入专家复核与下一轮验证设计，不适合直接宣布实验候选。</p></div><div><b>{search['candidate_count']:,}</b><span>规范候选 / canonical proposals</span></div><div><b>{products['mrna_design_count']:,}</b><span>翻译一致 mRNA CDS drafts</span></div><div><b>{ranking['unique_portfolio_candidates']}</b><span>临时候选 / provisional members</span></div></div></section>

    <section class="white" id="journey"><div class="inner"><div class="section-head"><div><p class="kicker">Research journey</p><h2>不是七个页面，而是一条研究决策链</h2></div><p>每一步解决一个不同的不确定性：先保证身份，再扩展设计空间，然后逐层加入结构、宿主、可开发性和产品证据。数字收缩代表计算预算配置，不代表未经验证的生物学淘汰。</p></div>
      <div class="research-spine"><div class="spine-node"><b>3</b><span>来源蛋白</span><small>exact AA/CDS pairs</small></div><div class="spine-node"><b>{search['candidate_count']:,}</b><span>规范候选</span><small>5 attributed generators</small></div><div class="spine-node"><b>{structure['candidate_count']}</b><span>预测结构</span><small>384 checksum-bound PDB</small></div><div class="spine-node"><b>{products['routing']['expensive_followup']}</b><span>昂贵复核</span><small>27 priority + 25 rescue</small></div><div class="spine-node"><b>{ranking['ranking_rows']}</b><span>双模态排名</span><small>52 candidates × 2</small></div><div class="spine-node final"><b>{ranking['unique_portfolio_candidates']} / 0</b><span>临时 / 正式</span><small>provisional / released</small></div></div>
      <p class="scope-note"><b>关键纪律：</b>2,276 → 384 → 52 是多保真计算分配。所有候选仍保留 ID、父子谱系、序列哈希和已有证据。</p>
      <div class="search-audit"><div class="audit-table"><h3>2,276 条候选从哪里来</h3><table><thead><tr><th>Generator</th><th>Materialized</th></tr></thead><tbody>{generator_rows}</tbody></table></div><div class="audit-table"><h3>384 条结构预算如何构成</h3><table><thead><tr><th>Generator family</th><th>Selected</th></tr></thead><tbody>{panel_rows}</tbody></table></div></div>
    </div></section>

    <div id="decisions">{_stage_chapters(data)}</div>

    <section class="dark" id="ranking"><div class="inner"><div class="section-head"><div><p class="kicker" style="color:var(--mint)">Transparent ranking</p><h2>最终 Rank 到底依据什么</h2></div><p style="color:#b7c5c1">排名不是一个黑盒“成功概率”。每个特征先在固定的 52 条共同证据候选中做方向感知的 min-max 归一化，再乘以显式权重；缺失值得到零贡献，所有结果保留分量。</p></div>
      <div class="ranking-formula"><code>score = Σ(normalized_feature × weight) / Σ(all_positive_weights)</code><p>本轮 hard gate = {ranking['hard_gate_count']}；排名允许 provisional，明确禁止 formal release。权重扰动 ±20% 另行记录敏感性。</p></div>
      <div class="weight-grid"><div class="weight-table"><h3>蛋白产品权重 / Protein</h3><table><thead><tr><th>Feature</th><th>Direction</th><th>Weight</th><th>Share</th><th>Role</th></tr></thead><tbody>{_ranking_feature_rows(data, 'protein', True)}</tbody></table></div><div class="weight-table"><h3>mRNA 产品权重 / mRNA</h3><table><thead><tr><th>Feature</th><th>Direction</th><th>Weight</th><th>Share</th><th>Role</th></tr></thead><tbody>{_ranking_feature_rows(data, 'mrna', True)}</tbody></table></div></div>
      <div class="inactive-evidence"><h3>为什么有些证据权重为 0？</h3><div><p>这些证据已被计算或预留接口，但当前 panel、宿主背景或完整性不足。系统保留它们供审计和下一轮使用，不让未经校准的信号悄悄改变排名。</p><ul>{zero_weight_features}</ul></div></div>
      <div class="table-wrap"><table class="rank-detail"><thead><tr><th>Candidate</th><th>Protein rank / score</th><th>mRNA rank / score</th><th>pLDDT</th><th>pTM</th><th>Liabilities</th><th>Protein translation</th><th>Best CAI</th><th>Evo2 mean</th></tr></thead><tbody>{_top_ranking_rows(data)}</tbody></table></div>
    </div></section>

    <section class="white" id="portfolio"><div class="inner"><div class="portfolio-intro"><div><p class="eyebrow">Provisional portfolio</p><h2>四条候选，结构和序列都可交付</h2><p>下面不是示意图。每张图由对应 checksum-bound PDB 的 Cα 坐标按主轴投影，颜色来自残基 pLDDT；PDB、AA 和 CDS 可直接下载。</p></div><div class="portfolio-warning"><b>组合偏差必须被看见</b><p>4 条唯一候选全部来自 B5 家族。原因是当前非零权重主要奖励结构、liability 和产品完整性，而 MHC、保守性、表达与实验标签尚未被校准。这是本轮计算结果，也是下一轮必须挑战的偏差，不是“三抗原最终答案”。</p></div></div><div class="candidate-list">{_candidate_cards(data)}</div></div></section>

    <section class="delivery-band" id="delivery"><div class="inner delivery-grid"><div><p class="eyebrow">Delivery package</p><h2>交付的不只是一个网页</h2><p>Top 4 的抗原 AA、蛋白产品、全部同义 mRNA CDS、最佳 CAI CDS、排名表、结构 PDB、结构投影和 SHA-256 manifest 已整理为可复算包。</p><a class="download-primary" href="deliverables/vaxflow-top4-delivery.zip">下载完整交付包 / Download package</a><div class="download-list"><a href="deliverables/top_candidates_antigen_aa.fasta">Antigen AA FASTA</a><a href="deliverables/top_protein_products_aa.fasta">Protein product FASTA</a><a href="deliverables/top_mrna_coding_designs.fasta">All mRNA CDS FASTA</a><a href="deliverables/top_mrna_best_cai.fasta">Best-CAI CDS FASTA</a><a href="deliverables/top_candidates_ranking.csv">Ranking CSV</a><a href="deliverables/manifest.json">Artifact manifest</a></div></div><div class="delivery-note"><b>为什么 full_mRNA_designs.fasta 仍为空</b><p>当前已交付的是经过翻译校验的 CDS，而不是完整制剂序列。5′/3′ UTR、cap、poly(A)、递送平台与 RNA 结构输入尚未审批；系统拒绝凭空补齐，因此明确保持 unavailable。</p><b>为什么蛋白构建仍是 draft</b><p>Top 候选已有 AA 与结构，但 CHO 表达支持、完整表达元件和实验 release gate 尚未关闭。交付包用于复核和下一轮设计，不是制造指令。</p></div></div></section>

    <section class="white"><div class="inner"><div class="section-head"><div><p class="kicker">Method assurance</p><h2>模型和工具做了什么，不做什么</h2></div><p>模型研究只用于确认工具可运行、代理任务有信号和权重是否稳定；本报告不展示模型原理图，也不把不同任务的指标混成一个总分。</p></div><div class="assurance-grid"><article><b>{models['esmfold_native_agreement']['ca_lddt_mean']:.3f}</b><span>ESMFold2-Fast 40-record native CA lDDT agreement</span></article><article><b>{models['internal_best_spearman']:.3f}</b><span>Internal mRNABERT best mRFP Spearman; public best {models['public_best_spearman']:.3f}</span></article><article><b>{evo2['spearman']:.3f}</b><span>Evo2 weight 0.25 rank Spearman; Top-10 overlap {evo2['top_10_overlap']}/10</span></article><article><b>Not qualified</b><span>ProteinMPNN engineering path passed, model promotion remains blocked</span></article></div></div></section>

    <section id="boundary"><div class="inner"><div class="section-head"><div><p class="kicker">Research boundary</p><h2>计算系统完成了，开放研究循环还没有</h2></div><p>确定性 Stage 1-7 与开放式文献研究是两种成熟度。来源清单可重建；原子 Claim、类比 Hypothesis 和候选影响仍是 LLM proposal，不能写入正式排序。</p></div><div class="research-loop"><article class="done"><b>Source inventory</b><span>{research['source_count']} sources · {research['independent_sources']} independent · {research['direct_sources']} direct</span></article><article class="running"><b>Atomic claims</b><span>{_e(research['claim_status'])}; raw output is not accepted evidence</span></article><article><b>Hypotheses</b><span>{_e(research['hypothesis_status'])}; analogy transfer pending</span></article><article><b>Candidate impact</b><span>{_e(research['impact_status'])}; no grammar patch applied</span></article></div>
      <div class="inactive-evidence"><h3>正式放行前必须关闭</h3><ul style="display:block">{requirements}</ul></div>
      <h3 style="margin-top:34px">声明边界 / Claims not supported</h3><ul class="limits">{limitations}</ul>
    </div></section>

    <section class="white"><div class="inner"><div class="section-head"><div><p class="kicker">Evidence provenance</p><h2>所有数字都有来源</h2></div><p>报告由冻结的运行产物生成。完整 SHA-256、Top 候选结构和导出文件校验保存在 <code>evidence.json</code> 与交付包 <code>manifest.json</code>。</p></div><div class="table-wrap"><table class="provenance"><thead><tr><th>Artifact</th><th>Bytes</th><th>SHA-256 prefix</th></tr></thead><tbody>{provenance_rows}</tbody></table></div></div></section>
  </main>
  <footer class="page-footer"><div class="inner">Evidence frozen {_e(data['generated_at_utc'])} · Snapshot <code>{evidence_sha[:20]}...</code> · Provisional Mock delivery · No formal experiment release.</div></footer>
  <script>window.addEventListener("load",()=>{{const id=new URLSearchParams(location.search).get("section")||location.hash.slice(1);if(id){{setTimeout(()=>document.getElementById(id)?.scrollIntoView(),0);}}}});</script>
</body>
</html>'''


def write_showcase(snapshot: dict[str, Any], output_dir: Path) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    delivery_path = write_delivery_assets(snapshot, output_dir)
    evidence_path = output_dir / "evidence.json"
    evidence_path.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    report_path = output_dir / "index.html"
    report_path.write_text(render_html(snapshot), encoding="utf-8")
    return report_path, evidence_path, delivery_path


def main() -> None:
    args = parse_args()
    snapshot = build_snapshot(
        args.stage7_run,
        args.research_run,
        args.model_comparison.resolve(),
        args.evo2_sensitivity.resolve(),
        args.generated_at,
    )
    report_path, evidence_path, delivery_path = write_showcase(
        snapshot, args.output_dir.resolve()
    )
    print(f"Showcase report: {report_path}")
    print(f"Evidence snapshot: {evidence_path}")
    print(f"Delivery package: {delivery_path}")


if __name__ == "__main__":
    main()
