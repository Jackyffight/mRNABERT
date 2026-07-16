"""Versioned specifications for the parallel Stage 6 product-design branches."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from .assessment_specs import DEVELOPABILITY_STAGE_ID, load_structure_candidate_scope
from .config import ProjectConfig, load_project_config
from .stage6_routing import (
    archive_runtime_file,
    initialize_stage6_routing,
    routing_descriptor,
)
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


def _candidate_bindings(
    source_run: Path,
    routing_manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    scoped_batch = load_structure_candidate_scope(source_run)["candidate_batch"]
    candidate_by_id = {
        candidate["candidate_id"]: candidate
        for candidate in scoped_batch["candidates"]
    }
    bindings = []
    for record in routing_manifest["records"]:
        if not record["product_drafting_eligible"]:
            continue
        candidate = candidate_by_id.get(record["candidate_id"])
        if (
            candidate is None
            or candidate["candidate_key"] != record["candidate_key"]
            or candidate["amino_acid_sha256"] != record["amino_acid_sha256"]
        ):
            raise ValueError(
                f"Stage 6 routing differs from active candidate: {record['candidate_id']}"
            )
        bindings.append(
            {
                "candidate_id": candidate["candidate_id"],
                "candidate_key": candidate["candidate_key"],
                "amino_acid_sha256": candidate["amino_acid_sha256"],
                "routing_lane": record["lane"],
                "expensive_followup_eligible": record[
                    "expensive_followup_eligible"
                ],
            }
        )
    if len(bindings) != routing_manifest["counts"]["product_drafting"]:
        raise ValueError("Stage 6 routing product-drafting count mismatch")
    return bindings


def default_protein_product_specification(
    config: ProjectConfig,
    source_run: Path,
    routing: dict[str, Any],
) -> dict[str, Any]:
    bindings = _candidate_bindings(source_run, routing["manifest"])
    context_declared = config.protein_expression_host.strip().lower() != "unspecified"
    return {
        "schema_version": 2,
        "specification_id": f"{config.project_id}-protein-product-v2",
        "stage_id": PROTEIN_PRODUCT_STAGE_ID,
        "mode": "exploratory",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "routing": routing_descriptor(config, routing),
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
    routing: dict[str, Any],
) -> dict[str, Any]:
    bindings = _candidate_bindings(source_run, routing["manifest"])
    target_declared = config.mrna_target_species.strip().lower() != "unspecified"
    return {
        "schema_version": 2,
        "specification_id": f"{config.project_id}-mrna-product-v2",
        "stage_id": MRNA_PRODUCT_STAGE_ID,
        "mode": "exploratory",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "routing": routing_descriptor(config, routing),
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
        "manufacturing_context": {
            "status": (
                "declared"
                if config.mrna_manufacturing_method
                and config.mrna_manufacturing_method.lower() != "unspecified"
                else "pending"
            ),
            "method": config.mrna_manufacturing_method or "unspecified",
        },
        "provided_coding_sequences": [],
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
    refresh_selection: bool = False,
) -> dict[str, Any]:
    config = load_project_config(Path(project_config))
    source = _resolve_stage5_run(
        config,
        Path(source_run_dir) if source_run_dir is not None else None,
    )
    protein_path = config.runtime_root / PROTEIN_SPEC_RELATIVE
    mrna_path = config.runtime_root / MRNA_SPEC_RELATIVE
    routing = initialize_stage6_routing(
        config,
        source,
        refresh=refresh_selection,
    )
    expected_protein = default_protein_product_specification(
        config, source, routing
    )
    expected_mrna = default_mrna_product_specification(config, source, routing)
    created: list[str] = list(routing["created"])
    archived: list[str] = list(routing["archived"])
    if not protein_path.exists():
        _atomic_json(protein_path, expected_protein)
        created.append(str(protein_path))
    elif not _specification_matches_routing(protein_path, expected_protein):
        if not refresh_selection:
            raise ValueError(
                "Protein Stage 6 specification selection is stale; rerun "
                "init-stage6 with --refresh-selection"
            )
        archived.append(
            str(
                archive_runtime_file(
                    protein_path,
                    config.runtime_root / "input/stage6/history",
                )
            )
        )
        _atomic_json(
            protein_path,
            _refresh_protein_specification(
                _load_json(protein_path), expected_protein
            ),
        )
    if not mrna_path.exists():
        _atomic_json(mrna_path, expected_mrna)
        created.append(str(mrna_path))
    elif not _specification_matches_routing(mrna_path, expected_mrna):
        if not refresh_selection:
            raise ValueError(
                "mRNA Stage 6 specification selection is stale; rerun "
                "init-stage6 with --refresh-selection"
            )
        archived.append(
            str(
                archive_runtime_file(
                    mrna_path,
                    config.runtime_root / "input/stage6/history",
                )
            )
        )
        _atomic_json(
            mrna_path,
            _refresh_mrna_specification(_load_json(mrna_path), expected_mrna),
        )
    return {
        "project_id": config.project_id,
        "source_run": str(source),
        "protein_specification": str(protein_path),
        "mrna_specification": str(mrna_path),
        "routing_policy": str(routing["policy_path"]),
        "routing_manifest": str(routing["manifest_path"]),
        "routing_counts": routing["manifest"]["counts"],
        "created": created,
        "archived": archived,
    }


def _specification_matches_routing(
    path: Path,
    expected: dict[str, Any],
) -> bool:
    try:
        current = _load_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    constructs_match = True
    if "constructs" in expected:
        current_constructs = current.get("constructs")
        constructs_match = (
            isinstance(current_constructs, dict)
            and set(current_constructs)
            == {
                binding["candidate_id"]
                for binding in expected["selection"]["candidates"]
            }
        )
    return (
        current.get("schema_version") == 2
        and current.get("stage_id") == expected["stage_id"]
        and current.get("routing") == expected["routing"]
        and current.get("selection", {}).get("candidates")
        == expected["selection"]["candidates"]
        and constructs_match
    )


def _refresh_protein_specification(
    current: dict[str, Any],
    expected: dict[str, Any],
) -> dict[str, Any]:
    refreshed = dict(expected)
    for field in (
        "expression_context",
        "codon_usage_table_path",
        "policy",
    ):
        if field in current:
            refreshed[field] = current[field]
    current_constructs = current.get("constructs", {})
    if not isinstance(current_constructs, dict):
        current_constructs = {}
    refreshed["constructs"] = {
        candidate_id: current_constructs.get(candidate_id, declaration)
        for candidate_id, declaration in expected["constructs"].items()
    }
    refreshed["selection"] = {
        **expected["selection"],
        "status": "draft",
    }
    return refreshed


def _refresh_mrna_specification(
    current: dict[str, Any],
    expected: dict[str, Any],
) -> dict[str, Any]:
    refreshed = dict(expected)
    for field in (
        "target_context",
        "manufacturing_context",
        "codon_usage_table_path",
        "generation",
        "constraints",
        "noncoding_elements",
        "policy",
    ):
        if field in current:
            refreshed[field] = current[field]
    selected_ids = {
        binding["candidate_id"]
        for binding in expected["selection"]["candidates"]
    }
    current_controls = current.get("provided_coding_sequences", [])
    if not isinstance(current_controls, list):
        current_controls = []
    refreshed["provided_coding_sequences"] = [
        declaration
        for declaration in current_controls
        if isinstance(declaration, dict)
        and declaration.get("candidate_id") in selected_ids
    ]
    refreshed["selection"] = {
        **expected["selection"],
        "status": "draft",
    }
    return refreshed


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
        document.get("schema_version") != 2
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
