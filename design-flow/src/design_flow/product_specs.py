"""Versioned specifications for the parallel Stage 6 product-design branches."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from .assessment_specs import DEVELOPABILITY_STAGE_ID
from .config import ProjectConfig, load_project_config
from .structure_job import _load_json
from .verification import verify_run


PROTEIN_PRODUCT_STAGE_ID = "protein_product_design"
MRNA_PRODUCT_STAGE_ID = "mrna_product_design"
PROTEIN_SPEC_RELATIVE = Path("input/stage6/protein_product_specification.json")
MRNA_SPEC_RELATIVE = Path("input/stage6/mrna_product_specification.json")
CODON_USAGE_SCHEMA = "vaxflow.codon-usage.v1"
PRODUCT_EVIDENCE_SCHEMA = "vaxflow.product-evidence.v1"
MRNA_EVIDENCE_SCHEMA = "vaxflow.mrna-evidence.v1"
PROTEIN_ADAPTER_IDS = ("structure_recheck", "expression_support")
MRNA_ADAPTER_IDS = ("rna_structure", "evo2_sequence_score")


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


def _resolve_stage5_run(config: ProjectConfig, source_run_dir: Path | None) -> Path:
    if source_run_dir is None:
        latest = _load_json(config.run_root / "latest.json")
        source_run_dir = Path(str(latest.get("run_path", "")))
    source = source_run_dir.expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f"Stage 4/5 run directory not found: {source}")
    verification = verify_run(source)
    if verification["status"] != "pass":
        raise ValueError(
            "Stage 4/5 run verification failed: "
            + "; ".join(verification["errors"][:5])
        )
    manifest = _load_json(source / "manifest.json")
    if manifest.get("project_id") != config.project_id:
        raise ValueError("Stage 4/5 run belongs to another project")
    if (
        manifest.get("current_stage") != DEVELOPABILITY_STAGE_ID
        or manifest.get("executed_stages")
        != ["immune_evidence_assessment", DEVELOPABILITY_STAGE_ID]
    ):
        raise ValueError("Stage 6 requires the combined Stage 4/5 continuation run")
    return source


def _candidate_bindings(source_run: Path) -> list[dict[str, str]]:
    batch = _load_json(source_run / "nodes/candidate_specification/candidate_batch.json")
    return [
        {
            "candidate_id": candidate["candidate_id"],
            "candidate_key": candidate["candidate_key"],
            "amino_acid_sha256": candidate["amino_acid_sha256"],
        }
        for candidate in batch["candidates"]
        if candidate.get("duplicate_of") is None
    ]


def default_protein_product_specification(
    config: ProjectConfig,
    source_run: Path,
) -> dict[str, Any]:
    bindings = _candidate_bindings(source_run)
    context_declared = config.protein_expression_host.strip().lower() != "unspecified"
    return {
        "schema_version": 1,
        "specification_id": f"{config.project_id}-protein-product-v1",
        "stage_id": PROTEIN_PRODUCT_STAGE_ID,
        "mode": "exploratory",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "selection": {
            "status": "draft",
            "candidates": bindings,
        },
        "expression_context": {
            "status": "declared" if context_declared else "pending",
            "host": config.protein_expression_host or "unspecified",
            "compartment": "unspecified",
            "vector_family": "unspecified",
            "purification_strategy": "unspecified",
            "final_product_form": "unspecified",
        },
        "constructs": {
            binding["candidate_id"]: {
                "elements": [],
                "coding_sequence_path": None,
            }
            for binding in bindings
        },
        "codon_usage_table_path": None,
        "external_adapters": {
            adapter_id: {"status": "not_configured", "result_path": None}
            for adapter_id in PROTEIN_ADAPTER_IDS
        },
        "policy": {
            "status": "draft",
            "terminal_stop_codon": "TAA",
            "allow_as_release_gate": False,
        },
    }


def default_mrna_product_specification(
    config: ProjectConfig,
    source_run: Path,
) -> dict[str, Any]:
    bindings = _candidate_bindings(source_run)
    target_declared = config.mrna_target_species.strip().lower() != "unspecified"
    return {
        "schema_version": 1,
        "specification_id": f"{config.project_id}-mrna-product-v1",
        "stage_id": MRNA_PRODUCT_STAGE_ID,
        "mode": "exploratory",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "selection": {
            "status": "draft",
            "candidates": bindings,
        },
        "target_context": {
            "status": "declared" if target_declared else "pending",
            "species": config.mrna_target_species or "unspecified",
            "cell_context": "unspecified",
            "delivery_platform": "unspecified",
        },
        "codon_usage_table_path": None,
        "generation": {
            "status": "disabled",
            "seed": 42,
            "designs_per_candidate": 4,
            "search_multiplier": 32,
        },
        "constraints": {
            "minimum_gc_fraction": 0.35,
            "maximum_gc_fraction": 0.65,
            "target_gc_fraction": 0.50,
            "maximum_homopolymer_length": 6,
            "forbidden_motifs": [],
        },
        "noncoding_elements": {
            "status": "pending",
            "five_prime_utr": "",
            "three_prime_utr": "",
            "poly_a_length": None,
            "cap_assumption": "unspecified",
            "modified_nucleoside_assumption": "unspecified",
        },
        "external_adapters": {
            adapter_id: {"status": "not_configured", "result_path": None}
            for adapter_id in MRNA_ADAPTER_IDS
        },
        "policy": {
            "status": "draft",
            "terminal_stop_codon": "TAA",
            "allow_as_release_gate": False,
        },
    }


def initialize_product_specifications(
    project_config: str | Path,
    *,
    source_run_dir: str | Path | None = None,
) -> dict[str, Any]:
    config = load_project_config(Path(project_config))
    source = _resolve_stage5_run(
        config,
        Path(source_run_dir) if source_run_dir is not None else None,
    )
    protein_path = config.runtime_root / PROTEIN_SPEC_RELATIVE
    mrna_path = config.runtime_root / MRNA_SPEC_RELATIVE
    created: list[str] = []
    if not protein_path.exists():
        _atomic_json(protein_path, default_protein_product_specification(config, source))
        created.append(str(protein_path))
    if not mrna_path.exists():
        _atomic_json(mrna_path, default_mrna_product_specification(config, source))
        created.append(str(mrna_path))
    return {
        "project_id": config.project_id,
        "source_run": str(source),
        "protein_specification": str(protein_path),
        "mrna_specification": str(mrna_path),
        "created": created,
    }


def resolve_runtime_input(
    config: ProjectConfig,
    value: Any,
    field_name: str,
) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be null or a non-empty path")
    raw = Path(value).expanduser()
    path = (raw if raw.is_absolute() else config.runtime_root / raw).resolve()
    if not path.is_relative_to(config.runtime_root):
        raise ValueError(f"{field_name} must resolve inside runtime_root")
    return path


def _load_specification(path: Path, stage_id: str) -> dict[str, Any]:
    document = _load_json(path)
    if (
        document.get("schema_version") != 1
        or document.get("stage_id") != stage_id
        or document.get("mode") != "exploratory"
    ):
        raise ValueError(f"Unsupported {stage_id} specification")
    if document.get("policy", {}).get("allow_as_release_gate") is not False:
        raise ValueError(f"Exploratory {stage_id} may not be a release gate")
    return document


def load_product_specifications(
    config: ProjectConfig,
) -> tuple[dict[str, Any], Path, dict[str, Any], Path]:
    protein_path = config.runtime_root / PROTEIN_SPEC_RELATIVE
    mrna_path = config.runtime_root / MRNA_SPEC_RELATIVE
    return (
        _load_specification(protein_path, PROTEIN_PRODUCT_STAGE_ID),
        protein_path,
        _load_specification(mrna_path, MRNA_PRODUCT_STAGE_ID),
        mrna_path,
    )
