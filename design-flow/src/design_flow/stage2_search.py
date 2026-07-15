"""Evidence-guided, multi-family Stage 2 candidate search."""

from __future__ import annotations

import csv
import hashlib
from html import escape
from io import StringIO
from itertools import permutations, product
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable

from .config import ProjectConfig, load_project_config
from .proposal_generation import (
    CANDIDATE_STAGE_ID,
    SeedRun,
    _artifact_index_valid,
    _document_sha256,
    _json_text,
    _load_object,
    _manual_record_from_seed,
    _resolve_seed_run,
    _sequence_sha256,
    _text,
    _wrap_fasta,
)
from .qc import CANONICAL_AMINO_ACIDS
from .verification import (
    ARTIFACT_INDEX_FILENAME,
    build_artifact_index,
    sha256_file,
    verify_run,
)


SEARCH_POLICY_SCHEMA = "vaxflow.stage2-search-policy.v1"
SEARCH_CONTEXT_SCHEMA = "vaxflow.stage2-search-context.v1"
EVIDENCE_BUNDLE_SCHEMA = "vaxflow.stage2-search-evidence.v1"
SEARCH_SUMMARY_SCHEMA = "vaxflow.stage2-search-summary.v1"
ATOMIC_SCHEMA = "vaxflow.stage2-atomic-components.v1"
POOL_SCHEMA = "vaxflow.stage2-search-pool.v1"
MATERIALIZED_PANEL_SCHEMA = "vaxflow.stage2-materialized-fusion-panel.v1"
SELECTION_SCHEMA = "vaxflow.stage3-selection.v1"
MODEL_JOBS_SCHEMA = "vaxflow.stage2-model-job-requests.v1"
SEARCH_GENERATOR_ID = "evidence-guided-multifamily-search"
SEARCH_GENERATOR_VERSION = "2"
SUPPORTED_EVIDENCE_STAGE = "developability_assessment"
RESIDUE_EVIDENCE_SCHEMA = "vaxflow.residue-evidence.v1"
STRUCTURE_ASSESSMENT_STAGE = "protein_structure_assessment"
APPROVAL_STATUSES = frozenset({"approved", "approved_for_mock_execution"})
ORDER_POLICIES = frozenset({"fixed", "all_permutations"})
EXPECTED_FILES = {
    "inputs/context.json",
    "inputs/evidence_bundle.json",
    "inputs/policy.json",
    "inputs/seed_candidate_batch.json",
    "atomic_components.json",
    "atomic_components.csv",
    "atomic_components.fasta",
    "candidate_pool.json",
    "candidate_pool.csv",
    "candidate_pool.fasta",
    "materialized_fusion_panel.json",
    "materialized_fusion_panel.csv",
    "materialized_fusion_panel.fasta",
    "candidate_specification.generated.json",
    "external_model_jobs.json",
    "report.html",
    "search_summary.json",
    "stage3_selection.fasta",
    "stage3_selection.json",
    ARTIFACT_INDEX_FILENAME,
}
KD_SCALE = {
    "I": 4.5,
    "V": 4.2,
    "L": 3.8,
    "F": 2.8,
    "C": 2.5,
    "M": 1.9,
    "A": 1.8,
    "G": -0.4,
    "T": -0.7,
    "S": -0.8,
    "W": -0.9,
    "Y": -1.3,
    "P": -1.6,
    "H": -3.2,
    "E": -3.5,
    "Q": -3.5,
    "D": -3.5,
    "N": -3.5,
    "K": -3.9,
    "R": -4.5,
}


def _strict_identifier(value: Any, field: str) -> str:
    identifier = _text(value, field)
    if not all(character.isalnum() or character in "._-" for character in identifier):
        raise ValueError(f"{field} contains unsupported characters: {identifier!r}")
    return identifier


def _positive_integer(value: Any, field: str, *, minimum: int = 1) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{field} must be an integer >= {minimum}")
    return value


def _finite_weight(value: Any, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return numeric


def _resolve_evidence_run(
    config: ProjectConfig,
    evidence_run_dir: str | Path,
) -> Path:
    directory = Path(evidence_run_dir).expanduser().resolve()
    if not directory.is_dir():
        raise ValueError(f"Stage 2 evidence run does not exist: {directory}")
    verification = verify_run(directory)
    if verification["status"] != "pass":
        raise ValueError(
            "Stage 2 evidence run verification failed: "
            + "; ".join(verification["errors"][:5])
        )
    manifest = _load_object(directory / "manifest.json", "evidence run manifest")
    if manifest.get("project_id") != config.project_id:
        raise ValueError("Stage 2 evidence run belongs to another project")
    if manifest.get("current_stage") != SUPPORTED_EVIDENCE_STAGE:
        raise ValueError(
            f"Stage 2 search requires a {SUPPORTED_EVIDENCE_STAGE} evidence run, got "
            f"{manifest.get('current_stage')}"
        )
    return directory


def _evidence_paths(directory: Path) -> dict[str, Path]:
    return {
        "candidate_batch": directory
        / "nodes"
        / CANDIDATE_STAGE_ID
        / "candidate_batch.json",
        "structure_assessments": directory
        / "nodes"
        / STRUCTURE_ASSESSMENT_STAGE
        / "structure_assessments.json",
        "transmembrane_topology": directory
        / "nodes"
        / SUPPORTED_EVIDENCE_STAGE
        / "inputs"
        / "adapter--transmembrane_topology.json",
        "signal_peptide": directory
        / "nodes"
        / SUPPORTED_EVIDENCE_STAGE
        / "inputs"
        / "adapter--signal_peptide.json",
        "disorder": directory
        / "nodes"
        / SUPPORTED_EVIDENCE_STAGE
        / "inputs"
        / "adapter--disorder.json",
        "mhc_binding": directory
        / "nodes"
        / "immune_evidence_assessment"
        / "inputs"
        / "adapter--mhc_binding.json",
    }


def _build_evidence_bundle(directory: Path) -> dict[str, Any]:
    manifest = _load_object(directory / "manifest.json", "evidence run manifest")
    paths = _evidence_paths(directory)
    missing = sorted(name for name, path in paths.items() if not path.is_file())
    if missing:
        raise ValueError(f"Evidence run is missing required artifacts: {missing}")
    documents = {
        name: _load_object(path, f"evidence {name}")
        for name, path in paths.items()
    }
    candidate_batch_sha = sha256_file(paths["candidate_batch"])
    candidate_batch = documents["candidate_batch"]
    candidates = candidate_batch.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("Evidence candidate batch has no candidates")
    by_id = {
        candidate.get("candidate_id"): candidate
        for candidate in candidates
        if isinstance(candidate, dict) and isinstance(candidate.get("candidate_id"), str)
    }
    if len(by_id) != len(candidates):
        raise ValueError("Evidence candidate IDs are missing or duplicated")

    for adapter_id in (
        "transmembrane_topology",
        "signal_peptide",
        "disorder",
        "mhc_binding",
    ):
        document = documents[adapter_id]
        if (
            document.get("schema_version") != RESIDUE_EVIDENCE_SCHEMA
            or document.get("adapter_id") != adapter_id
            or document.get("candidate_batch_sha256") != candidate_batch_sha
            or not isinstance(document.get("observations"), list)
        ):
            raise ValueError(f"Invalid or mismatched {adapter_id} evidence")
        for observation in document["observations"]:
            if not isinstance(observation, dict):
                raise ValueError(f"{adapter_id} contains a non-object observation")
            candidate = by_id.get(observation.get("candidate_id"))
            if candidate is None:
                raise ValueError(f"{adapter_id} references an unknown candidate")
            if observation.get("sequence_sha256") != candidate.get(
                "amino_acid_sha256"
            ):
                raise ValueError(f"{adapter_id} observation sequence hash mismatch")

    structure = documents["structure_assessments"]
    if (
        structure.get("stage_id") != STRUCTURE_ASSESSMENT_STAGE
        or structure.get("project_id") != manifest["project_id"]
        or not isinstance(structure.get("assessments"), list)
    ):
        raise ValueError("Invalid structure-assessment evidence")
    for assessment in structure["assessments"]:
        candidate = by_id.get(assessment.get("candidate_id"))
        if candidate is None or assessment.get("sequence_sha256") != candidate.get(
            "amino_acid_sha256"
        ):
            raise ValueError("Structure assessment candidate identity mismatch")

    return {
        "schema_version": EVIDENCE_BUNDLE_SCHEMA,
        "source": {
            "run_id": manifest["run_id"],
            "run_path": str(directory),
            "artifact_index_sha256": sha256_file(
                directory / ARTIFACT_INDEX_FILENAME
            ),
            "candidate_batch_sha256": candidate_batch_sha,
        },
        "documents": documents,
        "artifact_sha256": {
            name: sha256_file(path) for name, path in sorted(paths.items())
        },
    }


def _validate_policy(
    policy: dict[str, Any],
    *,
    project_id: str,
    round_id: str,
    source_ids: set[str],
) -> dict[str, Any]:
    if policy.get("schema_version") != SEARCH_POLICY_SCHEMA:
        raise ValueError(f"search policy schema_version must be {SEARCH_POLICY_SCHEMA}")
    if _text(policy.get("project_id"), "policy.project_id") != project_id:
        raise ValueError("search policy belongs to another project")
    if _text(policy.get("design_round_id"), "policy.design_round_id") != round_id:
        raise ValueError("search policy belongs to another design round")
    _strict_identifier(policy.get("policy_id"), "policy.policy_id")
    if _text(policy.get("status"), "policy.status") not in APPROVAL_STATUSES:
        raise ValueError("search policy is not approved for execution")

    boundary = policy.get("boundary_search")
    if not isinstance(boundary, dict):
        raise ValueError("policy.boundary_search must be an object")
    offsets = boundary.get("offsets")
    if (
        not isinstance(offsets, list)
        or not offsets
        or not all(isinstance(value, int) and not isinstance(value, bool) for value in offsets)
        or offsets != sorted(set(offsets))
    ):
        raise ValueError("boundary_search.offsets must be sorted unique integers")
    _positive_integer(
        boundary.get("minimum_segment_length"),
        "boundary_search.minimum_segment_length",
    )
    _positive_integer(
        boundary.get("maximum_variants_per_source"),
        "boundary_search.maximum_variants_per_source",
    )
    for field in ("exclude_signal_peptide_overlap", "exclude_transmembrane_overlap"):
        if not isinstance(boundary.get(field), bool):
            raise ValueError(f"boundary_search.{field} must be boolean")

    linkers = policy.get("linkers")
    if not isinstance(linkers, list) or not linkers:
        raise ValueError("policy.linkers must be a non-empty array")
    linker_ids: set[str] = set()
    for index, linker in enumerate(linkers):
        field = f"policy.linkers[{index}]"
        if not isinstance(linker, dict):
            raise ValueError(f"{field} must be an object")
        linker_id = _strict_identifier(linker.get("linker_id"), f"{field}.linker_id")
        if linker_id in linker_ids:
            raise ValueError(f"Duplicate linker ID: {linker_id}")
        linker_ids.add(linker_id)
        sequence = _text(linker.get("sequence", ""), f"{field}.sequence", allow_empty=True)
        if set(sequence) - CANONICAL_AMINO_ACIDS:
            raise ValueError(f"{field}.sequence contains non-canonical amino acids")
        _strict_identifier(linker.get("class"), f"{field}.class")
        _text(linker.get("rationale"), f"{field}.rationale")

    templates = policy.get("architecture_templates")
    if not isinstance(templates, list) or not templates:
        raise ValueError("policy.architecture_templates must be a non-empty array")
    template_ids: set[str] = set()
    for index, template in enumerate(templates):
        field = f"policy.architecture_templates[{index}]"
        if not isinstance(template, dict):
            raise ValueError(f"{field} must be an object")
        template_id = _strict_identifier(
            template.get("template_id"), f"{field}.template_id"
        )
        if template_id in template_ids:
            raise ValueError(f"Duplicate architecture template: {template_id}")
        template_ids.add(template_id)
        slots = template.get("source_slots")
        if not isinstance(slots, list) or len(slots) < 2:
            raise ValueError(f"{field}.source_slots must contain at least two entries")
        if not all(isinstance(source_id, str) and source_id in source_ids for source_id in slots):
            raise ValueError(f"{field}.source_slots references an unknown source")
        order_policy = _text(template.get("order_policy"), f"{field}.order_policy")
        if order_policy not in ORDER_POLICIES:
            raise ValueError(f"{field}.order_policy is unsupported")
        if not isinstance(template.get("require_distinct_same_source", True), bool):
            raise ValueError(f"{field}.require_distinct_same_source must be boolean")
        _text(template.get("rationale"), f"{field}.rationale")

    budgets = policy.get("budgets")
    if not isinstance(budgets, dict):
        raise ValueError("policy.budgets must be an object")
    for field in (
        "maximum_parent_orders_per_template",
        "maximum_linker_patterns_per_template",
        "maximum_scored_fusions_per_template",
        "maximum_materialized_fusions",
        "maximum_stage3_candidates",
        "maximum_baseline_generated_stage3_candidates",
        "maximum_external_model_parents",
    ):
        _positive_integer(budgets.get(field), f"budgets.{field}")
    if (
        budgets["maximum_baseline_generated_stage3_candidates"]
        > budgets["maximum_stage3_candidates"]
    ):
        raise ValueError("baseline Stage 3 budget exceeds total Stage 3 budget")
    if budgets["maximum_external_model_parents"] > budgets["maximum_stage3_candidates"]:
        raise ValueError("external-model parent budget exceeds Stage 3 budget")
    _positive_integer(policy.get("maximum_aa_length", 1024), "maximum_aa_length")

    objectives = policy.get("objective_weights")
    expected_objectives = {
        "mhc_retention",
        "structure_confidence",
        "disorder_avoidance",
        "manual_boundary_support",
        "compactness",
        "sequence_liability",
    }
    if not isinstance(objectives, dict) or set(objectives) != expected_objectives:
        raise ValueError(
            f"policy.objective_weights must contain {sorted(expected_objectives)}"
        )
    if sum(
        _finite_weight(value, f"objective_weights.{key}")
        for key, value in objectives.items()
    ) <= 0:
        raise ValueError("objective weights must contain at least one positive value")

    model_jobs = policy.get("external_model_jobs")
    if not isinstance(model_jobs, list) or not model_jobs:
        raise ValueError("policy.external_model_jobs must be a non-empty array")
    adapters: set[str] = set()
    for index, job in enumerate(model_jobs):
        field = f"policy.external_model_jobs[{index}]"
        if not isinstance(job, dict):
            raise ValueError(f"{field} must be an object")
        adapter_id = _strict_identifier(job.get("adapter_id"), f"{field}.adapter_id")
        if adapter_id in adapters:
            raise ValueError(f"Duplicate external-model adapter: {adapter_id}")
        adapters.add(adapter_id)
        _text(job.get("model_name"), f"{field}.model_name")
        _text(job.get("model_revision"), f"{field}.model_revision")
        status = _strict_identifier(job.get("status"), f"{field}.status")
        if status not in {"ready_for_external_execution", "blocked_on_stage3_structure"}:
            raise ValueError(f"{field}.status is unsupported")
        _positive_integer(job.get("variants_per_parent"), f"{field}.variants_per_parent")
        _positive_integer(job.get("junction_flank_residues"), f"{field}.junction_flank_residues", minimum=0)
        _positive_integer(job.get("maximum_substitutions"), f"{field}.maximum_substitutions")
    return policy


def _single_source_component(candidate: dict[str, Any]) -> dict[str, Any] | None:
    components = candidate.get("inferred_components")
    if not isinstance(components, list) or len(components) != 1:
        return None
    component = components[0]
    if not isinstance(component, dict) or component.get("component_type") != "source_segment":
        return None
    return component


def _map_candidate_interval(
    candidate: dict[str, Any],
    start: int,
    end: int,
) -> tuple[str, int, int] | None:
    for component in candidate.get("inferred_components", []):
        if component.get("component_type") != "source_segment":
            continue
        candidate_start = int(component["candidate_start"])
        candidate_end = int(component["candidate_end"])
        if candidate_start <= start <= end <= candidate_end:
            source_start = int(component["source_start"]) + start - candidate_start
            source_end = int(component["source_start"]) + end - candidate_start
            return str(component["source_protein_id"]), source_start, source_end
    return None


def _merge_intervals(intervals: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for start, end in sorted(set(intervals)):
        if not merged or start > merged[-1][1] + 1:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def _overlap_length(start: int, end: int, intervals: Iterable[tuple[int, int]]) -> int:
    return sum(
        max(0, min(end, interval_end) - max(start, interval_start) + 1)
        for interval_start, interval_end in _merge_intervals(intervals)
    )


def _build_source_profiles(
    seed_batch: dict[str, Any],
    evidence_bundle: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    seed_candidates = seed_batch["candidates"]
    seed_by_id = {candidate["candidate_id"]: candidate for candidate in seed_candidates}
    source_controls: dict[str, dict[str, Any]] = {}
    manual_ranges: dict[str, list[tuple[int, int, str]]] = {}
    for candidate in seed_candidates:
        component = _single_source_component(candidate)
        if component is None:
            continue
        source_id = str(component["source_protein_id"])
        if candidate["candidate_type"] == "source_control":
            source_controls[source_id] = candidate
        else:
            manual_ranges.setdefault(source_id, []).append(
                (
                    int(component["source_start"]),
                    int(component["source_end"]),
                    str(candidate["candidate_key"]),
                )
            )
    if not source_controls:
        raise ValueError("Seed batch has no source controls")

    evidence_candidates = evidence_bundle["documents"]["candidate_batch"]["candidates"]
    evidence_by_id = {
        candidate["candidate_id"]: candidate for candidate in evidence_candidates
    }
    for candidate_id, seed_candidate in seed_by_id.items():
        evidence_candidate = evidence_by_id.get(candidate_id)
        if evidence_candidate is not None and evidence_candidate.get(
            "amino_acid_sha256"
        ) != seed_candidate.get("amino_acid_sha256"):
            raise ValueError("Evidence and seed candidate identities disagree")

    profiles = {
        source_id: {
            "source_id": source_id,
            "candidate_key": candidate["candidate_key"],
            "candidate_id": candidate["candidate_id"],
            "sequence": candidate["amino_acid_sequence"],
            "length": len(candidate["amino_acid_sequence"]),
            "manual_ranges": sorted(manual_ranges.get(source_id, [])),
            "transmembrane_intervals": [],
            "signal_intervals": [],
            "disorder_intervals": [],
            "low_confidence_intervals": [],
            "mhc_epitopes": [],
            "evidence_ids": set(),
        }
        for source_id, candidate in source_controls.items()
    }

    documents = evidence_bundle["documents"]
    for adapter_id, profile_field in (
        ("transmembrane_topology", "transmembrane_intervals"),
        ("signal_peptide", "signal_intervals"),
        ("disorder", "disorder_intervals"),
    ):
        for observation in documents[adapter_id]["observations"]:
            candidate = evidence_by_id[observation["candidate_id"]]
            mapped = _map_candidate_interval(
                candidate,
                int(observation["residue_start"]),
                int(observation["residue_end"]),
            )
            if mapped is None or mapped[0] not in profiles:
                continue
            source_id, start, end = mapped
            profiles[source_id][profile_field].append((start, end))
            profiles[source_id]["evidence_ids"].add(observation["evidence_id"])

    structure = documents["structure_assessments"]
    for assessment in structure["assessments"]:
        candidate = evidence_by_id.get(assessment["candidate_id"])
        if candidate is None or candidate.get("candidate_type") != "source_control":
            continue
        component = _single_source_component(candidate)
        if component is None:
            continue
        source_id = str(component["source_protein_id"])
        for segment in assessment.get("low_confidence_segments", []):
            mapped = _map_candidate_interval(
                candidate,
                int(segment["start"]),
                int(segment["end"]),
            )
            if mapped is not None:
                profiles[source_id]["low_confidence_intervals"].append(
                    (mapped[1], mapped[2])
                )
        profiles[source_id]["structure_summary"] = {
            "candidate_id": candidate["candidate_id"],
            "mean_plddt": float(assessment["mean_plddt"]),
            "ptm": float(assessment["ptm"]),
        }

    epitope_keys: dict[str, set[tuple[Any, ...]]] = {
        source_id: set() for source_id in profiles
    }
    for observation in documents["mhc_binding"]["observations"]:
        if observation.get("status") != "supported":
            continue
        candidate = evidence_by_id[observation["candidate_id"]]
        mapped = _map_candidate_interval(
            candidate,
            int(observation["residue_start"]),
            int(observation["residue_end"]),
        )
        if mapped is None or mapped[0] not in profiles:
            continue
        source_id, start, end = mapped
        key = (
            start,
            end,
            observation.get("mhc_class"),
            observation.get("allele"),
            observation.get("peptide"),
        )
        if key in epitope_keys[source_id]:
            continue
        epitope_keys[source_id].add(key)
        profiles[source_id]["mhc_epitopes"].append(
            {
                "start": start,
                "end": end,
                "mhc_class": observation.get("mhc_class"),
                "allele": observation.get("allele"),
                "peptide": observation.get("peptide"),
                "binding_level": observation.get("binding_level"),
                "evidence_id": observation.get("evidence_id"),
            }
        )
        profiles[source_id]["evidence_ids"].add(observation["evidence_id"])

    for profile in profiles.values():
        for field in (
            "transmembrane_intervals",
            "signal_intervals",
            "disorder_intervals",
            "low_confidence_intervals",
        ):
            profile[field] = _merge_intervals(profile[field])
        profile["mhc_epitopes"] = sorted(
            profile["mhc_epitopes"],
            key=lambda item: (
                item["start"],
                item["end"],
                str(item["mhc_class"]),
                str(item["allele"]),
                str(item["peptide"]),
            ),
        )
        profile["evidence_ids"] = sorted(profile["evidence_ids"])
    return profiles


def _boundary_anchors(profile: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    starts = [{"position": 1, "source": "sequence_terminal", "evidence_id": None}]
    ends = [
        {
            "position": profile["length"],
            "source": "sequence_terminal",
            "evidence_id": None,
        }
    ]
    for start, end, candidate_key in profile["manual_ranges"]:
        starts.append(
            {
                "position": start,
                "source": "manual_seed_boundary",
                "evidence_id": candidate_key,
            }
        )
        ends.append(
            {
                "position": end,
                "source": "manual_seed_boundary",
                "evidence_id": candidate_key,
            }
        )
    for start, end in profile["signal_intervals"]:
        starts.append(
            {
                "position": end + 1,
                "source": "signal_peptide_boundary",
                "evidence_id": f"signal:{start}-{end}",
            }
        )
    for start, end in profile["transmembrane_intervals"]:
        ends.append(
            {
                "position": start - 1,
                "source": "transmembrane_boundary",
                "evidence_id": f"tm:{start}-{end}",
            }
        )
        starts.append(
            {
                "position": end + 1,
                "source": "transmembrane_boundary",
                "evidence_id": f"tm:{start}-{end}",
            }
        )
    for field, source in (
        ("low_confidence_intervals", "structure_low_confidence_boundary"),
        ("disorder_intervals", "disorder_boundary"),
    ):
        for start, end in profile[field]:
            if start > 1:
                ends.append(
                    {
                        "position": start - 1,
                        "source": source,
                        "evidence_id": f"{source}:{start}-{end}",
                    }
                )
            if end < profile["length"]:
                starts.append(
                    {
                        "position": end + 1,
                        "source": source,
                        "evidence_id": f"{source}:{start}-{end}",
                    }
                )
    return starts, ends


def _interval_feature(
    profile: dict[str, Any],
    start: int,
    end: int,
    start_anchor: dict[str, Any],
    end_anchor: dict[str, Any],
    weights: dict[str, Any],
) -> dict[str, Any]:
    length = end - start + 1
    epitopes = profile["mhc_epitopes"]
    retained = sum(
        start <= epitope["start"] and epitope["end"] <= end
        for epitope in epitopes
    )
    mhc_retention = retained / len(epitopes) if epitopes else 0.5
    low_confidence = _overlap_length(
        start, end, profile["low_confidence_intervals"]
    )
    disorder = _overlap_length(start, end, profile["disorder_intervals"])
    structure_confidence = 1.0 - low_confidence / length
    disorder_avoidance = 1.0 - disorder / length
    manual_distances = [
        abs(start - manual_start) + abs(end - manual_end)
        for manual_start, manual_end, _ in profile["manual_ranges"]
    ]
    manual_support = (
        1.0 / (1.0 + min(manual_distances) / 12.0)
        if manual_distances
        else 0.5
    )
    compactness = 1.0 - length / profile["length"]
    values = {
        "mhc_retention": mhc_retention,
        "structure_confidence": structure_confidence,
        "disorder_avoidance": disorder_avoidance,
        "manual_boundary_support": manual_support,
        "compactness": compactness,
    }
    denominator = sum(
        float(weights[key])
        for key in values
        if float(weights[key]) > 0
    )
    score = (
        sum(float(weights[key]) * value for key, value in values.items()) / denominator
        if denominator
        else 0.0
    )
    return {
        **{key: round(value, 6) for key, value in values.items()},
        "supported_mhc_epitopes": retained,
        "total_supported_mhc_epitopes": len(epitopes),
        "low_confidence_residues": low_confidence,
        "disorder_residues": disorder,
        "boundary_sources": [start_anchor, end_anchor],
        "atomic_priority_proxy": round(score, 6),
    }


def _round_robin_select(
    records: list[dict[str, Any]],
    *,
    limit: int,
    group_key: Any,
    score_key: str,
) -> list[dict[str, Any]]:
    def score(record: dict[str, Any]) -> float:
        value: Any = record
        for part in score_key.split("."):
            value = value[part]
        return float(value)

    groups: dict[Any, list[dict[str, Any]]] = {}
    for record in records:
        groups.setdefault(group_key(record), []).append(record)
    for values in groups.values():
        values.sort(
            key=lambda item: (
                -score(item),
                str(item.get("candidate_key", item.get("configuration_id", ""))),
            )
        )
    selected: list[dict[str, Any]] = []
    ordered_groups = sorted(groups, key=lambda value: str(value))
    while ordered_groups and len(selected) < limit:
        next_groups = []
        for key in ordered_groups:
            if len(selected) >= limit:
                break
            values = groups[key]
            if values:
                selected.append(values.pop(0))
            if values:
                next_groups.append(key)
        ordered_groups = next_groups
    return selected


def _generate_atomic_components(
    profiles: dict[str, dict[str, Any]],
    seed_batch: dict[str, Any],
    policy: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    boundary = policy["boundary_search"]
    weights = policy["objective_weights"]
    offsets = boundary["offsets"]
    minimum_length = int(boundary["minimum_segment_length"])
    maximum_per_source = int(boundary["maximum_variants_per_source"])
    seed_sequences = {
        candidate["amino_acid_sha256"]: candidate["candidate_key"]
        for candidate in seed_batch["candidates"]
    }
    generated: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    options_by_source: dict[str, list[dict[str, Any]]] = {
        source_id: [] for source_id in profiles
    }

    for candidate in seed_batch["candidates"]:
        component = _single_source_component(candidate)
        if component is None or candidate["candidate_type"] == "source_control":
            continue
        source_id = str(component["source_protein_id"])
        profile = profiles[source_id]
        start = int(component["source_start"])
        end = int(component["source_end"])
        exclusion_reason = None
        if boundary["exclude_transmembrane_overlap"] and _overlap_length(
            start, end, profile["transmembrane_intervals"]
        ):
            exclusion_reason = "seed_manual_overlaps_transmembrane_interval"
        elif boundary["exclude_signal_peptide_overlap"] and _overlap_length(
            start, end, profile["signal_intervals"]
        ):
            exclusion_reason = "seed_manual_overlaps_signal_peptide_interval"
        if exclusion_reason is not None:
            skipped.append(
                {
                    "candidate_key": candidate["candidate_key"],
                    "source_id": source_id,
                    "source_start": start,
                    "source_end": end,
                    "amino_acid_sha256": candidate["amino_acid_sha256"],
                    "reason": exclusion_reason,
                }
            )
            continue
        feature = _interval_feature(
            profile,
            start,
            end,
            {"position": start, "source": "manual_seed", "evidence_id": candidate["candidate_key"]},
            {"position": end, "source": "manual_seed", "evidence_id": candidate["candidate_key"]},
            weights,
        )
        options_by_source[source_id].append(
            {
                "candidate_key": candidate["candidate_key"],
                "source_id": source_id,
                "source_start": start,
                "source_end": end,
                "aa_length": len(candidate["amino_acid_sequence"]),
                "amino_acid_sequence": candidate["amino_acid_sequence"],
                "amino_acid_sha256": candidate["amino_acid_sha256"],
                "origin": "seed_manual_segment",
                "features": feature,
            }
        )

    for source_id, profile in sorted(profiles.items()):
        starts, ends = _boundary_anchors(profile)
        range_candidates: dict[tuple[int, int], dict[str, Any]] = {}
        for start_anchor, end_anchor, start_offset, end_offset in product(
            starts,
            ends,
            offsets,
            offsets,
        ):
            start = int(start_anchor["position"]) + start_offset
            end = int(end_anchor["position"]) + end_offset
            if start < 1 or end > profile["length"] or end - start + 1 < minimum_length:
                continue
            if boundary["exclude_transmembrane_overlap"] and _overlap_length(
                start, end, profile["transmembrane_intervals"]
            ):
                continue
            if boundary["exclude_signal_peptide_overlap"] and _overlap_length(
                start, end, profile["signal_intervals"]
            ):
                continue
            feature = _interval_feature(
                profile,
                start,
                end,
                {**start_anchor, "offset": start_offset},
                {**end_anchor, "offset": end_offset},
                weights,
            )
            key = (start, end)
            previous = range_candidates.get(key)
            if previous is None or feature["atomic_priority_proxy"] > previous["features"][
                "atomic_priority_proxy"
            ]:
                sequence = profile["sequence"][start - 1 : end]
                range_candidates[key] = {
                    "source_id": source_id,
                    "source_start": start,
                    "source_end": end,
                    "aa_length": len(sequence),
                    "amino_acid_sequence": sequence,
                    "amino_acid_sha256": _sequence_sha256(sequence),
                    "origin": "evidence_guided_boundary_search",
                    "features": feature,
                }
        eligible = []
        for record in range_candidates.values():
            duplicate = seed_sequences.get(record["amino_acid_sha256"])
            if duplicate is not None:
                skipped.append(
                    {
                        **{key: value for key, value in record.items() if key != "amino_acid_sequence"},
                        "reason": "duplicate_seed_sequence",
                        "duplicate_of": duplicate,
                    }
                )
                continue
            identity = _document_sha256(
                {
                    "source_id": source_id,
                    "source_start": record["source_start"],
                    "source_end": record["source_end"],
                    "amino_acid_sha256": record["amino_acid_sha256"],
                }
            )
            record["candidate_key"] = (
                f"search-seg-{source_id.lower()}-{record['source_start']}-"
                f"{record['source_end']}-{identity[:8]}"
            )
            eligible.append(record)
        selected = _round_robin_select(
            eligible,
            limit=maximum_per_source,
            group_key=lambda item: (
                item["features"]["boundary_sources"][0]["source"],
                item["features"]["boundary_sources"][1]["source"],
                item["source_start"] // 6,
                item["source_end"] // 6,
            ),
            score_key="features.atomic_priority_proxy",
        )
        selected.sort(key=lambda item: item["candidate_key"])
        generated.extend(selected)
        options_by_source[source_id].extend(selected)

    for options in options_by_source.values():
        options.sort(
            key=lambda item: (
                -item["features"]["atomic_priority_proxy"],
                item["candidate_key"],
            )
        )
    generated.sort(key=lambda item: item["candidate_key"])
    skipped.sort(
        key=lambda item: (
            item["source_id"],
            item["source_start"],
            item["source_end"],
        )
    )
    return generated, skipped, options_by_source


def _linker_patterns(
    linkers: list[dict[str, Any]],
    junction_count: int,
    limit: int,
) -> list[tuple[str, ...]]:
    linker_ids = [linker["linker_id"] for linker in linkers]
    patterns = list(product(linker_ids, repeat=junction_count))
    homogeneous = {tuple([linker_id] * junction_count) for linker_id in linker_ids}
    patterns.sort(
        key=lambda pattern: (
            0 if pattern in homogeneous else 1,
            -len(set(pattern)),
            hashlib.sha256("|".join(pattern).encode("ascii")).hexdigest(),
        )
    )
    return patterns[:limit]


def _parent_orders(
    template: dict[str, Any],
    options_by_source: dict[str, list[dict[str, Any]]],
    limit: int,
) -> list[tuple[dict[str, Any], ...]]:
    slot_options = [options_by_source[source_id] for source_id in template["source_slots"]]
    orders: dict[tuple[str, ...], tuple[dict[str, Any], ...]] = {}
    for selected in product(*slot_options):
        keys = [item["candidate_key"] for item in selected]
        if template.get("require_distinct_same_source", True) and len(keys) != len(set(keys)):
            continue
        variants = [selected]
        if template["order_policy"] == "all_permutations":
            variants = permutations(selected)
        for variant in variants:
            key = tuple(item["candidate_key"] for item in variant)
            orders[key] = tuple(variant)
    ranked = sorted(
        orders.values(),
        key=lambda values: (
            -sum(item["features"]["atomic_priority_proxy"] for item in values)
            / len(values),
            hashlib.sha256(
                "|".join(item["candidate_key"] for item in values).encode("ascii")
            ).hexdigest(),
        ),
    )
    return ranked[:limit]


def _maximum_homopolymer(sequence: str) -> int:
    maximum = 0
    current = 0
    previous = ""
    for residue in sequence:
        current = current + 1 if residue == previous else 1
        previous = residue
        maximum = max(maximum, current)
    return maximum


def _sequence_liability_score(sequence: str) -> dict[str, Any]:
    window = 19
    hydrophobic_windows = 0
    if len(sequence) >= window:
        hydrophobic_windows = sum(
            sum(KD_SCALE[residue] for residue in sequence[index : index + window])
            / window
            >= 1.6
            for index in range(len(sequence) - window + 1)
        )
    homopolymer = _maximum_homopolymer(sequence)
    normalized = min(1.0, hydrophobic_windows / 10.0 + max(0, homopolymer - 3) / 6.0)
    return {
        "hydrophobic_window_count": hydrophobic_windows,
        "maximum_homopolymer": homopolymer,
        "sequence_liability": round(1.0 - normalized, 6),
    }


def _fusion_feature(
    parents: tuple[dict[str, Any], ...],
    linker_records: list[dict[str, Any]],
    sequence: str,
    maximum_length: int,
    weights: dict[str, Any],
) -> dict[str, Any]:
    lengths = [parent["aa_length"] for parent in parents]
    total_parent_length = sum(lengths)
    values = {}
    for key in (
        "mhc_retention",
        "structure_confidence",
        "disorder_avoidance",
        "manual_boundary_support",
    ):
        values[key] = sum(
            parent["features"][key] * parent["aa_length"] for parent in parents
        ) / total_parent_length
    values["compactness"] = 1.0 - len(sequence) / maximum_length
    liability = _sequence_liability_score(sequence)
    values["sequence_liability"] = liability["sequence_liability"]
    denominator = sum(float(weights[key]) for key in values if float(weights[key]) > 0)
    proxy = (
        sum(float(weights[key]) * value for key, value in values.items()) / denominator
        if denominator
        else 0.0
    )
    return {
        **{key: round(value, 6) for key, value in values.items()},
        **liability,
        "linker_residues": sum(len(linker["sequence"]) for linker in linker_records),
        "linker_classes": [linker["class"] for linker in linker_records],
        "fusion_priority_proxy": round(proxy, 6),
    }


def _generate_fusion_pool(
    options_by_source: dict[str, list[dict[str, Any]]],
    seed_batch: dict[str, Any],
    atomic_generated: list[dict[str, Any]],
    policy: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    budgets = policy["budgets"]
    linkers = policy["linkers"]
    linker_by_id = {linker["linker_id"]: linker for linker in linkers}
    maximum_length = int(policy.get("maximum_aa_length", 1024))
    seen_sequences = {
        candidate["amino_acid_sha256"]: candidate["candidate_key"]
        for candidate in seed_batch["candidates"]
    }
    seen_sequences.update(
        {
            record["amino_acid_sha256"]: record["candidate_key"]
            for record in atomic_generated
        }
    )
    eligible: list[dict[str, Any]] = []
    statistics = {
        "configuration_space_considered": 0,
        "configuration_budgeted": 0,
        "length_filtered": 0,
        "duplicate_filtered": 0,
        "by_template": {},
    }
    for template in policy["architecture_templates"]:
        template_id = template["template_id"]
        parents = _parent_orders(
            template,
            options_by_source,
            int(budgets["maximum_parent_orders_per_template"]),
        )
        patterns = _linker_patterns(
            linkers,
            len(template["source_slots"]) - 1,
            int(budgets["maximum_linker_patterns_per_template"]),
        )
        configurations = [(parent_order, pattern) for parent_order in parents for pattern in patterns]
        statistics["configuration_space_considered"] += len(configurations)
        configurations.sort(
            key=lambda item: hashlib.sha256(
                (
                    template_id
                    + "|"
                    + "|".join(parent["candidate_key"] for parent in item[0])
                    + "|"
                    + "|".join(item[1])
                ).encode("ascii")
            ).hexdigest()
        )
        configurations = configurations[
            : int(budgets["maximum_scored_fusions_per_template"])
        ]
        statistics["configuration_budgeted"] += len(configurations)
        template_eligible = 0
        for parent_order, pattern in configurations:
            linker_records = [linker_by_id[linker_id] for linker_id in pattern]
            chunks = []
            for index, parent in enumerate(parent_order):
                chunks.append(parent["amino_acid_sequence"])
                if index < len(linker_records):
                    chunks.append(linker_records[index]["sequence"])
            sequence = "".join(chunks)
            if len(sequence) > maximum_length:
                statistics["length_filtered"] += 1
                continue
            sequence_sha = _sequence_sha256(sequence)
            if sequence_sha in seen_sequences:
                statistics["duplicate_filtered"] += 1
                continue
            configuration_identity = _document_sha256(
                {
                    "template_id": template_id,
                    "parents": [parent["candidate_key"] for parent in parent_order],
                    "linkers": list(pattern),
                    "amino_acid_sha256": sequence_sha,
                }
            )
            candidate_key = f"search-{template_id}-{configuration_identity[:14]}"
            feature = _fusion_feature(
                parent_order,
                linker_records,
                sequence,
                maximum_length,
                policy["objective_weights"],
            )
            record = {
                "candidate_key": candidate_key,
                "configuration_id": configuration_identity,
                "template_id": template_id,
                "ordered_parent_keys": [
                    parent["candidate_key"] for parent in parent_order
                ],
                "ordered_source_ids": [parent["source_id"] for parent in parent_order],
                "linker_ids": list(pattern),
                "linker_sequences": [linker["sequence"] for linker in linker_records],
                "linker_classes": [linker["class"] for linker in linker_records],
                "amino_acid_sequence": sequence,
                "amino_acid_sha256": sequence_sha,
                "aa_length": len(sequence),
                "features": feature,
                "rationale": template["rationale"],
                "parent_components": [
                    {
                        key: parent[key]
                        for key in (
                            "candidate_key",
                            "source_id",
                            "source_start",
                            "source_end",
                            "origin",
                        )
                    }
                    for parent in parent_order
                ],
            }
            eligible.append(record)
            seen_sequences[sequence_sha] = candidate_key
            template_eligible += 1
        statistics["by_template"][template_id] = {
            "parent_orders": len(parents),
            "linker_patterns": len(patterns),
            "eligible": template_eligible,
        }

    selected = _round_robin_select(
        eligible,
        limit=int(budgets["maximum_materialized_fusions"]),
        group_key=lambda item: (
            item["template_id"],
            tuple(item["ordered_source_ids"]),
            tuple(item["linker_classes"]),
        ),
        score_key="features.fusion_priority_proxy",
    )
    selected.sort(key=lambda item: item["candidate_key"])
    eligible.sort(key=lambda item: item["candidate_key"])
    statistics["eligible_unique_fusions"] = len(eligible)
    statistics["materialized_fusions"] = len(selected)
    return eligible, selected, statistics


def _seed_records(seed_batch: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    source_controls = []
    manual = []
    for candidate in seed_batch["candidates"]:
        if candidate["candidate_type"] == "source_control":
            component = _single_source_component(candidate)
            if component is None:
                raise ValueError("Source control has no exact source component")
            source_controls.append(component["source_protein_id"])
        else:
            manual.append(_manual_record_from_seed(candidate))
    return source_controls, manual


def _candidate_specification(
    context: dict[str, Any],
    policy: dict[str, Any],
    seed_batch: dict[str, Any],
    atomic: list[dict[str, Any]],
    fusions: list[dict[str, Any]],
    identity: str,
) -> dict[str, Any]:
    source_controls, manual = _seed_records(seed_batch)
    for record in atomic:
        manual.append(
            {
                "candidate_key": record["candidate_key"],
                "display_name": (
                    f"Evidence-guided {record['source_id']} segment "
                    f"{record['source_start']}-{record['source_end']}"
                ),
                "candidate_type": "truncation",
                "amino_acid_sequence": record["amino_acid_sequence"],
                "claimed_source_id": record["source_id"],
                "claimed_source_start": record["source_start"],
                "claimed_source_end": record["source_end"],
                "annotation_status": "unreviewed",
                "proposal": {
                    "generator": {
                        "id": "evidence-guided-boundary-search",
                        "version": SEARCH_GENERATOR_VERSION,
                        "parameters": {
                            "search_identity": identity,
                            "features": record["features"],
                        },
                    },
                    "parent_candidate_keys": [f"source-{record['source_id']}"],
                    "transformation": "evidence_guided_truncation",
                    "rationale": (
                        "Explore a topology-safe boundary hypothesis derived from "
                        "versioned manual, structure, topology, disorder, and MHC evidence."
                    ),
                    "feedback_request_ids": [],
                },
            }
        )
    for record in fusions:
        manual.append(
            {
                "candidate_key": record["candidate_key"],
                "display_name": (
                    f"Search {record['template_id']}: "
                    + " -> ".join(record["ordered_parent_keys"])
                    + " ["
                    + ",".join(record["linker_ids"])
                    + "]"
                ),
                "candidate_type": "fusion",
                "amino_acid_sequence": record["amino_acid_sequence"],
                "claimed_component_keys": record["ordered_parent_keys"],
                "annotation_status": "unreviewed",
                "proposal": {
                    "generator": {
                        "id": SEARCH_GENERATOR_ID,
                        "version": SEARCH_GENERATOR_VERSION,
                        "parameters": {
                            "search_identity": identity,
                            "template_id": record["template_id"],
                            "linker_ids": record["linker_ids"],
                            "linker_sequences": record["linker_sequences"],
                            "features": record["features"],
                        },
                    },
                    "parent_candidate_keys": record["ordered_parent_keys"],
                    "transformation": "ordered_component_concatenation",
                    "rationale": record["rationale"],
                    "feedback_request_ids": [],
                },
            }
        )
    return {
        "schema_version": 1,
        "specification_id": f"{policy['policy_id']}-{identity[:12]}",
        "batch_label": policy.get(
            "batch_label",
            f"Evidence-guided Stage 2 search {identity[:12]}",
        ),
        "design_round_id": seed_batch["design_round_id"],
        "release_mode": "provisional",
        "include_source_controls": source_controls,
        "manual_candidates": manual,
        "generation_grammar": {
            "status": "approved",
            "approval_scope": policy["status"],
            "generate_new_candidates": False,
            "structure_max_length": int(policy.get("maximum_aa_length", 1024)),
            "materialized_generator": {
                "id": SEARCH_GENERATOR_ID,
                "version": SEARCH_GENERATOR_VERSION,
                "search_identity": identity,
                "policy_id": policy["policy_id"],
            },
        },
    }


def _selection_records(
    seed_batch: dict[str, Any],
    atomic: list[dict[str, Any]],
    fusions: list[dict[str, Any]],
    policy: dict[str, Any],
    identity: str,
) -> dict[str, Any]:
    budget = int(policy["budgets"]["maximum_stage3_candidates"])
    baseline_generated_budget = int(
        policy["budgets"]["maximum_baseline_generated_stage3_candidates"]
    )
    mandatory = [
        {
            "candidate_key": candidate["candidate_key"],
            "amino_acid_sha256": candidate["amino_acid_sha256"],
            "aa_length": len(candidate["amino_acid_sequence"]),
            "selection_tier": "baseline_source_or_manual",
            "priority_proxy": None,
        }
        for candidate in seed_batch["candidates"]
        if candidate.get("duplicate_of") is None
        and candidate.get("proposal", {}).get("generator", {}).get("id")
        in {"source_intake", "manual_import"}
    ]
    baseline_generated = []
    for candidate in seed_batch["candidates"]:
        generator = candidate.get("proposal", {}).get("generator", {})
        if (
            candidate.get("duplicate_of") is not None
            or generator.get("id") in {"source_intake", "manual_import"}
        ):
            continue
        parameters = generator.get("parameters", {})
        baseline_generated.append(
            {
                "candidate_key": candidate["candidate_key"],
                "amino_acid_sha256": candidate["amino_acid_sha256"],
                "aa_length": len(candidate["amino_acid_sequence"]),
                "selection_tier": "baseline_generated_panel",
                "priority_proxy": None,
                "selection_priority": 0.0,
                "diversity_group": (
                    str(generator.get("id", "unknown")),
                    str(parameters.get("template_id", candidate["candidate_type"])),
                    str(parameters.get("linker_id", "unspecified")),
                ),
            }
        )
    selected_baseline = _round_robin_select(
        baseline_generated,
        limit=min(baseline_generated_budget, len(baseline_generated)),
        group_key=lambda item: item["diversity_group"],
        score_key="selection_priority",
    )
    for record in selected_baseline:
        record.pop("selection_priority")
        record.pop("diversity_group")
    mandatory.extend(selected_baseline)
    mandatory.extend(
        {
            "candidate_key": record["candidate_key"],
            "amino_acid_sha256": record["amino_acid_sha256"],
            "aa_length": record["aa_length"],
            "selection_tier": "atomic_boundary_panel",
            "priority_proxy": record["features"]["atomic_priority_proxy"],
        }
        for record in atomic
    )
    if len(mandatory) > budget:
        raise ValueError("Stage 3 budget is smaller than mandatory controls/atomic panel")
    selected_fusions = _round_robin_select(
        fusions,
        limit=budget - len(mandatory),
        group_key=lambda item: (
            item["template_id"],
            tuple(item["ordered_source_ids"]),
            tuple(item["linker_classes"]),
        ),
        score_key="features.fusion_priority_proxy",
    )
    records = mandatory + [
        {
            "candidate_key": record["candidate_key"],
            "amino_acid_sha256": record["amino_acid_sha256"],
            "aa_length": record["aa_length"],
            "selection_tier": "multifamily_fusion_panel",
            "priority_proxy": record["features"]["fusion_priority_proxy"],
        }
        for record in selected_fusions
    ]
    return {
        "schema_version": SELECTION_SCHEMA,
        "selection_id": _document_sha256(
            {
                "search_identity": identity,
                "records": records,
                "budget": budget,
            }
        ),
        "project_id": policy["project_id"],
        "design_round_id": policy["design_round_id"],
        "search_identity": identity,
        "strategy": "mandatory-controls-plus-stratified-proxy-diversity",
        "budget": budget,
        "records": records,
        "limitations": [
            "Priority proxies rank computational hypotheses, not vaccine efficacy.",
            "All baseline candidates remain in the candidate pool; only a versioned diversity subset consumes Stage 3 folding budget.",
            "Selection preserves architecture/linker diversity before expensive folding.",
        ],
    }


def _model_jobs(
    fusions: list[dict[str, Any]],
    selection: dict[str, Any],
    policy: dict[str, Any],
    profiles: dict[str, dict[str, Any]],
    identity: str,
) -> dict[str, Any]:
    selected_keys = {
        record["candidate_key"]
        for record in selection["records"]
        if record["selection_tier"] == "multifamily_fusion_panel"
    }
    candidates = [record for record in fusions if record["candidate_key"] in selected_keys]
    candidates.sort(
        key=lambda item: (
            -item["features"]["fusion_priority_proxy"],
            item["candidate_key"],
        )
    )
    candidates = candidates[: int(policy["budgets"]["maximum_external_model_parents"])]
    jobs = []
    for job_policy in policy["external_model_jobs"]:
        job_records = []
        flank = int(job_policy["junction_flank_residues"])
        for candidate in candidates:
            mutable: set[int] = set()
            protected: set[int] = set()
            offset = 0
            for parent_index, parent in enumerate(candidate["parent_components"]):
                parent_length = parent["source_end"] - parent["source_start"] + 1
                parent_start = offset + 1
                parent_end = offset + parent_length
                if parent_index > 0:
                    mutable.update(range(parent_start, min(parent_end, parent_start + flank - 1) + 1))
                if parent_index < len(candidate["parent_components"]) - 1:
                    mutable.update(range(max(parent_start, parent_end - flank + 1), parent_end + 1))
                source_profile = profiles[parent["source_id"]]
                for epitope in source_profile["mhc_epitopes"]:
                    if (
                        parent["source_start"] <= epitope["start"]
                        and epitope["end"] <= parent["source_end"]
                    ):
                        protected.update(
                            range(
                                parent_start + epitope["start"] - parent["source_start"],
                                parent_start + epitope["end"] - parent["source_start"] + 1,
                            )
                        )
                offset = parent_end
                if parent_index < len(candidate["linker_sequences"]):
                    linker_length = len(candidate["linker_sequences"][parent_index])
                    mutable.update(range(offset + 1, offset + linker_length + 1))
                    offset += linker_length
            protected.update(
                position
                for position, residue in enumerate(candidate["amino_acid_sequence"], 1)
                if residue == "C"
            )
            mutable -= protected
            job_records.append(
                {
                    "parent_candidate_key": candidate["candidate_key"],
                    "sequence": candidate["amino_acid_sequence"],
                    "sequence_sha256": candidate["amino_acid_sha256"],
                    "mutable_positions": sorted(mutable),
                    "protected_positions": sorted(protected),
                    "maximum_substitutions": min(
                        int(job_policy["maximum_substitutions"]),
                        len(mutable),
                    ),
                }
            )
        job = {
            "job_id": f"{job_policy['adapter_id']}-{identity[:12]}",
            "adapter_id": job_policy["adapter_id"],
            "model": {
                "name": job_policy["model_name"],
                "revision": job_policy["model_revision"],
            },
            "status": job_policy["status"],
            "search_identity": identity,
            "transformation": "constrained_substitution",
            "variants_per_parent": job_policy["variants_per_parent"],
            "records": job_records,
            "result_schema": "vaxflow.stage2-external-proposals.v1",
        }
        job["job_identity"] = _document_sha256(job)
        jobs.append(job)
    return {
        "schema_version": MODEL_JOBS_SCHEMA,
        "project_id": policy["project_id"],
        "design_round_id": policy["design_round_id"],
        "search_identity": identity,
        "jobs": jobs,
        "limitations": [
            "A job request is not evidence that a model executed.",
            "Source cysteines and residues covered by supported MHC predictions are protected.",
            "ProteinMPNN jobs require the matching Stage 3 predicted structure before execution.",
        ],
    }


def _search_identity(
    context: dict[str, Any],
    policy: dict[str, Any],
    atomic: list[dict[str, Any]],
    eligible_fusions: list[dict[str, Any]],
    fusions: list[dict[str, Any]],
) -> str:
    def without_sequence(record: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in record.items()
            if key != "amino_acid_sequence"
        }

    return _document_sha256(
        {
            "schema_version": SEARCH_SUMMARY_SCHEMA,
            "context": context,
            "policy": policy,
            "atomic": [without_sequence(record) for record in atomic],
            "eligible_fusion_pool_sha256": _document_sha256(
                [without_sequence(record) for record in eligible_fusions]
            ),
            "fusions": [without_sequence(record) for record in fusions],
        }
    )


def _build_search(
    context: dict[str, Any],
    policy: dict[str, Any],
    seed_batch: dict[str, Any],
    evidence_bundle: dict[str, Any],
) -> dict[str, Any]:
    if context.get("schema_version") != SEARCH_CONTEXT_SCHEMA:
        raise ValueError("Unsupported Stage 2 search context")
    if evidence_bundle.get("schema_version") != EVIDENCE_BUNDLE_SCHEMA:
        raise ValueError("Unsupported Stage 2 evidence bundle")
    candidates = seed_batch.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("Seed candidate batch has no candidates")
    profiles = _build_source_profiles(seed_batch, evidence_bundle)
    _validate_policy(
        policy,
        project_id=context["project_id"],
        round_id=seed_batch["design_round_id"],
        source_ids=set(profiles),
    )
    atomic, atomic_skipped, options_by_source = _generate_atomic_components(
        profiles,
        seed_batch,
        policy,
    )
    eligible_fusions, fusions, search_statistics = _generate_fusion_pool(
        options_by_source,
        seed_batch,
        atomic,
        policy,
    )
    identity = _search_identity(
        context,
        policy,
        atomic,
        eligible_fusions,
        fusions,
    )
    specification = _candidate_specification(
        context,
        policy,
        seed_batch,
        atomic,
        fusions,
        identity,
    )
    selection = _selection_records(
        seed_batch,
        atomic,
        fusions,
        policy,
        identity,
    )
    model_jobs = _model_jobs(
        fusions,
        selection,
        policy,
        profiles,
        identity,
    )
    summary = {
        "schema_version": SEARCH_SUMMARY_SCHEMA,
        "search_identity": identity,
        "project_id": context["project_id"],
        "design_round_id": seed_batch["design_round_id"],
        "status": "materialized_for_mock_evaluation",
        "generator": {
            "id": SEARCH_GENERATOR_ID,
            "version": SEARCH_GENERATOR_VERSION,
        },
        "counts": {
            "seed_candidates": len(candidates),
            "generated_atomic_components": len(atomic),
            "skipped_atomic_hypotheses": len(atomic_skipped),
            "skipped_atomic_hypotheses_by_reason": {
                reason: sum(record["reason"] == reason for record in atomic_skipped)
                for reason in sorted({record["reason"] for record in atomic_skipped})
            },
            "materialized_fusions": len(fusions),
            "eligible_unique_fusions": len(eligible_fusions),
            "total_candidate_specification_records": len(candidates)
            + len(atomic)
            + len(fusions),
            "stage3_selected_candidates": len(selection["records"]),
            "external_model_jobs": len(model_jobs["jobs"]),
            "stage3_selection_tiers": {
                tier: sum(
                    record["selection_tier"] == tier
                    for record in selection["records"]
                )
                for tier in sorted(
                    {record["selection_tier"] for record in selection["records"]}
                )
            },
        },
        "search_statistics": search_statistics,
        "source_profiles": {
            source_id: {
                "length": profile["length"],
                "manual_ranges": profile["manual_ranges"],
                "transmembrane_intervals": profile["transmembrane_intervals"],
                "signal_intervals": profile["signal_intervals"],
                "disorder_intervals": profile["disorder_intervals"],
                "low_confidence_intervals": profile["low_confidence_intervals"],
                "supported_mhc_epitopes": len(profile["mhc_epitopes"]),
            }
            for source_id, profile in sorted(profiles.items())
        },
        "limitations": [
            "This is a bounded multi-family search, not an exhaustive molecular enumeration.",
            "Priority proxies are transparent computational triage and are not efficacy claims.",
            "External model jobs remain unexecuted until result artifacts are imported.",
            "Wet-lab comparison is required to establish superiority over manual designs.",
        ],
    }
    return {
        "identity": identity,
        "profiles": profiles,
        "atomic": atomic,
        "atomic_skipped": atomic_skipped,
        "eligible_fusions": eligible_fusions,
        "fusions": fusions,
        "specification": specification,
        "selection": selection,
        "model_jobs": model_jobs,
        "summary": summary,
    }


def _atomic_csv(records: list[dict[str, Any]]) -> str:
    fields = [
        "candidate_key",
        "source_id",
        "source_start",
        "source_end",
        "aa_length",
        "atomic_priority_proxy",
        "mhc_retention",
        "structure_confidence",
        "disorder_avoidance",
        "manual_boundary_support",
        "amino_acid_sha256",
    ]
    handle = StringIO(newline="")
    writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for record in records:
        writer.writerow(
            {
                **{key: record.get(key, "") for key in fields},
                **{key: record["features"].get(key, "") for key in fields},
            }
        )
    return handle.getvalue()


def _fusion_csv(records: list[dict[str, Any]]) -> str:
    fields = [
        "candidate_key",
        "template_id",
        "ordered_parent_keys",
        "linker_ids",
        "aa_length",
        "fusion_priority_proxy",
        "mhc_retention",
        "structure_confidence",
        "disorder_avoidance",
        "sequence_liability",
        "amino_acid_sha256",
    ]
    handle = StringIO(newline="")
    writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for record in records:
        writer.writerow(
            {
                **{key: record.get(key, "") for key in fields},
                **{key: record["features"].get(key, "") for key in fields},
                "ordered_parent_keys": "|".join(record["ordered_parent_keys"]),
                "linker_ids": "|".join(record["linker_ids"]),
            }
        )
    return handle.getvalue()


def _fasta(records: list[dict[str, Any]], *, label: str) -> str:
    return "".join(
        f">{record['candidate_key']} family={label} length={record['aa_length']}\n"
        f"{_wrap_fasta(record['amino_acid_sequence'])}\n"
        for record in records
    )


def _selection_fasta(
    selection: dict[str, Any],
    seed_batch: dict[str, Any],
    atomic: list[dict[str, Any]],
    fusions: list[dict[str, Any]],
) -> str:
    sequences = {
        candidate["candidate_key"]: candidate["amino_acid_sequence"]
        for candidate in seed_batch["candidates"]
    }
    sequences.update(
        {record["candidate_key"]: record["amino_acid_sequence"] for record in atomic}
    )
    sequences.update(
        {record["candidate_key"]: record["amino_acid_sequence"] for record in fusions}
    )
    return "".join(
        f">{record['candidate_key']} tier={record['selection_tier']} "
        f"length={record['aa_length']}\n{_wrap_fasta(sequences[record['candidate_key']])}\n"
        for record in selection["records"]
    )


def _render_report(result: dict[str, Any], policy: dict[str, Any]) -> str:
    summary = result["summary"]
    counts = summary["counts"]
    profile_rows = "".join(
        "<tr>"
        f"<td><code>{escape(source_id)}</code></td>"
        f"<td>{profile['length']}</td>"
        f"<td>{escape(str(profile['manual_ranges']))}</td>"
        f"<td>{escape(str(profile['signal_intervals']))}</td>"
        f"<td>{escape(str(profile['transmembrane_intervals']))}</td>"
        f"<td>{profile['supported_mhc_epitopes']}</td>"
        "</tr>"
        for source_id, profile in summary["source_profiles"].items()
    )
    template_rows = "".join(
        "<tr>"
        f"<td><code>{escape(template_id)}</code></td>"
        f"<td>{values['parent_orders']}</td>"
        f"<td>{values['linker_patterns']}</td>"
        f"<td>{values['eligible']}</td>"
        "</tr>"
        for template_id, values in sorted(
            summary["search_statistics"]["by_template"].items()
        )
    )
    job_rows = "".join(
        "<tr>"
        f"<td><code>{escape(job['adapter_id'])}</code></td>"
        f"<td>{escape(job['model']['name'])}</td>"
        f"<td><code>{escape(job['status'])}</code></td>"
        f"<td>{len(job['records'])}</td>"
        f"<td>{job['variants_per_parent']}</td>"
        "</tr>"
        for job in result["model_jobs"]["jobs"]
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stage 2 Multi-family Search</title><style>
:root{{--ink:#17211b;--muted:#5f6b64;--line:#d7dfd9;--paper:#f6f8f6;--white:#fff;--green:#176b45;--amber:#9a5d00}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 Arial,"Noto Sans SC",sans-serif;letter-spacing:0}}
header{{background:#153d2c;color:white;padding:34px max(24px,calc((100vw - 1180px)/2))}}header h1{{margin:0 0 8px;font-size:30px}}header p{{margin:0;color:#dce9e1;max-width:900px}}
main{{max-width:1180px;margin:auto;padding:28px 24px 64px}}section{{margin-bottom:32px}}h2{{font-size:20px}}.metrics{{display:grid;grid-template-columns:repeat(6,1fr);background:white;border:1px solid var(--line)}}.metric{{padding:16px;border-right:1px solid var(--line)}}.metric b{{display:block;font-size:25px;color:var(--green)}}.metric span,.muted{{color:var(--muted)}}.table{{overflow:auto;border:1px solid var(--line);background:white}}table{{border-collapse:collapse;width:100%;min-width:760px}}th,td{{padding:10px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{background:#edf2ee;font-size:12px}}code{{font-family:ui-monospace,monospace;font-size:12px}}.notice{{padding:14px 16px;border-left:4px solid var(--amber);background:#fff8e8}}@media(max-width:800px){{.metrics{{grid-template-columns:1fr 1fr}}}}
</style></head><body><header><h1>Stage 2 多路线菜谱搜索 / Multi-family Search</h1><p>当前 {counts['seed_candidates']} 条 baseline 被保留；本节点使用边界证据、独立 linker、透明代理指标和多样性预算扩大候选池，并为外部生成模型准备受约束任务。</p></header><main>
<section><h2>搜索结果 / Search result</h2><div class="metrics">
<div class="metric"><b>{counts['seed_candidates']}</b><span>baseline seeds</span></div>
<div class="metric"><b>{counts['generated_atomic_components']}</b><span>new boundaries</span></div>
<div class="metric"><b>{counts['eligible_unique_fusions']}</b><span>eligible pool</span></div>
<div class="metric"><b>{counts['materialized_fusions']}</b><span>materialized panel</span></div>
<div class="metric"><b>{counts['total_candidate_specification_records']}</b><span>total candidates</span></div>
<div class="metric"><b>{counts['stage3_selected_candidates']}</b><span>Stage 3 budget</span></div>
</div><p class="muted">候选数量来自版本化预算，而不是隐式截断。完整原子边界、配置空间计数、过滤原因和选择策略均保存在机器产物中。</p></section>
<section><h2>输入证据 / Evidence used</h2><div class="table"><table><thead><tr><th>Source</th><th>AA</th><th>Manual ranges</th><th>Signal</th><th>TM</th><th>MHC-supported</th></tr></thead><tbody>{profile_rows}</tbody></table></div></section>
<section><h2>架构搜索 / Architecture search</h2><div class="table"><table><thead><tr><th>Template</th><th>Parent orders</th><th>Linker patterns</th><th>Eligible</th></tr></thead><tbody>{template_rows}</tbody></table></div></section>
<section><h2>模型任务 / External model jobs</h2><div class="table"><table><thead><tr><th>Adapter</th><th>Model</th><th>Status</th><th>Parents</th><th>Variants each</th></tr></thead><tbody>{job_rows}</tbody></table></div></section>
<section class="notice"><strong>结论边界 / Boundary</strong><br>这是可复算的计算搜索池，不是“模型已经证明有效”。ESM3/ProteinMPNN job request 只有在输出被校验并重新进入候选 schema 后，才算真正生成候选。</section>
<footer>Search <code>{escape(result['identity'])}</code> | Policy <code>{escape(policy['policy_id'])}</code></footer>
</main></body></html>"""


def _materialized_documents(
    result: dict[str, Any],
    policy: dict[str, Any],
    seed_batch: dict[str, Any],
) -> dict[str, str]:
    atomic_document = {
        "schema_version": ATOMIC_SCHEMA,
        "search_identity": result["identity"],
        "records": result["atomic"],
        "skipped": result["atomic_skipped"],
    }
    pool_document = {
        "schema_version": POOL_SCHEMA,
        "search_identity": result["identity"],
        "records": result["eligible_fusions"],
        "statistics": result["summary"]["search_statistics"],
    }
    materialized_document = {
        "schema_version": MATERIALIZED_PANEL_SCHEMA,
        "search_identity": result["identity"],
        "records": result["fusions"],
        "selection_policy": "stratified-proxy-diversity",
    }
    return {
        "atomic_components.json": _json_text(atomic_document),
        "atomic_components.csv": _atomic_csv(result["atomic"]),
        "atomic_components.fasta": _fasta(result["atomic"], label="atomic-boundary"),
        "candidate_pool.json": _json_text(pool_document),
        "candidate_pool.csv": _fusion_csv(result["eligible_fusions"]),
        "candidate_pool.fasta": _fasta(result["eligible_fusions"], label="fusion-eligible"),
        "materialized_fusion_panel.json": _json_text(materialized_document),
        "materialized_fusion_panel.csv": _fusion_csv(result["fusions"]),
        "materialized_fusion_panel.fasta": _fasta(
            result["fusions"], label="fusion-materialized"
        ),
        "candidate_specification.generated.json": _json_text(result["specification"]),
        "external_model_jobs.json": _json_text(result["model_jobs"]),
        "report.html": _render_report(result, policy),
        "search_summary.json": _json_text(result["summary"]),
        "stage3_selection.json": _json_text(result["selection"]),
        "stage3_selection.fasta": _selection_fasta(
            result["selection"],
            seed_batch,
            result["atomic"],
            result["fusions"],
        ),
    }


def verify_stage2_search(directory: str | Path) -> dict[str, Any]:
    root = Path(directory).expanduser().resolve()
    errors: list[str] = []
    if not root.is_dir():
        return {
            "status": "fail",
            "identity": root.name,
            "errors": [f"Missing directory: {root}"],
        }
    actual_files = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }
    if any(path.is_symlink() for path in root.rglob("*")):
        errors.append("Search directory may not contain symlinks")
    if actual_files != EXPECTED_FILES:
        errors.append(
            f"Artifact set differs: missing={sorted(EXPECTED_FILES - actual_files)} "
            f"unexpected={sorted(actual_files - EXPECTED_FILES)}"
        )
    try:
        context = _load_object(root / "inputs/context.json", "search context")
        policy = _load_object(root / "inputs/policy.json", "search policy")
        seed = _load_object(
            root / "inputs/seed_candidate_batch.json",
            "search seed candidate batch",
        )
        evidence = _load_object(
            root / "inputs/evidence_bundle.json",
            "search evidence bundle",
        )
        index = _load_object(root / ARTIFACT_INDEX_FILENAME, "artifact index")
        if sha256_file(root / "inputs/policy.json") != context.get("policy_sha256"):
            errors.append("Policy snapshot hash differs from search context")
        if sha256_file(root / "inputs/seed_candidate_batch.json") != context.get(
            "seed_candidate_batch_sha256"
        ):
            errors.append("Seed snapshot hash differs from search context")
        if sha256_file(root / "inputs/evidence_bundle.json") != context.get(
            "evidence_bundle_sha256"
        ):
            errors.append("Evidence snapshot hash differs from search context")
        rebuilt = _build_search(context, policy, seed, evidence)
        if rebuilt["identity"] != root.name:
            errors.append("Search directory name differs from recomputed identity")
        expected_documents = _materialized_documents(rebuilt, policy, seed)
        for relative, expected in expected_documents.items():
            if (root / relative).read_text(encoding="utf-8") != expected:
                errors.append(f"{relative} differs from deterministic recomputation")
        if index.get("project_id") != context.get("project_id"):
            errors.append("Artifact index project differs from search context")
        if index.get("run_id") != root.name:
            errors.append("Artifact index run ID differs from search identity")
        if not _artifact_index_valid(root, index):
            errors.append("Artifact index differs from files on disk")
    except (KeyError, OSError, ValueError) as error:
        errors.append(str(error))
    return {
        "schema_version": 1,
        "identity": root.name,
        "path": str(root),
        "status": "fail" if errors else "pass",
        "errors": errors,
    }


def write_stage2_search(
    project_config: str | Path,
    *,
    policy_path: str | Path,
    seed_run_dir: str | Path,
    evidence_run_dir: str | Path,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    config = load_project_config(Path(project_config))
    seed: SeedRun = _resolve_seed_run(config, seed_run_dir)
    evidence_run = _resolve_evidence_run(config, evidence_run_dir)
    policy_source = Path(policy_path).expanduser().resolve()
    policy = _load_object(policy_source, "Stage 2 search policy")
    evidence_bundle = _build_evidence_bundle(evidence_run)
    evidence_text = _json_text(evidence_bundle)
    context = {
        "schema_version": SEARCH_CONTEXT_SCHEMA,
        "project_id": config.project_id,
        "design_round_id": seed.candidate_batch["design_round_id"],
        "seed_run_id": seed.manifest["run_id"],
        "seed_run_path": str(seed.directory),
        "seed_artifact_index_sha256": seed.artifact_index_sha256,
        "seed_candidate_batch_sha256": seed.candidate_batch_sha256,
        "evidence_run_id": evidence_bundle["source"]["run_id"],
        "evidence_run_path": str(evidence_run),
        "evidence_artifact_index_sha256": evidence_bundle["source"][
            "artifact_index_sha256"
        ],
        "evidence_bundle_sha256": hashlib.sha256(
            evidence_text.encode("utf-8")
        ).hexdigest(),
        "policy_sha256": sha256_file(policy_source),
        "generator": {
            "id": SEARCH_GENERATOR_ID,
            "version": SEARCH_GENERATOR_VERSION,
        },
    }
    result = _build_search(context, policy, seed.candidate_batch, evidence_bundle)
    root = (
        Path(output_root).expanduser().resolve()
        if output_root is not None
        else config.runtime_root / "input" / "stage2" / "searches"
    )
    root.mkdir(parents=True, exist_ok=True)
    output_dir = root / result["identity"]
    if output_dir.exists():
        verification = verify_stage2_search(output_dir)
        if verification["status"] != "pass":
            raise ValueError(
                "Existing Stage 2 search is invalid: "
                + "; ".join(verification["errors"][:5])
            )
    else:
        temporary = Path(tempfile.mkdtemp(prefix=f".{result['identity']}.", dir=root))
        try:
            (temporary / "inputs").mkdir()
            shutil.copyfile(policy_source, temporary / "inputs/policy.json")
            shutil.copyfile(
                seed.directory
                / "nodes"
                / CANDIDATE_STAGE_ID
                / "candidate_batch.json",
                temporary / "inputs/seed_candidate_batch.json",
            )
            (temporary / "inputs/context.json").write_text(
                _json_text(context), encoding="utf-8"
            )
            (temporary / "inputs/evidence_bundle.json").write_text(
                evidence_text, encoding="utf-8"
            )
            for relative, content in _materialized_documents(
                result,
                policy,
                seed.candidate_batch,
            ).items():
                (temporary / relative).write_text(content, encoding="utf-8")
            artifact_index = build_artifact_index(
                temporary,
                config.project_id,
                result["identity"],
            )
            (temporary / ARTIFACT_INDEX_FILENAME).write_text(
                _json_text(artifact_index), encoding="utf-8"
            )
            os.replace(temporary, output_dir)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
    verification = verify_stage2_search(output_dir)
    if verification["status"] != "pass":
        raise ValueError(
            "Stage 2 search verification failed: "
            + "; ".join(verification["errors"][:5])
        )
    counts = result["summary"]["counts"]
    return {
        "schema_version": 1,
        "project_id": config.project_id,
        "identity": result["identity"],
        "output_dir": str(output_dir),
        "seed_candidates": counts["seed_candidates"],
        "generated_atomic_components": counts["generated_atomic_components"],
        "eligible_unique_fusions": counts["eligible_unique_fusions"],
        "materialized_fusions": counts["materialized_fusions"],
        "total_candidates": counts["total_candidate_specification_records"],
        "stage3_selected_candidates": counts["stage3_selected_candidates"],
        "candidate_specification": str(
            output_dir / "candidate_specification.generated.json"
        ),
        "stage3_selection": str(output_dir / "stage3_selection.json"),
        "external_model_jobs": str(output_dir / "external_model_jobs.json"),
        "report": str(output_dir / "report.html"),
        "verification_status": verification["status"],
    }
