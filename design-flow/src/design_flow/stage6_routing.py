"""Deterministic candidate routing between Stage 5 and Stage 6."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from .assessment_specs import load_structure_candidate_scope
from .config import ProjectConfig
from .structure_job import _load_json
from .verification import ARTIFACT_INDEX_FILENAME, sha256_file


ROUTING_POLICY_SCHEMA = "vaxflow.stage6-routing-policy.v1"
ROUTING_MANIFEST_SCHEMA = "vaxflow.stage6-routing-manifest.v1"
ROUTING_RULESET_ID = "stage6-evidence-cost-routing-v1"
ROUTING_POLICY_RELATIVE = Path("input/stage6/candidate_routing_policy.json")
ROUTING_MANIFEST_RELATIVE = Path("input/stage6/candidate_routing_manifest.json")
ROUTING_LANES = ("priority", "diversity_rescue", "archive")


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _identity(document: dict[str, Any]) -> str:
    return _canonical_sha256(
        {key: value for key, value in document.items() if key != "routing_id"}
    )


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def default_routing_policy(config: ProjectConfig) -> dict[str, Any]:
    return {
        "schema_version": ROUTING_POLICY_SCHEMA,
        "policy_id": f"{config.project_id}-stage6-routing-v1",
        "status": "approved_for_exploratory_use",
        "authority": "ADR-0004",
        "priority_confidence_bands": [
            "higher_confidence",
            "mixed_confidence",
        ],
        "forced_rescue_generator_ids": ["source_intake", "manual_import"],
        "diversity_dimensions": [
            "candidate_type",
            "source_composition",
            "source_order",
            "architecture",
            "linker_family",
        ],
        "maximum_diversity_rescue_candidates": 64,
        "product_drafting_lanes": list(ROUTING_LANES),
        "expensive_followup_lanes": ["priority", "diversity_rescue"],
    }


def _validate_policy(policy: dict[str, Any]) -> None:
    if (
        policy.get("schema_version") != ROUTING_POLICY_SCHEMA
        or not isinstance(policy.get("policy_id"), str)
        or not policy["policy_id"]
        or policy.get("status") != "approved_for_exploratory_use"
    ):
        raise ValueError("Stage 6 routing policy identity/status is invalid")
    priority_bands = policy.get("priority_confidence_bands")
    forced_generators = policy.get("forced_rescue_generator_ids")
    diversity_dimensions = policy.get("diversity_dimensions")
    maximum_rescue = policy.get("maximum_diversity_rescue_candidates")
    drafting_lanes = policy.get("product_drafting_lanes")
    expensive_lanes = policy.get("expensive_followup_lanes")
    priority_bands_valid = (
        isinstance(priority_bands, list)
        and bool(priority_bands)
        and all(isinstance(value, str) and value for value in priority_bands)
        and len(priority_bands) == len(set(priority_bands))
    )
    forced_generators_valid = (
        isinstance(forced_generators, list)
        and all(
            isinstance(value, str) and value for value in forced_generators
        )
        and len(forced_generators) == len(set(forced_generators))
    )
    expensive_lanes_valid = (
        isinstance(expensive_lanes, list)
        and bool(expensive_lanes)
        and all(isinstance(value, str) and value for value in expensive_lanes)
        and len(expensive_lanes) == len(set(expensive_lanes))
    )
    if (
        not priority_bands_valid
        or not forced_generators_valid
        or diversity_dimensions
        != [
            "candidate_type",
            "source_composition",
            "source_order",
            "architecture",
            "linker_family",
        ]
        or not isinstance(maximum_rescue, int)
        or isinstance(maximum_rescue, bool)
        or maximum_rescue < 1
        or drafting_lanes != list(ROUTING_LANES)
        or not expensive_lanes_valid
        or not set(expensive_lanes) <= set(drafting_lanes)
        or "archive" in expensive_lanes
    ):
        raise ValueError("Stage 6 routing policy fields are invalid")


def _component_signature(candidate: dict[str, Any]) -> dict[str, Any]:
    source_order: list[str] = []
    component_pattern: list[str] = []
    linker_families: list[str] = []
    for component in candidate.get("inferred_components", []):
        component_type = component.get("component_type")
        component_pattern.append(str(component_type))
        if component_type == "source_segment":
            source_order.append(str(component.get("source_protein_id")))
        elif component_type == "addition":
            linker_id = component.get("linker_id")
            if isinstance(linker_id, str) and linker_id:
                linker_families.append(linker_id)
            else:
                sequence = str(component.get("sequence", ""))
                linker_families.append(f"sequence-{hashlib.sha256(sequence.encode('ascii')).hexdigest()[:12]}")
    if not linker_families:
        linker_families = ["direct"]
    return {
        "candidate_type": candidate.get("candidate_type"),
        "source_order": source_order,
        "component_pattern": component_pattern,
        "linker_families": linker_families,
    }


def _diversity_features(signature: dict[str, Any]) -> list[str]:
    source_order = signature["source_order"]
    linker_families = signature["linker_families"]
    features = {
        f"candidate_type:{signature['candidate_type']}",
        "source_composition:" + "+".join(sorted(source_order)),
        "source_order:" + "+".join(source_order),
        "architecture:"
        f"sources={len(source_order)};"
        f"linkers={sum(value == 'addition' for value in signature['component_pattern'])}",
    }
    features.update(f"linker_family:{value}" for value in linker_families)
    return sorted(features)


def _evidence_record(
    assessment: dict[str, Any],
    immune: dict[str, Any],
    developability: dict[str, Any],
) -> dict[str, Any]:
    categories = immune.get("categories", {})
    mhc = categories.get("mhc_binding", {})
    observations = int(mhc.get("observation_count", 0))
    supported = int(mhc.get("supported_count", 0))
    surface = categories.get("surface_accessibility_proxy", {})
    return {
        "confidence_band": assessment.get("confidence_band"),
        "mean_plddt": float(assessment.get("mean_plddt", 0.0)),
        "ptm": float(assessment.get("ptm", 0.0)),
        "surface_proxy_exposed_fraction": float(
            surface.get("exposed_fraction", 0.0)
        ),
        "mhc_supported_fraction": (
            round(supported / observations, 8) if observations else 0.0
        ),
        "developability_review_liability_count": int(
            developability.get("review_liability_count", 0)
        ),
    }


def _representative_key(record: dict[str, Any]) -> tuple[Any, ...]:
    evidence = record["evidence"]
    return (
        -evidence["mhc_supported_fraction"],
        evidence["developability_review_liability_count"],
        -evidence["surface_proxy_exposed_fraction"],
        -evidence["ptm"],
        -evidence["mean_plddt"],
        record["candidate_id"],
    )


def route_candidates(
    candidates: list[dict[str, Any]],
    assessments: list[dict[str, Any]],
    immune_candidates: list[dict[str, Any]],
    developability_candidates: list[dict[str, Any]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    """Assign all active candidates to priority, rescue, or archive lanes."""

    _validate_policy(policy)
    candidate_by_id = {candidate.get("candidate_id"): candidate for candidate in candidates}
    assessment_by_id = {item.get("candidate_id"): item for item in assessments}
    immune_by_id = {item.get("candidate_id"): item for item in immune_candidates}
    developability_by_id = {
        item.get("candidate_id"): item for item in developability_candidates
    }
    candidate_ids = set(candidate_by_id)
    if (
        None in candidate_ids
        or len(candidate_by_id) != len(candidates)
        or len(assessment_by_id) != len(assessments)
        or len(immune_by_id) != len(immune_candidates)
        or len(developability_by_id) != len(developability_candidates)
        or set(assessment_by_id) != candidate_ids
        or set(immune_by_id) != candidate_ids
        or set(developability_by_id) != candidate_ids
    ):
        raise ValueError("Stage 6 routing inputs do not cover one exact candidate set")

    records: list[dict[str, Any]] = []
    priority_bands = set(policy["priority_confidence_bands"])
    forced_generators = set(policy["forced_rescue_generator_ids"])
    for candidate_id in sorted(candidate_ids):
        candidate = candidate_by_id[candidate_id]
        assessment = assessment_by_id[candidate_id]
        if (
            assessment.get("candidate_key") != candidate.get("candidate_key")
            or assessment.get("sequence_sha256")
            != candidate.get("amino_acid_sha256")
        ):
            raise ValueError(f"Stage 6 routing candidate identity mismatch: {candidate_id}")
        signature = _component_signature(candidate)
        stratum_id = _canonical_sha256(signature)
        generator_id = (
            candidate.get("proposal", {}).get("generator", {}).get("id")
        )
        confidence_band = assessment.get("confidence_band")
        lane = None
        reason = None
        if confidence_band in priority_bands:
            lane = "priority"
            reason = "structure_confidence_priority"
        elif generator_id in forced_generators:
            lane = "diversity_rescue"
            reason = "forced_control_rescue"
        records.append(
            {
                "candidate_id": candidate_id,
                "candidate_key": candidate["candidate_key"],
                "amino_acid_sha256": candidate["amino_acid_sha256"],
                "generator_id": generator_id,
                "diversity_stratum": {"stratum_id": stratum_id, **signature},
                "diversity_features": _diversity_features(signature),
                "evidence": _evidence_record(
                    assessment,
                    immune_by_id[candidate_id],
                    developability_by_id[candidate_id],
                ),
                "lane": lane,
                "selection_reason": reason,
            }
        )

    all_features = {
        feature
        for record in records
        for feature in record["diversity_features"]
    }
    covered_features = {
        feature
        for record in records
        if record["lane"] in {"priority", "diversity_rescue"}
        for feature in record["diversity_features"]
    }
    rescue_count = sum(record["lane"] == "diversity_rescue" for record in records)
    maximum_rescue = policy["maximum_diversity_rescue_candidates"]
    if rescue_count > maximum_rescue:
        raise ValueError("Forced Stage 6 rescue controls exceed the routing budget")
    while covered_features != all_features and rescue_count < maximum_rescue:
        candidates_with_gain = [
            (
                len(set(record["diversity_features"]) - covered_features),
                record,
            )
            for record in records
            if record["lane"] is None
        ]
        candidates_with_gain = [
            item for item in candidates_with_gain if item[0] > 0
        ]
        if not candidates_with_gain:
            break
        _, selected_record = min(
            candidates_with_gain,
            key=lambda item: (-item[0], *_representative_key(item[1])),
        )
        selected_record["lane"] = "diversity_rescue"
        selected_record["selection_reason"] = "diversity_feature_coverage"
        covered_features.update(selected_record["diversity_features"])
        rescue_count += 1
    drafting_lanes = set(policy["product_drafting_lanes"])
    expensive_lanes = set(policy["expensive_followup_lanes"])
    for record in records:
        if record["lane"] is None:
            record["lane"] = "archive"
            record["selection_reason"] = "covered_stratum_archive"
        record["product_drafting_eligible"] = record["lane"] in drafting_lanes
        record["expensive_followup_eligible"] = record["lane"] in expensive_lanes

    counts = {
        lane: sum(record["lane"] == lane for record in records)
        for lane in ROUTING_LANES
    }
    document = {
        "schema_version": ROUTING_MANIFEST_SCHEMA,
        "ruleset_id": ROUTING_RULESET_ID,
        "routing_id": "pending",
        "policy_id": policy["policy_id"],
        "active_candidate_set_sha256": _canonical_sha256(
            [
                {
                    key: record[key]
                    for key in ("candidate_id", "candidate_key", "amino_acid_sha256")
                }
                for record in records
            ]
        ),
        "counts": {
            "active": len(records),
            **counts,
            "product_drafting": sum(
                record["product_drafting_eligible"] for record in records
            ),
            "expensive_followup": sum(
                record["expensive_followup_eligible"] for record in records
            ),
        },
        "diversity_coverage": {
            "feature_count": len(all_features),
            "covered_feature_count": len(covered_features),
            "uncovered_features": sorted(all_features - covered_features),
            "maximum_rescue_candidates": maximum_rescue,
        },
        "records": records,
    }
    document["routing_id"] = _identity(document)
    return document


def bind_routing_source(
    routed_candidates: dict[str, Any],
    source: dict[str, Any],
) -> dict[str, Any]:
    document = {**routed_candidates, "source": source, "routing_id": "pending"}
    document["routing_id"] = _identity(document)
    return document


def build_routing_manifest(
    source_run: Path,
    policy: dict[str, Any],
    *,
    policy_sha256: str,
) -> dict[str, Any]:
    scope = load_structure_candidate_scope(source_run)
    structure_path = (
        source_run
        / "nodes/protein_structure_assessment/structure_assessments.json"
    )
    immune_path = (
        source_run / "nodes/immune_evidence_assessment/immune_evidence.json"
    )
    developability_path = (
        source_run
        / "nodes/developability_assessment/developability_assessments.json"
    )
    structure = _load_json(structure_path)
    immune = _load_json(immune_path)
    developability = _load_json(developability_path)
    document = route_candidates(
        scope["candidate_batch"]["candidates"],
        structure["assessments"],
        immune["candidates"],
        developability["candidates"],
        policy,
    )
    return bind_routing_source(document, {
        "stage5_run_id": _load_json(source_run / "manifest.json")["run_id"],
        "stage5_artifact_index_sha256": sha256_file(
            source_run / ARTIFACT_INDEX_FILENAME
        ),
        "candidate_batch_sha256": scope["candidate_batch_sha256"],
        "stage3_candidate_set_sha256": scope["candidate_set_sha256"],
        "structure_assessments_sha256": sha256_file(structure_path),
        "immune_evidence_sha256": sha256_file(immune_path),
        "developability_assessments_sha256": sha256_file(developability_path),
        "routing_policy_sha256": policy_sha256,
    })


def archive_runtime_file(path: Path, history_root: Path) -> Path:
    digest = sha256_file(path)
    destination = history_root / f"{path.stem}-{digest}{path.suffix}"
    if destination.exists():
        if not destination.is_file() or sha256_file(destination) != digest:
            raise ValueError(
                f"Stage 6 history artifact conflicts with its digest: {destination}"
            )
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", dir=destination.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(path.read_bytes())
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    return destination


def initialize_stage6_routing(
    config: ProjectConfig,
    source_run: Path,
    *,
    refresh: bool,
) -> dict[str, Any]:
    policy_path = config.runtime_root / ROUTING_POLICY_RELATIVE
    manifest_path = config.runtime_root / ROUTING_MANIFEST_RELATIVE
    history_root = config.runtime_root / "input/stage6/history"
    created: list[str] = []
    archived: list[str] = []
    if not policy_path.exists():
        _atomic_json(policy_path, default_routing_policy(config))
        created.append(str(policy_path))
    policy = _load_json(policy_path)
    _validate_policy(policy)
    expected = build_routing_manifest(
        source_run,
        policy,
        policy_sha256=sha256_file(policy_path),
    )
    if manifest_path.exists():
        current = _load_json(manifest_path)
        if current != expected:
            if not refresh:
                raise ValueError(
                    "Stage 6 routing manifest is stale; rerun init-stage6 with "
                    "--refresh-selection"
                )
            archived.append(
                str(archive_runtime_file(manifest_path, history_root))
            )
            _atomic_json(manifest_path, expected)
    else:
        _atomic_json(manifest_path, expected)
        created.append(str(manifest_path))
    return {
        "policy": policy,
        "policy_path": policy_path,
        "manifest": expected,
        "manifest_path": manifest_path,
        "created": created,
        "archived": archived,
    }


def routing_descriptor(
    config: ProjectConfig,
    routing: dict[str, Any],
) -> dict[str, Any]:
    manifest_path: Path = routing["manifest_path"]
    policy_path: Path = routing["policy_path"]
    manifest = routing["manifest"]
    policy = routing["policy"]
    return {
        "routing_id": manifest["routing_id"],
        "manifest_path": str(manifest_path.relative_to(config.runtime_root)),
        "manifest_sha256": sha256_file(manifest_path),
        "policy_path": str(policy_path.relative_to(config.runtime_root)),
        "policy_sha256": sha256_file(policy_path),
        "active_candidate_set_sha256": manifest["active_candidate_set_sha256"],
        "product_drafting_lanes": policy["product_drafting_lanes"],
        "expensive_followup_lanes": policy["expensive_followup_lanes"],
    }


def resolve_stage6_routing(
    config: ProjectConfig,
    source_run: Path,
    descriptor: dict[str, Any],
) -> tuple[dict[str, Any], Path, Path]:
    if not isinstance(descriptor, dict):
        raise ValueError("Stage 6 specification has no routing descriptor")
    manifest_path = (
        config.runtime_root / str(descriptor.get("manifest_path", ""))
    ).resolve()
    policy_path = (
        config.runtime_root / str(descriptor.get("policy_path", ""))
    ).resolve()
    if (
        not manifest_path.is_relative_to(config.runtime_root)
        or not policy_path.is_relative_to(config.runtime_root)
        or not manifest_path.is_file()
        or not policy_path.is_file()
    ):
        raise ValueError("Stage 6 routing inputs are missing or outside runtime_root")
    policy = _load_json(policy_path)
    _validate_policy(policy)
    expected = build_routing_manifest(
        source_run,
        policy,
        policy_sha256=sha256_file(policy_path),
    )
    observed = _load_json(manifest_path)
    expected_descriptor = routing_descriptor(
        config,
        {
            "manifest": expected,
            "manifest_path": manifest_path,
            "policy": policy,
            "policy_path": policy_path,
        },
    )
    if observed != expected or descriptor != expected_descriptor:
        raise ValueError("Stage 6 routing manifest or descriptor is stale")
    return observed, manifest_path, policy_path
