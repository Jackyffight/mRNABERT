"""Semantic verifier for the combined Stage 6 product-design run."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .design_loop import validate_redesign_request_document
from .config import load_project_config
from .product_design import (
    _apply_upstream_developability_requirement,
    _load_codon_usage,
    _mrna_analysis,
    _protein_analysis,
)
from .product_reporting import _fasta
from .product_specs import MRNA_PRODUCT_STAGE_ID, PROTEIN_PRODUCT_STAGE_ID


REQUIRED_NODE_FILES = {
    PROTEIN_PRODUCT_STAGE_ID: {
        "summary.json", "report.html", "input_audit.json", "process_record.json",
        "output_audit.json", "human_actions.json", "handoff.json", "protein_products.json",
        "products.csv", "expression_constructs.fasta", "final_products.fasta",
        "coding_sequences.fasta", "structure_recheck_candidates.fasta",
        "structure_recheck_job.json", "inputs/protein_specification.json",
    },
    MRNA_PRODUCT_STAGE_ID: {
        "summary.json", "report.html", "input_audit.json", "process_record.json",
        "output_audit.json", "human_actions.json", "handoff.json", "mrna_products.json",
        "designs.csv", "coding_designs.fasta", "full_mrna_designs.fasta",
        "rejected_designs.csv", "inputs/mrna_specification.json",
    },
}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _snapshot_path(node: Path, entry: dict[str, Any]) -> Path:
    relative = entry.get("snapshot_path")
    if not isinstance(relative, str) or not relative:
        raise ValueError("Input audit entry has no snapshot_path")
    path = (node / relative).resolve()
    if not path.is_relative_to(node) or not path.is_file():
        raise ValueError(f"Input snapshot is missing or outside node: {relative}")
    return path


def _rewrite_protein_paths(
    spec: dict[str, Any], audit: dict[str, Any], node: Path
) -> dict[str, Any]:
    rewritten = copy.deepcopy(spec)
    inputs = audit.get("inputs", {})
    if "protein_codon_usage" in inputs:
        rewritten["codon_usage_table_path"] = str(
            _snapshot_path(node, inputs["protein_codon_usage"])
        )
    for candidate_id, declaration in rewritten["constructs"].items():
        key = f"protein_cds:{candidate_id}"
        if key in inputs:
            declaration["coding_sequence_path"] = str(_snapshot_path(node, inputs[key]))
    for adapter_id, declaration in rewritten["external_adapters"].items():
        key = f"protein_adapter:{adapter_id}"
        if key in inputs:
            declaration["result_path"] = str(_snapshot_path(node, inputs[key]))
    return rewritten


def _rewrite_mrna_paths(
    spec: dict[str, Any], audit: dict[str, Any], node: Path
) -> dict[str, Any]:
    rewritten = copy.deepcopy(spec)
    inputs = audit.get("inputs", {})
    if "mrna_codon_usage" in inputs:
        rewritten["codon_usage_table_path"] = str(
            _snapshot_path(node, inputs["mrna_codon_usage"])
        )
    for declaration in rewritten.get("provided_coding_sequences", []):
        key = f"mrna_control:{declaration.get('control_id')}"
        if key in inputs:
            declaration["sequence_path"] = str(_snapshot_path(node, inputs[key]))
    for adapter_id, declaration in rewritten["external_adapters"].items():
        key = f"mrna_adapter:{adapter_id}"
        if key in inputs:
            declaration["result_path"] = str(_snapshot_path(node, inputs[key]))
    return rewritten


def verify_product_run(
    run_dir: Path,
    *,
    check_external_inputs: bool,
) -> dict[str, Any]:
    from .verification import (
        ARTIFACT_INDEX_FILENAME,
        _Verification,
        _workflow_blueprint_matches,
        build_artifact_index,
        sha256_file,
        verify_run,
    )

    root = run_dir.expanduser().resolve()
    verifier = _Verification()
    run_id = root.name
    if not root.is_dir():
        verifier.fail("run-directory", f"Run directory does not exist: {root}")
        return verifier.result(root, run_id)
    symlinks = [path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_symlink()]
    verifier.check(
        "stage6-no-symlinks", not symlinks, "No symlinked artifacts", f"Symlinks: {symlinks}"
    )
    for stage_id, required in REQUIRED_NODE_FILES.items():
        node = root / "nodes" / stage_id
        actual = {
            path.relative_to(node).as_posix() for path in node.rglob("*") if path.is_file()
        } if node.is_dir() else set()
        verifier.check(
            f"{stage_id}-required-artifacts",
            required <= actual,
            f"All required {stage_id} artifacts are present",
            f"Missing artifacts: {sorted(required - actual)}",
        )
    try:
        manifest = _load(root / "manifest.json")
        workflow = _load(root / "workflow.json")
        index = _load(root / ARTIFACT_INDEX_FILENAME)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        verifier.fail("stage6-root-json", str(error))
        return verifier.result(root, run_id)
    verifier.check(
        "stage6-root-identity",
        manifest.get("run_id") == run_id
        and manifest.get("current_stage") == MRNA_PRODUCT_STAGE_ID
        and manifest.get("executed_stages")
        == [PROTEIN_PRODUCT_STAGE_ID, MRNA_PRODUCT_STAGE_ID]
        and index.get("run_id") == run_id,
        "Manifest identifies the combined Stage 6 run",
        "Manifest or index identity mismatch",
    )
    try:
        integrity = build_artifact_index(root, manifest["project_id"], run_id) == index
    except (OSError, ValueError, KeyError):
        integrity = False
    verifier.check(
        "stage6-artifact-integrity", integrity,
        "Every artifact matches the SHA256 index", "Artifact index differs from current files",
    )
    verifier.check(
        "stage6-workflow-contract",
        _workflow_blueprint_matches(workflow)
        and workflow.get("run_id") == run_id
        and workflow.get("current_stage") == MRNA_PRODUCT_STAGE_ID,
        "Workflow matches the frozen contract", "Workflow contract or current stage mismatch",
    )
    lineage = manifest.get("lineage", {})
    parent_path = Path(str(lineage.get("parent_run_path", ""))).expanduser().resolve()
    try:
        parent_index_snapshot = root / "inputs/lineage/stage5_parent_artifact_index.json"
        parent_manifest_snapshot = root / "inputs/lineage/stage5_parent_manifest.json"
        parent_index = _load(parent_index_snapshot)
        parent_manifest = _load(parent_manifest_snapshot)
        parent_entries = parent_index["artifacts"]
        parent_seal = (
            parent_manifest.get("run_id") == lineage.get("parent_run_id")
            and sha256_file(parent_index_snapshot) == lineage.get("parent_artifact_index_sha256")
            and sha256_file(parent_manifest_snapshot) == parent_entries["manifest.json"]["sha256"]
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        parent_entries = {}
        parent_seal = False
    verifier.check(
        "stage6-parent-seal", parent_seal,
        "Stage 5 parent snapshots are sealed", "Parent seal mismatch",
    )
    copied_parent = bool(parent_entries)
    for relative, identity in parent_entries.items():
        if not relative.startswith(("inputs/", "nodes/")):
            continue
        copied = root / relative
        if (
            not copied.is_file()
            or copied.stat().st_size != identity.get("size_bytes")
            or sha256_file(copied) != identity.get("sha256")
        ):
            copied_parent = False
            break
    verifier.check(
        "stage6-parent-artifacts-copied", copied_parent,
        "Copied Stage 1-5 artifacts match the parent index",
        "Copied parent artifacts differ from the sealed index",
    )
    if check_external_inputs:
        external_ok = False
        if parent_path.is_dir():
            parent_result = verify_run(parent_path)
            external_ok = (
                parent_result["status"] == "pass"
                and sha256_file(parent_path / ARTIFACT_INDEX_FILENAME)
                == lineage.get("parent_artifact_index_sha256")
            )
        verifier.check(
            "stage6-external-parent", external_ok,
            "External Stage 4/5 parent remains valid",
            f"External parent missing, invalid, or changed: {parent_path}",
        )
    protein_node = root / "nodes" / PROTEIN_PRODUCT_STAGE_ID
    mrna_node = root / "nodes" / MRNA_PRODUCT_STAGE_ID
    try:
        config = load_project_config(root / "inputs/project.json")
        candidate_batch = _load(root / "nodes/candidate_specification/candidate_batch.json")
        protein_audit = _load(protein_node / "input_audit.json")
        mrna_audit = _load(mrna_node / "input_audit.json")
        protein_spec_path = protein_node / "inputs/protein_specification.json"
        mrna_spec_path = mrna_node / "inputs/mrna_specification.json"
        protein_spec = _rewrite_protein_paths(
            _load(protein_spec_path), protein_audit, protein_node
        )
        mrna_spec = _rewrite_mrna_paths(_load(mrna_spec_path), mrna_audit, mrna_node)
        protein_codon = (
            _load_codon_usage(Path(protein_spec["codon_usage_table_path"]))
            if protein_spec.get("codon_usage_table_path") else None
        )
        mrna_codon = (
            _load_codon_usage(Path(mrna_spec["codon_usage_table_path"]))
            if mrna_spec.get("codon_usage_table_path") else None
        )
        recompute_inputs: dict[str, Path] = {}
        protein_recomputed = _protein_analysis(
            config, protein_spec, protein_spec_path, candidate_batch, protein_codon, recompute_inputs
        )
        mrna_recomputed = _mrna_analysis(
            config, mrna_spec, mrna_spec_path, candidate_batch, mrna_codon, recompute_inputs
        )
        _apply_upstream_developability_requirement(
            root, protein_recomputed, mrna_recomputed
        )
        protein_stored = _load(protein_node / "protein_products.json")
        mrna_stored = _load(mrna_node / "mrna_products.json")
        semantic_loaded = True
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        semantic_loaded = False
        protein_recomputed = mrna_recomputed = protein_stored = mrna_stored = {}
    verifier.check(
        "stage6-semantic-recompute", semantic_loaded,
        "Both product branches recomputed from copied specifications and inputs",
        "Stage 6 deterministic recomputation failed",
    )
    workflow_version = workflow.get("workflow_version")
    is_v2_workflow = isinstance(workflow_version, int) and workflow_version >= 2
    if is_v2_workflow:
        round_id = str(candidate_batch.get("design_round_id")) if semantic_loaded else ""
        for stage_id, node in (
            (PROTEIN_PRODUCT_STAGE_ID, protein_node),
            (MRNA_PRODUCT_STAGE_ID, mrna_node),
        ):
            try:
                redesign_requests = _load(node / "redesign_requests.json")
            except (OSError, ValueError, json.JSONDecodeError):
                redesign_requests = {}
            verifier.check(
                f"{stage_id}-redesign-requests",
                bool(round_id)
                and validate_redesign_request_document(
                    redesign_requests,
                    project_id=str(manifest.get("project_id")),
                    run_id=run_id,
                    round_id=round_id,
                    stage_id=stage_id,
                ),
                f"{stage_id} redesign requests are schema-valid",
                f"{stage_id} redesign-request artifact is missing or invalid",
            )
    verifier.check(
        "stage6-protein-reproducibility",
        semantic_loaded and protein_stored == protein_recomputed,
        "Stored protein products exactly match deterministic recomputation",
        "Stored protein products differ from deterministic recomputation",
    )
    verifier.check(
        "stage6-mrna-reproducibility",
        semantic_loaded and mrna_stored == mrna_recomputed,
        "Stored mRNA products exactly match deterministic recomputation",
        "Stored mRNA products differ from deterministic recomputation",
    )
    output_payloads_ok = False
    if semantic_loaded:
        products = protein_recomputed["products"]
        designs = mrna_recomputed["designs"]
        try:
            output_payloads_ok = (
                (protein_node / "expression_constructs.fasta").read_text(encoding="utf-8")
                == _fasta([(item["design_id"], item["expression_sequence"]) for item in products])
                and (protein_node / "final_products.fasta").read_text(encoding="utf-8")
                == _fasta([(item["design_id"], item["final_product_sequence"]) for item in products])
                and (mrna_node / "coding_designs.fasta").read_text(encoding="utf-8")
                == _fasta([(item["design_id"], item["coding_sequence_dna"]) for item in designs])
            )
        except OSError:
            output_payloads_ok = False
    verifier.check(
        "stage6-output-payloads", output_payloads_ok,
        "Model/synthesis payloads match exact recomputed sequences",
        "One or more Stage 6 FASTA payloads differ from recomputed products",
    )
    try:
        protein_handoff = _load(protein_node / "handoff.json")
        mrna_handoff = _load(mrna_node / "handoff.json")
        handoff_ok = (
            protein_handoff["carried_forward"]["result_sha256"]
            == sha256_file(protein_node / "protein_products.json")
            and mrna_handoff["carried_forward"]["result_sha256"]
            == sha256_file(mrna_node / "mrna_products.json")
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        handoff_ok = False
    verifier.check(
        "stage6-handoff-seals", handoff_ok,
        "Both Stage 6 handoffs seal their result artifacts",
        "Stage 6 handoff result hashes differ",
    )
    return verifier.result(root, run_id)
