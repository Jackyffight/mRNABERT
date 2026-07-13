"""Semantic verification for the combined Stage 4/5 continuation run."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .assessment_specs import DEVELOPABILITY_STAGE_ID, IMMUNE_STAGE_ID
from .config import load_project_config
from .post_structure_assessment import _developability_analysis, _immune_analysis
from .structure_metrics import parse_ca_pdb


REQUIRED_NODE_FILES = {
    IMMUNE_STAGE_ID: {
        "summary.json", "report.html", "input_audit.json", "process_record.json",
        "output_audit.json", "human_actions.json", "handoff.json",
        "immune_evidence.json", "immune_candidates.csv", "immune_requirements.csv",
        "inputs/immune_specification.json",
    },
    DEVELOPABILITY_STAGE_ID: {
        "summary.json", "report.html", "input_audit.json", "process_record.json",
        "output_audit.json", "human_actions.json", "handoff.json",
        "developability_assessments.json", "developability_candidates.csv",
        "liabilities.csv", "inputs/developability_specification.json",
    },
}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _without_nonsemantic_paths(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_nonsemantic_paths(item)
            for key, item in value.items()
            if key != "alignment_path"
        }
    if isinstance(value, list):
        return [_without_nonsemantic_paths(item) for item in value]
    return value


def _snapshot_path(node_dir: Path, entry: dict[str, Any]) -> Path:
    relative = entry.get("snapshot_path")
    if not isinstance(relative, str) or not relative:
        raise ValueError("Input audit entry has no snapshot_path")
    path = (node_dir / relative).resolve()
    if not path.is_relative_to(node_dir) or not path.is_file():
        raise ValueError(f"Input snapshot is missing or outside node: {relative}")
    return path


def _rewrite_immune_spec_paths(
    spec: dict[str, Any],
    input_audit: dict[str, Any],
    node_dir: Path,
) -> dict[str, Any]:
    rewritten = copy.deepcopy(spec)
    inputs = input_audit.get("inputs", {})
    for source_id, declaration in rewritten["pathogen_panel"]["source_alignments"].items():
        key = f"alignment:{source_id}"
        if key in inputs:
            declaration["alignment_path"] = str(_snapshot_path(node_dir, inputs[key]))
    if "mhc_panel" in inputs:
        rewritten["host"]["mhc_panel_path"] = str(
            _snapshot_path(node_dir, inputs["mhc_panel"])
        )
    for adapter_id, declaration in rewritten["adapters"].items():
        key = f"adapter:{adapter_id}"
        if key in inputs:
            declaration["result_path"] = str(_snapshot_path(node_dir, inputs[key]))
    return rewritten


def _rewrite_developability_spec_paths(
    spec: dict[str, Any],
    input_audit: dict[str, Any],
    node_dir: Path,
) -> dict[str, Any]:
    rewritten = copy.deepcopy(spec)
    inputs = input_audit.get("inputs", {})
    for adapter_id, declaration in rewritten["external_adapters"].items():
        key = f"adapter:{adapter_id}"
        if key in inputs:
            declaration["result_path"] = str(_snapshot_path(node_dir, inputs[key]))
    return rewritten


def verify_post_structure_run(
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
        "stage4-5-no-symlinks", not symlinks, "No symlinked artifacts", f"Symlinks: {symlinks}"
    )
    for stage_id, required in REQUIRED_NODE_FILES.items():
        node = root / "nodes" / stage_id
        actual = {
            path.relative_to(node).as_posix()
            for path in node.rglob("*")
            if path.is_file()
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
        verifier.fail("stage4-5-root-json", str(error))
        return verifier.result(root, run_id)
    verifier.check(
        "stage4-5-root-identity",
        manifest.get("run_id") == run_id
        and manifest.get("current_stage") == DEVELOPABILITY_STAGE_ID
        and manifest.get("executed_stages")
        == [IMMUNE_STAGE_ID, DEVELOPABILITY_STAGE_ID]
        and index.get("run_id") == run_id,
        "Manifest identifies the combined Stage 4/5 run",
        "Manifest or index identity mismatch",
    )
    try:
        rebuilt = build_artifact_index(root, manifest["project_id"], run_id)
        integrity = rebuilt == index
    except (OSError, ValueError, KeyError):
        integrity = False
    verifier.check(
        "stage4-5-artifact-integrity",
        integrity,
        "Every artifact matches the SHA256 index",
        "Artifact index differs from current files",
    )
    verifier.check(
        "stage4-5-workflow-contract",
        _workflow_blueprint_matches(workflow)
        and workflow.get("run_id") == run_id
        and workflow.get("current_stage") == DEVELOPABILITY_STAGE_ID,
        "Workflow matches the frozen contract",
        "Workflow contract or current stage mismatch",
    )
    lineage = manifest.get("lineage", {})
    parent_path = Path(str(lineage.get("parent_run_path", ""))).expanduser().resolve()
    try:
        parent_index_snapshot = (
            root / "inputs/lineage/stage3_parent_artifact_index.json"
        )
        parent_manifest_snapshot = root / "inputs/lineage/stage3_parent_manifest.json"
        parent_index = _load(parent_index_snapshot)
        parent_manifest = _load(parent_manifest_snapshot)
        parent_entries = parent_index["artifacts"]
        parent_seal = (
            parent_manifest.get("run_id") == lineage.get("parent_run_id")
            and sha256_file(parent_index_snapshot)
            == lineage.get("parent_artifact_index_sha256")
            and sha256_file(parent_manifest_snapshot)
            == parent_entries["manifest.json"]["sha256"]
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        parent_index = {}
        parent_entries = {}
        parent_seal = False
    verifier.check(
        "stage4-5-parent-seal",
        parent_seal,
        "Parent manifest and index snapshots are sealed",
        "Parent seal mismatch",
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
        "stage4-5-parent-artifacts-copied",
        copied_parent,
        "Copied Stage 1-3 artifacts match the parent index",
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
            "stage4-5-external-parent",
            external_ok,
            "External Stage 3 parent remains valid",
            f"External parent missing, invalid, or changed: {parent_path}",
        )

    immune_node = root / "nodes" / IMMUNE_STAGE_ID
    developability_node = root / "nodes" / DEVELOPABILITY_STAGE_ID
    try:
        config = load_project_config(root / "inputs/project.json")
        candidate_batch_path = root / "nodes/candidate_specification/candidate_batch.json"
        candidate_batch = _load(candidate_batch_path)
        structure_document = _load(
            root / "nodes/protein_structure_assessment/structure_assessments.json"
        )
        structure_by_id = {
            item["candidate_id"]: item for item in structure_document["assessments"]
        }
        candidate_ids = [item["candidate_id"] for item in candidate_batch["candidates"]]
        structures = {
            candidate_id: parse_ca_pdb(
                root
                / "nodes/protein_structure_assessment/structures"
                / f"{candidate_id}.pdb"
            )
            for candidate_id in candidate_ids
        }
        immune_stored = _load(immune_node / "immune_evidence.json")
        developability_stored = _load(
            developability_node / "developability_assessments.json"
        )
        immune_input_audit = _load(immune_node / "input_audit.json")
        developability_input_audit = _load(developability_node / "input_audit.json")
        immune_spec = _rewrite_immune_spec_paths(
            _load(immune_node / "inputs/immune_specification.json"),
            immune_input_audit,
            immune_node,
        )
        developability_spec = _rewrite_developability_spec_paths(
            _load(developability_node / "inputs/developability_specification.json"),
            developability_input_audit,
            developability_node,
        )
        recompute_inputs: dict[str, Path] = {}
        candidate_batch_sha = sha256_file(candidate_batch_path)
        immune_recomputed = _immune_analysis(
            config,
            immune_spec,
            candidate_batch,
            structure_by_id,
            structures,
            recompute_inputs,
            candidate_batch_sha,
        )
        developability_recomputed = _developability_analysis(
            config,
            developability_spec,
            candidate_batch,
            structure_by_id,
            recompute_inputs,
            candidate_batch_sha,
        )
        semantic_loaded = True
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        semantic_loaded = False
        immune_recomputed = {}
        developability_recomputed = {}
        immune_stored = {}
        developability_stored = {}
    verifier.check(
        "stage4-5-semantic-recompute",
        semantic_loaded,
        "Both stages recomputed from copied candidates, structures, specs, and evidence",
        "Stage 4/5 deterministic recomputation failed",
    )
    verifier.check(
        "stage4-immune-reproducibility",
        semantic_loaded
        and _without_nonsemantic_paths(immune_stored)
        == _without_nonsemantic_paths(immune_recomputed),
        "Stored immune evidence exactly matches deterministic recomputation",
        "Stored immune evidence differs from deterministic recomputation",
    )
    verifier.check(
        "stage5-developability-reproducibility",
        semantic_loaded and developability_stored == developability_recomputed,
        "Stored developability assessments exactly match deterministic recomputation",
        "Stored developability assessments differ from deterministic recomputation",
    )
    try:
        immune_handoff = _load(immune_node / "handoff.json")
        developability_handoff = _load(developability_node / "handoff.json")
        handoff_ok = (
            immune_handoff.get("carried_forward", {}).get("immune_evidence_sha256")
            == sha256_file(immune_node / "immune_evidence.json")
            and developability_handoff.get("carried_forward", {}).get(
                "developability_assessments_sha256"
            )
            == sha256_file(developability_node / "developability_assessments.json")
        )
    except (OSError, ValueError, json.JSONDecodeError):
        handoff_ok = False
    verifier.check(
        "stage4-5-handoff-seals",
        handoff_ok,
        "Both handoffs seal their deterministic result artifacts",
        "Stage 4/5 handoff result hashes differ",
    )
    return verifier.result(root, run_id)
