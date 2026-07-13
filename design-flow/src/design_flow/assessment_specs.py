"""Versioned Stage 4/5 specifications and external evidence contracts."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from .config import ProjectConfig, load_project_config
from .structure_job import _load_json
from .verification import verify_run


IMMUNE_STAGE_ID = "immune_evidence_assessment"
DEVELOPABILITY_STAGE_ID = "developability_assessment"
STRUCTURE_STAGE_ID = "protein_structure_assessment"
EVIDENCE_SCHEMA = "vaxflow.residue-evidence.v1"
IMMUNE_SPEC_RELATIVE = Path("input/stage4/immune_evidence_specification.json")
DEVELOPABILITY_SPEC_RELATIVE = Path(
    "input/stage5/developability_specification.json"
)
ADAPTER_IDS = (
    "mhc_binding",
    "host_similarity",
    "epitope_support",
)
DEVELOPABILITY_ADAPTER_IDS = (
    "signal_peptide",
    "transmembrane_topology",
    "disorder",
    "solubility",
    "aggregation",
)


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


def _resolve_structure_run(config: ProjectConfig, source_run_dir: Path | None) -> Path:
    if source_run_dir is None:
        latest = _load_json(config.run_root / "latest.json")
        source_run_dir = Path(str(latest.get("run_path", "")))
    source = source_run_dir.expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f"Stage 3 run directory not found: {source}")
    verification = verify_run(source)
    if verification["status"] != "pass":
        raise ValueError(
            "Stage 3 run verification failed: "
            + "; ".join(verification["errors"][:5])
        )
    manifest = _load_json(source / "manifest.json")
    if manifest.get("project_id") != config.project_id:
        raise ValueError("Stage 3 run belongs to another project")
    if manifest.get("current_stage") != STRUCTURE_STAGE_ID:
        raise ValueError(
            f"Stage 4/5 require a {STRUCTURE_STAGE_ID} run, got "
            f"{manifest.get('current_stage')}"
        )
    return source


def _source_protein_ids(structure_run: Path) -> list[str]:
    batch = _load_json(
        structure_run
        / "nodes"
        / "candidate_specification"
        / "candidate_batch.json"
    )
    source_ids = []
    for candidate in batch.get("candidates", []):
        if candidate.get("candidate_type") != "source_control":
            continue
        components = candidate.get("inferred_components", [])
        if len(components) != 1 or components[0].get("component_type") != "source_segment":
            raise ValueError("Source-control candidate has no single source component")
        source_ids.append(components[0]["source_protein_id"])
    if not source_ids:
        raise ValueError("Candidate batch has no source controls")
    return source_ids


def default_immune_specification(
    config: ProjectConfig,
    structure_run: Path,
) -> dict[str, Any]:
    source_ids = _source_protein_ids(structure_run)
    return {
        "schema_version": 1,
        "specification_id": f"{config.project_id}-immune-evidence-v1",
        "stage_id": IMMUNE_STAGE_ID,
        "mode": "exploratory",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "host": {
            "species": config.intended_host_species,
            "population_status": "pending",
            "population_description": "",
            "mhc_panel_path": None,
        },
        "pathogen_panel": {
            "status": "pending",
            "metadata_path": None,
            "source_alignments": {
                source_id: {
                    "alignment_path": None,
                    "reference_record_id": None,
                }
                for source_id in source_ids
            },
        },
        "adapters": {
            adapter_id: {"status": "not_configured", "result_path": None}
            for adapter_id in ADAPTER_IDS
        },
        "policy": {
            "status": "draft",
            "minimum_alignment_sequences": 3,
            "minimum_residue_panel_coverage": 0.8,
            "surface_proxy_ca_radius_angstrom": 10.0,
            "surface_proxy_max_nonlocal_neighbors": 8,
            "allow_as_release_gate": False,
        },
    }


def default_developability_specification(config: ProjectConfig) -> dict[str, Any]:
    expression_host = config.protein_expression_host.strip()
    context_status = (
        "pending" if not expression_host or expression_host.lower() == "unspecified" else "declared"
    )
    return {
        "schema_version": 1,
        "specification_id": f"{config.project_id}-developability-v1",
        "stage_id": DEVELOPABILITY_STAGE_ID,
        "mode": "exploratory",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "expression_context": {
            "status": context_status,
            "host": expression_host or "unspecified",
            "compartment": "unspecified",
            "purification_strategy": "unspecified",
            "formulation_context": "unspecified",
        },
        "external_adapters": {
            adapter_id: {"status": "not_configured", "result_path": None}
            for adapter_id in DEVELOPABILITY_ADAPTER_IDS
        },
        "policy": {
            "status": "draft",
            "allow_as_release_gate": False,
            "hydrophobic_window_length": 19,
            "hydrophobic_window_mean_kd": 1.6,
            "low_complexity_window_length": 12,
            "low_complexity_entropy_bits": 2.2,
            "homopolymer_min_length": 4,
        },
    }


def initialize_assessment_specifications(
    project_config: str | Path,
    *,
    source_run_dir: str | Path | None = None,
) -> dict[str, Any]:
    config = load_project_config(Path(project_config))
    source = _resolve_structure_run(
        config,
        Path(source_run_dir) if source_run_dir is not None else None,
    )
    immune_path = config.runtime_root / IMMUNE_SPEC_RELATIVE
    developability_path = config.runtime_root / DEVELOPABILITY_SPEC_RELATIVE
    created = []
    if not immune_path.exists():
        _atomic_json(immune_path, default_immune_specification(config, source))
        created.append(str(immune_path))
    if not developability_path.exists():
        _atomic_json(developability_path, default_developability_specification(config))
        created.append(str(developability_path))
    return {
        "project_id": config.project_id,
        "source_run": str(source),
        "immune_specification": str(immune_path),
        "developability_specification": str(developability_path),
        "created": created,
    }


def _runtime_path(config: ProjectConfig, value: Any, field_name: str) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be null or a non-empty path")
    raw = Path(value).expanduser()
    path = (raw if raw.is_absolute() else config.runtime_root / raw).resolve()
    if not path.is_relative_to(config.runtime_root):
        raise ValueError(f"{field_name} must resolve inside runtime_root")
    return path


def load_immune_specification(config: ProjectConfig) -> tuple[dict[str, Any], Path]:
    path = config.runtime_root / IMMUNE_SPEC_RELATIVE
    spec = _load_json(path)
    if (
        spec.get("schema_version") != 1
        or spec.get("stage_id") != IMMUNE_STAGE_ID
        or spec.get("mode") != "exploratory"
    ):
        raise ValueError("Unsupported immune evidence specification")
    if spec.get("policy", {}).get("allow_as_release_gate") is not False:
        raise ValueError("Exploratory immune evidence may not be configured as a release gate")
    return spec, path


def load_developability_specification(
    config: ProjectConfig,
) -> tuple[dict[str, Any], Path]:
    path = config.runtime_root / DEVELOPABILITY_SPEC_RELATIVE
    spec = _load_json(path)
    if (
        spec.get("schema_version") != 1
        or spec.get("stage_id") != DEVELOPABILITY_STAGE_ID
        or spec.get("mode") != "exploratory"
    ):
        raise ValueError("Unsupported developability specification")
    if spec.get("policy", {}).get("allow_as_release_gate") is not False:
        raise ValueError("Exploratory developability evidence may not be a release gate")
    return spec, path


def resolve_spec_path(
    config: ProjectConfig,
    value: Any,
    field_name: str,
) -> Path | None:
    return _runtime_path(config, value, field_name)


def load_residue_evidence(
    path: Path,
    *,
    adapter_id: str,
    candidate_by_id: dict[str, dict[str, Any]],
    candidate_batch_sha256: str,
) -> dict[str, Any]:
    document = _load_json(path)
    if (
        document.get("schema_version") != EVIDENCE_SCHEMA
        or document.get("adapter_id") != adapter_id
        or document.get("candidate_batch_sha256") != candidate_batch_sha256
    ):
        raise ValueError(f"Evidence identity mismatch for adapter {adapter_id}")
    tool = document.get("tool")
    if not isinstance(tool, dict) or not all(
        isinstance(tool.get(name), str) and tool[name].strip()
        for name in ("name", "version", "revision")
    ):
        raise ValueError(f"Adapter {adapter_id} must pin tool name/version/revision")
    observations = document.get("observations")
    if not isinstance(observations, list):
        raise ValueError(f"Adapter {adapter_id} observations must be an array")
    for index, observation in enumerate(observations):
        if not isinstance(observation, dict):
            raise ValueError(f"Adapter {adapter_id} observation {index} must be an object")
        candidate_id = observation.get("candidate_id")
        candidate = candidate_by_id.get(candidate_id)
        if candidate is None:
            raise ValueError(f"Adapter {adapter_id} references unknown candidate {candidate_id}")
        if observation.get("sequence_sha256") != candidate["amino_acid_sha256"]:
            raise ValueError(f"Adapter {adapter_id} candidate sequence hash mismatch")
        start, end = observation.get("residue_start"), observation.get("residue_end")
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or start < 1
            or end < start
            or end > len(candidate["amino_acid_sequence"])
        ):
            raise ValueError(f"Adapter {adapter_id} has an invalid residue range")
        status = observation.get("status")
        if status not in {"supported", "risk", "context", "not_supported"}:
            raise ValueError(f"Adapter {adapter_id} has invalid evidence status {status}")
        if not isinstance(observation.get("evidence_id"), str):
            raise ValueError(f"Adapter {adapter_id} observation requires evidence_id")
    return document
