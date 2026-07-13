"""Deterministic, uncertainty-aware Stage 7 integrated ranking."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any

from .config import ProjectConfig, load_project_config
from .ranking_specs import (
    RANKING_STAGE_ID,
    _resolve_stage6_run,
    load_ranking_specification,
)
from .structure_job import _load_json
from .verification import sha256_file


RANKING_RULESET_ID = "transparent-multiobjective-ranking-v1"
KNOWN_FEATURES = {
    "structure_mean_plddt",
    "structure_ptm",
    "developability_review_liability_count",
    "immune_surface_proxy_exposed_fraction",
    "pathogen_conservation_mean",
    "immune_mhc_supported_fraction",
    "developability_external_risk_count",
    "protein_product_translation_verified",
    "protein_expression_supported_fraction",
    "mrna_best_cai_proxy",
    "mrna_full_construct_available",
    "mrna_evo2_mean_score",
    "mrna_rna_structure_mean_score",
}
MODALITIES = ("protein", "mrna")


@dataclass
class RankingAnalysis:
    config: ProjectConfig
    source_run_dir: Path
    source_manifest: dict[str, Any]
    ranking_specification: dict[str, Any]
    ranking_specification_path: Path
    candidate_batch: dict[str, Any]
    result: dict[str, Any]


def _validate_bindings(
    specification: dict[str, Any],
    candidate_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    candidate_set = specification.get("candidate_set")
    if not isinstance(candidate_set, dict) or not isinstance(candidate_set.get("candidates"), list):
        raise ValueError("ranking candidate_set.candidates must be an array")
    selected = []
    seen: set[str] = set()
    for binding in candidate_set["candidates"]:
        if not isinstance(binding, dict):
            raise ValueError("ranking candidate binding must be an object")
        candidate_id = binding.get("candidate_id")
        candidate = candidate_by_id.get(candidate_id)
        if candidate is None or candidate_id in seen:
            raise ValueError("ranking contains an unknown or duplicate candidate binding")
        if (
            binding.get("candidate_key") != candidate["candidate_key"]
            or binding.get("amino_acid_sha256") != candidate["amino_acid_sha256"]
        ):
            raise ValueError(f"ranking candidate identity mismatch: {candidate_id}")
        seen.add(candidate_id)
        selected.append(candidate)
    if not selected:
        raise ValueError("ranking candidate set is empty")
    return selected


def _validate_features(raw_features: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_features, list) or not raw_features:
        raise ValueError("ranking features must be a non-empty array")
    features = []
    seen: set[str] = set()
    for raw in raw_features:
        if not isinstance(raw, dict):
            raise ValueError("ranking feature must be an object")
        feature_id = raw.get("feature_id")
        weight = raw.get("weight")
        modalities = raw.get("modalities")
        if feature_id not in KNOWN_FEATURES or feature_id in seen:
            raise ValueError(f"unknown or duplicate ranking feature: {feature_id}")
        if (
            raw.get("direction") not in {"maximize", "minimize"}
            or not isinstance(weight, (int, float))
            or isinstance(weight, bool)
            or not math.isfinite(float(weight))
            or weight < 0
            or not isinstance(raw.get("required"), bool)
            or not isinstance(modalities, list)
            or not modalities
            or set(modalities) - set(MODALITIES)
        ):
            raise ValueError(f"invalid ranking feature declaration: {feature_id}")
        seen.add(feature_id)
        features.append({**raw, "weight": float(weight)})
    if not any(feature["weight"] > 0 for feature in features):
        raise ValueError("ranking must contain at least one positive feature weight")
    return features


def _feature_values(
    candidates: list[dict[str, Any]],
    structure: dict[str, dict[str, Any]],
    immune: dict[str, dict[str, Any]],
    developability: dict[str, dict[str, Any]],
    protein_products: dict[str, dict[str, Any]],
    mrna_designs: dict[str, list[dict[str, Any]]],
    immune_document: dict[str, Any],
    developability_document: dict[str, Any],
    protein_document: dict[str, Any],
    mrna_document: dict[str, Any],
) -> dict[str, dict[str, float | None]]:
    values: dict[str, dict[str, float | None]] = {}
    for candidate in candidates:
        candidate_id = candidate["candidate_id"]
        structure_row = structure[candidate_id]
        immune_row = immune[candidate_id]
        developability_row = developability[candidate_id]
        protein_row = protein_products.get(candidate_id)
        mrna_rows = mrna_designs.get(candidate_id, [])
        protein_design_ids = {protein_row["design_id"]} if protein_row else set()
        mrna_design_ids = {row["design_id"] for row in mrna_rows}
        cai_values = [
            row["metrics"]["cai_proxy"]
            for row in mrna_rows
            if row["metrics"].get("cai_proxy") is not None
        ]
        mhc_observations = [
            observation
            for observation in immune_document["adapter_states"]
            .get("mhc_binding", {})
            .get("observations", [])
            if observation["candidate_id"] == candidate_id
        ]
        developability_states = list(
            developability_document.get("adapter_states", {}).values()
        )
        developability_observations = [
            observation
            for state in developability_states
            for observation in state.get("observations", [])
            if observation["candidate_id"] == candidate_id
        ]
        expression_observations = [
            observation
            for observation in protein_document["adapter_states"]
            .get("expression_support", {})
            .get("observations", [])
            if observation["design_id"] in protein_design_ids
        ]
        evo2_scores = [
            float(observation["score"])
            for observation in mrna_document["adapter_states"]
            .get("evo2_sequence_score", {})
            .get("observations", [])
            if observation["design_id"] in mrna_design_ids
            and observation.get("score") is not None
        ]
        rna_structure_scores = [
            float(observation["score"])
            for observation in mrna_document["adapter_states"]
            .get("rna_structure", {})
            .get("observations", [])
            if observation["design_id"] in mrna_design_ids
            and observation.get("score") is not None
        ]
        values[candidate_id] = {
            "structure_mean_plddt": float(structure_row["mean_plddt"]),
            "structure_ptm": float(structure_row["ptm"]),
            "developability_review_liability_count": float(
                developability_row["review_liability_count"]
            ),
            "immune_surface_proxy_exposed_fraction": float(
                immune_row["categories"]["surface_accessibility_proxy"]["exposed_fraction"]
            ),
            "pathogen_conservation_mean": immune_row["categories"][
                "pathogen_conservation"
            ]["mean_conservation_fraction"],
            "immune_mhc_supported_fraction": (
                sum(item["status"] == "supported" for item in mhc_observations)
                / len(mhc_observations)
                if mhc_observations
                else None
            ),
            "developability_external_risk_count": (
                float(sum(item["status"] == "risk" for item in developability_observations))
                if developability_states
                and all(state.get("status") == "evaluated" for state in developability_states)
                else None
            ),
            "protein_product_translation_verified": (
                float(bool(protein_row["translation_verified"])) if protein_row else None
            ),
            "protein_expression_supported_fraction": (
                sum(item["status"] == "supported" for item in expression_observations)
                / len(expression_observations)
                if expression_observations
                else None
            ),
            "mrna_best_cai_proxy": max(cai_values) if cai_values else None,
            "mrna_full_construct_available": (
                float(any(row["full_mrna_sequence"] is not None for row in mrna_rows))
                if mrna_rows
                else None
            ),
            "mrna_evo2_mean_score": (
                sum(evo2_scores) / len(evo2_scores) if evo2_scores else None
            ),
            "mrna_rna_structure_mean_score": (
                sum(rna_structure_scores) / len(rna_structure_scores)
                if rna_structure_scores
                else None
            ),
        }
    return values


def _normalizations(
    values: dict[str, dict[str, float | None]],
    features: list[dict[str, Any]],
) -> dict[str, dict[str, float | None]]:
    normalized = {candidate_id: {} for candidate_id in values}
    for feature in features:
        feature_id = feature["feature_id"]
        observed = [row[feature_id] for row in values.values() if row[feature_id] is not None]
        minimum = min(observed) if observed else None
        maximum = max(observed) if observed else None
        for candidate_id, row in values.items():
            value = row[feature_id]
            if value is None or minimum is None or maximum is None:
                result = None
            elif maximum == minimum:
                result = 0.5
            else:
                result = (float(value) - minimum) / (maximum - minimum)
                if feature["direction"] == "minimize":
                    result = 1.0 - result
            normalized[candidate_id][feature_id] = (
                round(result, 8) if result is not None else None
            )
    return normalized


def _hard_gate_reasons(
    raw_values: dict[str, float | None],
    modality: str,
    gates: list[dict[str, Any]],
) -> list[str]:
    reasons = []
    for gate in gates:
        if modality not in gate["modalities"]:
            continue
        value = raw_values[gate["feature_id"]]
        if value is None:
            if gate["on_missing"] == "exclude":
                reasons.append(f"{gate['gate_id']}:missing")
            continue
        threshold = gate["threshold"]
        passed = {
            ">=": value >= threshold,
            "<=": value <= threshold,
            ">": value > threshold,
            "<": value < threshold,
            "==": value == threshold,
        }[gate["operator"]]
        if not passed:
            reasons.append(f"{gate['gate_id']}:failed")
    return reasons


def _validate_gates(raw_gates: Any, features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(raw_gates, list):
        raise ValueError("hard_gates must be an array")
    known = {feature["feature_id"] for feature in features}
    gates = []
    seen: set[str] = set()
    for raw in raw_gates:
        if not isinstance(raw, dict):
            raise ValueError("hard gate must be an object")
        gate_id = raw.get("gate_id")
        modalities = raw.get("modalities")
        threshold = raw.get("threshold")
        if (
            not isinstance(gate_id, str)
            or not gate_id
            or gate_id in seen
            or raw.get("feature_id") not in known
            or raw.get("operator") not in {">=", "<=", ">", "<", "=="}
            or not isinstance(threshold, (int, float))
            or isinstance(threshold, bool)
            or not math.isfinite(float(threshold))
            or not isinstance(modalities, list)
            or not modalities
            or set(modalities) - set(MODALITIES)
            or raw.get("on_missing") not in {"exclude", "review"}
        ):
            raise ValueError(f"invalid hard gate: {gate_id}")
        seen.add(gate_id)
        gates.append({**raw, "threshold": float(threshold)})
    return gates


def _score_rows(
    candidates: list[dict[str, Any]],
    values: dict[str, dict[str, float | None]],
    normalized: dict[str, dict[str, float | None]],
    features: list[dict[str, Any]],
    gates: list[dict[str, Any]],
    modality: str,
    weights: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    rows = []
    applicable = [feature for feature in features if modality in feature["modalities"]]
    effective_weights = weights or {
        feature["feature_id"]: feature["weight"] for feature in applicable
    }
    total_weight = sum(effective_weights[feature["feature_id"]] for feature in applicable)
    if total_weight <= 0:
        raise ValueError(f"No positive ranking weight for modality {modality}")
    for candidate in candidates:
        candidate_id = candidate["candidate_id"]
        components = []
        weighted_sum = 0.0
        available_weight = 0.0
        missing_required = []
        for feature in applicable:
            feature_id = feature["feature_id"]
            weight = effective_weights[feature_id]
            value = values[candidate_id][feature_id]
            normalized_value = normalized[candidate_id][feature_id]
            if value is None and feature["required"] and weight > 0:
                missing_required.append(feature_id)
            contribution = None
            if normalized_value is not None and weight > 0:
                contribution = normalized_value * weight
                weighted_sum += contribution
                available_weight += weight
            components.append({
                "feature_id": feature_id,
                "raw_value": value,
                "normalized_value": normalized_value,
                "weight": round(weight, 8),
                "weighted_contribution": round(contribution, 8) if contribution is not None else None,
                "required": feature["required"],
            })
        gate_reasons = _hard_gate_reasons(values[candidate_id], modality, gates)
        exclusion_reasons = [f"required_feature_missing:{item}" for item in missing_required] + gate_reasons
        score = weighted_sum / total_weight
        rows.append({
            "candidate_id": candidate_id,
            "candidate_key": candidate["candidate_key"],
            "candidate_type": candidate["candidate_type"],
            "modality": modality,
            "eligible": not exclusion_reasons,
            "exclusion_reasons": exclusion_reasons,
            "score": round(score, 8),
            "evidence_coverage": round(available_weight / total_weight, 8),
            "components": components,
        })
    eligible = sorted(
        (row for row in rows if row["eligible"]),
        key=lambda row: (-row["score"], row["candidate_id"]),
    )
    rank_by_id = {row["candidate_id"]: rank for rank, row in enumerate(eligible, 1)}
    for row in rows:
        row["rank"] = rank_by_id.get(row["candidate_id"])
    return sorted(rows, key=lambda row: (not row["eligible"], row["rank"] or 10**9, row["candidate_id"]))


def _sequence_similarity(left: str, right: str, k: int = 3) -> float:
    if left == right:
        return 1.0
    if len(left) < k or len(right) < k:
        return 0.0
    left_kmers = {left[index : index + k] for index in range(len(left) - k + 1)}
    right_kmers = {right[index : index + k] for index in range(len(right) - k + 1)}
    union = left_kmers | right_kmers
    return len(left_kmers & right_kmers) / len(union) if union else 0.0


def _portfolio(
    rows: list[dict[str, Any]],
    candidate_by_id: dict[str, dict[str, Any]],
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    budget = int(policy.get("budget_per_modality", 0))
    maximum_similarity = float(policy.get("maximum_sequence_similarity", 1.0))
    source_minimum = int(policy.get("minimum_source_controls", 0))
    manual_minimum = int(policy.get("minimum_manual_controls", 0))
    if budget < 1 or not 0 <= maximum_similarity <= 1 or min(source_minimum, manual_minimum) < 0:
        raise ValueError("portfolio policy contains invalid bounds")
    if source_minimum + manual_minimum > budget:
        raise ValueError("portfolio control minimums exceed the modality budget")
    eligible = [row for row in rows if row["eligible"]]
    selected: list[dict[str, Any]] = []

    def add_matching(predicate: Any, minimum: int, reason: str) -> None:
        for row in eligible:
            if len([item for item in selected if item["selection_reason"] == reason]) >= minimum:
                break
            if row["candidate_id"] in {item["candidate_id"] for item in selected}:
                continue
            if predicate(candidate_by_id[row["candidate_id"]]):
                selected.append({**row, "selection_reason": reason})

    add_matching(lambda candidate: candidate["candidate_type"] == "source_control", source_minimum, "required_source_control")
    add_matching(lambda candidate: candidate["candidate_type"] != "source_control", manual_minimum, "required_manual_or_generated_control")
    for row in eligible:
        if len(selected) >= budget:
            break
        if row["candidate_id"] in {item["candidate_id"] for item in selected}:
            continue
        sequence = candidate_by_id[row["candidate_id"]]["amino_acid_sequence"]
        if any(
            _sequence_similarity(
                sequence,
                candidate_by_id[item["candidate_id"]]["amino_acid_sequence"],
            ) > maximum_similarity
            for item in selected
        ):
            continue
        selected.append({**row, "selection_reason": "rank_and_diversity"})
    return selected[:budget]


def _sensitivity(
    candidates: list[dict[str, Any]],
    values: dict[str, dict[str, float | None]],
    normalized: dict[str, dict[str, float | None]],
    features: list[dict[str, Any]],
    gates: list[dict[str, Any]],
    modality: str,
    perturbation: float,
) -> list[dict[str, Any]]:
    if not 0 <= perturbation <= 1:
        raise ValueError("relative_weight_perturbation must be between 0 and 1")
    base_weights = {feature["feature_id"]: feature["weight"] for feature in features if modality in feature["modalities"]}
    scenarios = [("base", base_weights)]
    for feature_id, weight in base_weights.items():
        if weight <= 0:
            continue
        for label, factor in (("low", 1 - perturbation), ("high", 1 + perturbation)):
            changed = dict(base_weights)
            changed[feature_id] = weight * factor
            scenarios.append((f"{feature_id}:{label}", changed))
    ranks: dict[str, list[int]] = {candidate["candidate_id"]: [] for candidate in candidates}
    for _, weights in scenarios:
        rows = _score_rows(candidates, values, normalized, features, gates, modality, weights)
        for row in rows:
            if row["rank"] is not None:
                ranks[row["candidate_id"]].append(row["rank"])
    return [
        {
            "candidate_id": candidate_id,
            "minimum_rank": min(candidate_ranks) if candidate_ranks else None,
            "maximum_rank": max(candidate_ranks) if candidate_ranks else None,
            "rank_span": max(candidate_ranks) - min(candidate_ranks) if candidate_ranks else None,
            "scenario_count": len(scenarios),
        }
        for candidate_id, candidate_ranks in sorted(ranks.items())
    ]


def analyze_integrated_ranking(
    project_config: str | Path,
    *,
    source_run_dir: str | Path | None = None,
) -> RankingAnalysis:
    config = load_project_config(Path(project_config))
    source = _resolve_stage6_run(
        config, Path(source_run_dir) if source_run_dir is not None else None
    )
    manifest = _load_json(source / "manifest.json")
    candidate_batch = _load_json(source / "nodes/candidate_specification/candidate_batch.json")
    spec, spec_path = load_ranking_specification(config)
    result = _compute_ranking_result(source, spec, spec_path, candidate_batch)
    return RankingAnalysis(
        config=config,
        source_run_dir=source,
        source_manifest=manifest,
        ranking_specification=spec,
        ranking_specification_path=spec_path,
        candidate_batch=candidate_batch,
        result=result,
    )


def _compute_ranking_result(
    source: Path,
    spec: dict[str, Any],
    spec_path: Path,
    candidate_batch: dict[str, Any],
) -> dict[str, Any]:
    candidate_by_id = {
        candidate["candidate_id"]: candidate for candidate in candidate_batch["candidates"]
    }
    candidates = _validate_bindings(spec, candidate_by_id)
    features = _validate_features(spec.get("features"))
    gates = _validate_gates(spec.get("hard_gates"), features)
    structure_document = _load_json(
        source / "nodes/protein_structure_assessment/structure_assessments.json"
    )
    immune_document = _load_json(source / "nodes/immune_evidence_assessment/immune_evidence.json")
    developability_document = _load_json(
        source / "nodes/developability_assessment/developability_assessments.json"
    )
    protein_document = _load_json(source / "nodes/protein_product_design/protein_products.json")
    mrna_document = _load_json(source / "nodes/mrna_product_design/mrna_products.json")
    structure = {row["candidate_id"]: row for row in structure_document["assessments"]}
    immune = {row["candidate_id"]: row for row in immune_document["candidates"]}
    developability = {row["candidate_id"]: row for row in developability_document["candidates"]}
    protein_products = {row["candidate_id"]: row for row in protein_document["products"]}
    mrna_designs: dict[str, list[dict[str, Any]]] = {}
    for design in mrna_document["designs"]:
        mrna_designs.setdefault(design["candidate_id"], []).append(design)
    selected_ids = {candidate["candidate_id"] for candidate in candidates}
    if not selected_ids <= set(structure) & set(immune) & set(developability):
        raise ValueError("Ranking inputs do not cover the exact selected candidates")
    values = _feature_values(
        candidates,
        structure,
        immune,
        developability,
        protein_products,
        mrna_designs,
        immune_document,
        developability_document,
        protein_document,
        mrna_document,
    )
    normalized = _normalizations(values, features)
    rows_by_modality = {
        modality: _score_rows(candidates, values, normalized, features, gates, modality)
        for modality in MODALITIES
    }
    portfolio_policy = spec.get("portfolio", {})
    portfolios = {
        modality: _portfolio(rows_by_modality[modality], candidate_by_id, portfolio_policy)
        for modality in MODALITIES
    }
    perturbation = float(spec.get("sensitivity", {}).get("relative_weight_perturbation", 0.2))
    sensitivity = {
        modality: _sensitivity(
            candidates, values, normalized, features, gates, modality, perturbation
        )
        for modality in MODALITIES
    }
    requirements = []
    if spec.get("candidate_set", {}).get("status") != "approved":
        requirements.append({
            "requirement_id": "approve-ranking-candidate-set",
            "status": "missing",
            "description": "Approve the exact candidate set entering integrated ranking.",
        })
    if portfolio_policy.get("status") != "approved":
        requirements.append({
            "requirement_id": "approve-ranking-portfolio-policy",
            "status": "missing",
            "description": "Approve modality budgets, control composition, and diversity threshold.",
        })
    if spec.get("policy", {}).get("status") != "approved":
        requirements.append({
            "requirement_id": "approve-ranking-feature-policy",
            "status": "missing",
            "description": "Approve features, weights, gates, missing-value policy, and risk tolerance.",
        })
    upstream = {
        "immune_evidence_assessment": immune_document["status"],
        "developability_assessment": developability_document["status"],
        "protein_product_design": protein_document["status"],
        "mrna_product_design": mrna_document["status"],
    }
    if any(status == "needs_data" for status in upstream.values()):
        requirements.append({
            "requirement_id": "resolve-upstream-evidence-gaps",
            "status": "missing",
            "description": "Resolve or explicitly waive upstream Stage 4-6 data gaps before portfolio release.",
        })
    missing_required = sorted({
        reason.split(":", 1)[1]
        for rows in rows_by_modality.values()
        for row in rows
        for reason in row["exclusion_reasons"]
        if reason.startswith("required_feature_missing:")
    })
    if missing_required:
        requirements.append({
            "requirement_id": "provide-required-ranking-features",
            "status": "missing",
            "description": "Provide required ranking features: " + ", ".join(missing_required),
        })
    control_gaps = []
    for modality, selected in portfolios.items():
        source_count = sum(
            candidate_by_id[item["candidate_id"]]["candidate_type"] == "source_control"
            for item in selected
        )
        manual_count = sum(
            candidate_by_id[item["candidate_id"]]["candidate_type"] != "source_control"
            for item in selected
        )
        if source_count < int(portfolio_policy.get("minimum_source_controls", 0)):
            control_gaps.append(f"{modality}:source_control")
        if manual_count < int(portfolio_policy.get("minimum_manual_controls", 0)):
            control_gaps.append(f"{modality}:manual_or_generated_control")
    if control_gaps:
        requirements.append({
            "requirement_id": "satisfy-portfolio-control-composition",
            "status": "missing",
            "description": "Portfolio control minimums are not met: " + ", ".join(control_gaps),
        })
    return {
        "schema_version": 1,
        "stage_id": RANKING_STAGE_ID,
        "mode": "exploratory",
        "ruleset_id": RANKING_RULESET_ID,
        "specification_id": spec["specification_id"],
        "specification_sha256": sha256_file(spec_path),
        "status": "needs_data" if requirements else "evaluated",
        "release_gate_enabled": False,
        "upstream_status": upstream,
        "feature_policy": features,
        "hard_gates": gates,
        "requirements": requirements,
        "rankings": [row for modality in MODALITIES for row in rows_by_modality[modality]],
        "provisional_portfolios": {
            modality: [
                {
                    "candidate_id": row["candidate_id"],
                    "candidate_key": row["candidate_key"],
                    "rank": row["rank"],
                    "score": row["score"],
                    "selection_reason": row["selection_reason"],
                }
                for row in portfolios[modality]
            ]
            for modality in MODALITIES
        },
        "formal_portfolio": [],
        "sensitivity": sensitivity,
        "limitations": [
            "Ranking is a transparent technical prioritization, not a vaccine efficacy prediction.",
            "Zero-weight immune features are displayed but do not influence the default ranking.",
            "Missing positive-weight evidence reduces score through the declared coverage penalty.",
            "The Stage 7 node cannot formally release an experimental portfolio.",
        ],
    }
