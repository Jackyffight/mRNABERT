"""Versioned Stage 7 integrated-ranking policy."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any

from .config import ProjectConfig, load_project_config
from .product_specs import MRNA_PRODUCT_STAGE_ID, PROTEIN_PRODUCT_STAGE_ID
from .stage6_routing import archive_runtime_file
from .structure_job import _load_json
from .verification import verify_run


RANKING_STAGE_ID = "integrated_ranking"
RANKING_SPEC_RELATIVE = Path("input/stage7/ranking_specification.json")
EVO2_SENSITIVITY_RELATIVE = Path("input/stage7/experiments")


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
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


def _write_new_or_identical_json(path: Path, value: dict[str, Any]) -> None:
    if path.exists():
        if _load_json(path) != value:
            raise ValueError(f"Refusing to overwrite a different Stage 7 policy: {path}")
        return
    _atomic_json(path, value)


def _resolve_stage6_run(config: ProjectConfig, source_run_dir: Path | None) -> Path:
    if source_run_dir is None:
        latest = _load_json(config.run_root / "latest.json")
        source_run_dir = Path(str(latest.get("run_path", "")))
    source = source_run_dir.expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f"Stage 6 run directory not found: {source}")
    verification = verify_run(source)
    if verification["status"] != "pass":
        raise ValueError(
            "Stage 6 run verification failed: " + "; ".join(verification["errors"][:5])
        )
    manifest = _load_json(source / "manifest.json")
    if manifest.get("project_id") != config.project_id:
        raise ValueError("Stage 6 run belongs to another project")
    if (
        manifest.get("current_stage") != MRNA_PRODUCT_STAGE_ID
        or manifest.get("executed_stages")
        != [PROTEIN_PRODUCT_STAGE_ID, MRNA_PRODUCT_STAGE_ID]
    ):
        raise ValueError("Stage 7 requires the combined Stage 6 continuation run")
    return source


def default_ranking_specification(
    config: ProjectConfig,
    source_run: Path,
) -> dict[str, Any]:
    candidate_batch = _load_json(
        source_run / "nodes/candidate_specification/candidate_batch.json"
    )
    candidate_by_id = {
        candidate["candidate_id"]: candidate
        for candidate in candidate_batch["candidates"]
    }
    protein_products = _load_json(
        source_run / "nodes/protein_product_design/protein_products.json"
    )["products"]
    selected_candidate_ids: set[str] = set()
    for product in protein_products:
        candidate_id = product.get("candidate_id")
        candidate = candidate_by_id.get(candidate_id)
        if (
            candidate is None
            or candidate_id in selected_candidate_ids
            or product.get("candidate_key") != candidate["candidate_key"]
            or product.get("antigen_sequence_sha256")
            != candidate["amino_acid_sha256"]
        ):
            raise ValueError(
                "Stage 6 protein products do not bind one exact candidate set"
            )
        selected_candidate_ids.add(candidate_id)
    if not selected_candidate_ids:
        raise ValueError("Stage 6 has no product candidates for Stage 7")
    bindings = [
        {
            "candidate_id": candidate["candidate_id"],
            "candidate_key": candidate["candidate_key"],
            "amino_acid_sha256": candidate["amino_acid_sha256"],
        }
        for candidate in candidate_batch["candidates"]
        if candidate["candidate_id"] in selected_candidate_ids
    ]
    return {
        "schema_version": 1,
        "specification_id": f"{config.project_id}-integrated-ranking-v1",
        "stage_id": RANKING_STAGE_ID,
        "mode": "exploratory",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidate_set": {"status": "draft", "candidates": bindings},
        "features": [
            {
                "feature_id": "structure_mean_plddt",
                "source": "protein_structure_assessment.mean_plddt",
                "direction": "maximize",
                "weight": 1.0,
                "required": True,
                "modalities": ["protein", "mrna"],
            },
            {
                "feature_id": "structure_ptm",
                "source": "protein_structure_assessment.ptm",
                "direction": "maximize",
                "weight": 0.5,
                "required": True,
                "modalities": ["protein", "mrna"],
            },
            {
                "feature_id": "developability_review_liability_count",
                "source": "developability_assessment.review_liability_count",
                "direction": "minimize",
                "weight": 0.5,
                "required": True,
                "modalities": ["protein", "mrna"],
            },
            {
                "feature_id": "immune_surface_proxy_exposed_fraction",
                "source": "immune_evidence_assessment.surface_proxy",
                "direction": "maximize",
                "weight": 0.0,
                "required": False,
                "modalities": ["protein", "mrna"],
            },
            {
                "feature_id": "pathogen_conservation_mean",
                "source": "immune_evidence_assessment.pathogen_conservation",
                "direction": "maximize",
                "weight": 0.0,
                "required": False,
                "modalities": ["protein", "mrna"],
            },
            {
                "feature_id": "immune_mhc_supported_fraction",
                "source": "immune_evidence_assessment.mhc_binding.supported_fraction",
                "direction": "maximize",
                "weight": 0.0,
                "required": False,
                "modalities": ["protein", "mrna"],
            },
            {
                "feature_id": "developability_external_risk_count",
                "source": "developability_assessment.external_adapters.risk_count",
                "direction": "minimize",
                "weight": 0.0,
                "required": False,
                "modalities": ["protein", "mrna"],
            },
            {
                "feature_id": "protein_product_translation_verified",
                "source": "protein_product_design.translation_verified",
                "direction": "maximize",
                "weight": 0.25,
                "required": True,
                "modalities": ["protein"],
            },
            {
                "feature_id": "protein_expression_supported_fraction",
                "source": "protein_product_design.expression_support.supported_fraction",
                "direction": "maximize",
                "weight": 0.0,
                "required": False,
                "modalities": ["protein"],
            },
            {
                "feature_id": "mrna_best_cai_proxy",
                "source": "mrna_product_design.best_cai_proxy",
                "direction": "maximize",
                "weight": 0.25,
                "required": False,
                "modalities": ["mrna"],
            },
            {
                "feature_id": "mrna_evo2_mean_score",
                "source": "mrna_product_design.evo2_sequence_score.mean_score",
                "direction": "maximize",
                "weight": 0.0,
                "required": False,
                "modalities": ["mrna"],
            },
            {
                "feature_id": "mrna_rna_structure_mean_score",
                "source": "mrna_product_design.rna_structure.mean_score",
                "direction": "maximize",
                "weight": 0.0,
                "required": False,
                "modalities": ["mrna"],
            },
            {
                "feature_id": "mrna_full_construct_available",
                "source": "mrna_product_design.full_construct_available",
                "direction": "maximize",
                "weight": 0.25,
                "required": True,
                "modalities": ["mrna"],
            },
        ],
        "hard_gates": [],
        "portfolio": {
            "status": "draft",
            "budget_per_modality": 4,
            "maximum_sequence_similarity": 0.95,
            "minimum_source_controls": 1,
            "minimum_manual_controls": 1,
        },
        "sensitivity": {"relative_weight_perturbation": 0.20},
        "policy": {
            "status": "draft",
            "missing_value_policy": "coverage_penalty",
            "allow_provisional_ranking": True,
            "allow_formal_release": False,
        },
    }


def initialize_ranking_specification(
    project_config: str | Path,
    *,
    source_run_dir: str | Path | None = None,
    refresh_candidate_set: bool = False,
) -> dict[str, Any]:
    config = load_project_config(Path(project_config))
    source = _resolve_stage6_run(
        config, Path(source_run_dir) if source_run_dir is not None else None
    )
    path = config.runtime_root / RANKING_SPEC_RELATIVE
    created: list[str] = []
    archived: list[str] = []
    expected = default_ranking_specification(config, source)
    if not path.exists():
        _atomic_json(path, expected)
        created.append(str(path))
    else:
        current = _load_json(path)
        current_candidates = current.get("candidate_set", {}).get("candidates")
        expected_candidates = expected["candidate_set"]["candidates"]
        if current_candidates != expected_candidates:
            if not refresh_candidate_set:
                raise ValueError(
                    "Stage 7 ranking candidate set is stale; rerun init-stage7 "
                    "with --refresh-candidate-set"
                )
            if (
                current.get("schema_version") != 1
                or current.get("stage_id") != RANKING_STAGE_ID
                or current.get("mode") != "exploratory"
            ):
                raise ValueError("Cannot migrate unsupported Stage 7 specification")
            archived_path = archive_runtime_file(
                path,
                config.runtime_root / "input/stage7/history",
            )
            archived.append(str(archived_path))
            refreshed = dict(expected)
            for field in (
                "features",
                "hard_gates",
                "portfolio",
                "sensitivity",
                "policy",
            ):
                if field in current:
                    refreshed[field] = current[field]
            refreshed["candidate_set"] = {
                **expected["candidate_set"],
                "status": "draft",
            }
            _atomic_json(path, refreshed)
    return {
        "project_id": config.project_id,
        "source_run": str(source),
        "ranking_specification": str(path),
        "created": created,
        "archived": archived,
    }


def load_ranking_specification(
    config: ProjectConfig,
    specification_path: str | Path | None = None,
) -> tuple[dict[str, Any], Path]:
    path = (
        Path(specification_path).expanduser().resolve()
        if specification_path is not None
        else config.runtime_root / RANKING_SPEC_RELATIVE
    )
    if not path.is_file():
        raise ValueError(f"Stage 7 ranking specification not found: {path}")
    document = _load_json(path)
    if (
        document.get("schema_version") != 1
        or document.get("stage_id") != RANKING_STAGE_ID
        or document.get("mode") != "exploratory"
    ):
        raise ValueError("Unsupported integrated ranking specification")
    policy = document.get("policy", {})
    if policy.get("allow_formal_release") is not False:
        raise ValueError("Stage 7 may propose a portfolio but may not formally release it")
    if policy.get("missing_value_policy") != "coverage_penalty":
        raise ValueError("Only the explicit coverage_penalty missing-value policy is supported")
    return document, path


def prepare_evo2_sensitivity_specifications(
    project_config: str | Path,
    *,
    source_run_dir: str | Path | None = None,
    evo2_weight: float = 0.25,
) -> dict[str, Any]:
    if (
        isinstance(evo2_weight, bool)
        or not isinstance(evo2_weight, (int, float))
        or not math.isfinite(float(evo2_weight))
        or not 0 < float(evo2_weight) <= 1
    ):
        raise ValueError("Evo 2 sensitivity weight must be greater than 0 and at most 1")

    config = load_project_config(Path(project_config))
    source = _resolve_stage6_run(
        config, Path(source_run_dir) if source_run_dir is not None else None
    )
    canonical, canonical_path = load_ranking_specification(config)
    mrna_document = _load_json(
        source / "nodes/mrna_product_design/mrna_products.json"
    )
    adapter = mrna_document.get("adapter_states", {}).get("evo2_sequence_score", {})
    observations = adapter.get("observations")
    result_sha256 = adapter.get("result_sha256")
    if (
        adapter.get("status") != "evaluated"
        or not isinstance(observations, list)
        or not observations
        or not isinstance(result_sha256, str)
        or len(result_sha256) != 64
    ):
        raise ValueError("Stage 6 run does not contain checksum-bound Evo 2 evidence")

    design_to_candidate = {
        design["design_id"]: design["candidate_id"]
        for design in mrna_document.get("designs", [])
    }
    observed_candidate_ids: set[str] = set()
    observed_design_ids: set[str] = set()
    for observation in observations:
        design_id = observation.get("design_id")
        candidate_id = observation.get("candidate_id")
        bound_candidate_id = design_to_candidate.get(design_id)
        if (
            not isinstance(design_id, str)
            or bound_candidate_id is None
            or (candidate_id is not None and candidate_id != bound_candidate_id)
            or design_id in observed_design_ids
        ):
            raise ValueError("Evo 2 evidence contains an invalid or duplicate design binding")
        observed_design_ids.add(design_id)
        observed_candidate_ids.add(bound_candidate_id)

    canonical_bindings = canonical.get("candidate_set", {}).get("candidates", [])
    if not isinstance(canonical_bindings, list):
        raise ValueError("Canonical Stage 7 candidate set is invalid")
    selected_bindings = [
        binding
        for binding in canonical_bindings
        if binding.get("candidate_id") in observed_candidate_ids
    ]
    if len(selected_bindings) != len(observed_candidate_ids):
        raise ValueError("Evo 2 evidence is not a subset of the canonical Stage 7 candidates")

    weight = float(evo2_weight)
    weight_slug = format(weight, ".8g").replace(".", "p")
    experiment_id = f"evo2-observed-{result_sha256[:12]}-w{weight_slug}"
    output_dir = config.runtime_root / EVO2_SENSITIVITY_RELATIVE / experiment_id
    source_manifest = _load_json(source / "manifest.json")
    common_experiment = {
        "experiment_id": experiment_id,
        "experiment_type": "evo2-observed-subset-weight-sensitivity",
        "source_run_id": source_manifest["run_id"],
        "source_evo2_result_sha256": result_sha256,
        "observed_candidate_count": len(selected_bindings),
        "observed_design_count": len(observed_design_ids),
        "scope": "Only candidates with checksum-bound Evo 2 evidence are compared.",
        "formal_release_allowed": False,
    }

    def build(role: str, feature_weight: float) -> dict[str, Any]:
        document = copy.deepcopy(canonical)
        document["created_at_utc"] = source_manifest["created_at_utc"]
        document["specification_id"] = (
            f"{canonical['specification_id']}-{experiment_id}-{role}"
        )
        document["candidate_set"] = {
            "status": "draft",
            "candidates": selected_bindings,
        }
        document["experiment"] = {
            **common_experiment,
            "role": role,
            "evo2_weight": feature_weight,
        }
        for feature in document["features"]:
            if feature.get("feature_id") == "mrna_evo2_mean_score":
                feature["weight"] = feature_weight
                break
        else:
            raise ValueError("Canonical Stage 7 policy does not declare the Evo 2 feature")
        document["policy"] = {
            **document["policy"],
            "status": "draft",
            "allow_formal_release": False,
        }
        return document

    control = build("control", 0.0)
    weighted = build("weighted", weight)
    control_path = output_dir / "control.json"
    weighted_path = output_dir / "weighted.json"
    _write_new_or_identical_json(control_path, control)
    _write_new_or_identical_json(weighted_path, weighted)
    return {
        "project_id": config.project_id,
        "source_run": str(source),
        "canonical_specification": str(canonical_path),
        "experiment_id": experiment_id,
        "candidate_count": len(selected_bindings),
        "design_count": len(observed_design_ids),
        "evo2_weight": weight,
        "control_specification": str(control_path),
        "weighted_specification": str(weighted_path),
    }
