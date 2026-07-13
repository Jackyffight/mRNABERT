"""Semantic verifier for immutable Stage 3 continuation runs."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .structure_job import _document_sha256, _identity
from .structure_metrics import (
    RULESET_ID,
    add_source_geometry_comparisons,
    assess_candidate_structure,
    parse_ca_pdb,
)


STRUCTURE_STAGE_ID = "protein_structure_assessment"
CANDIDATE_STAGE_ID = "candidate_specification"
REQUIRED_NODE_FILES = {
    "summary.json",
    "report.html",
    "input_audit.json",
    "process_record.json",
    "output_audit.json",
    "human_actions.json",
    "handoff.json",
    "structure_assessments.json",
    "structures.csv",
    "components.csv",
    "boundaries.csv",
    "source_comparisons.csv",
    "findings.csv",
    "inputs/job-manifest.json",
    "inputs/sequences.fasta",
    "inputs/result-archive.tar.gz",
    "model_results/run-manifest.json",
    "model_results/summary.json",
}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def verify_structure_run(
    run_dir: Path,
    *,
    check_external_inputs: bool,
) -> dict[str, Any]:
    # Imported lazily by verification.verify_run to avoid a module cycle.
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
        "no-symlinks",
        not symlinks,
        "Run contains no symlinked artifacts",
        f"Symlinked artifacts are not allowed: {symlinks}",
    )
    node_dir = root / "nodes" / STRUCTURE_STAGE_ID
    actual_node_files = {
        path.relative_to(node_dir).as_posix()
        for path in node_dir.rglob("*")
        if path.is_file()
    } if node_dir.is_dir() else set()
    verifier.check(
        "stage3-required-artifacts",
        REQUIRED_NODE_FILES <= actual_node_files,
        "All required Stage 3 artifacts are present",
        f"Missing Stage 3 artifacts: {sorted(REQUIRED_NODE_FILES - actual_node_files)}",
    )

    try:
        manifest = _load(root / "manifest.json")
        workflow = _load(root / "workflow.json")
        index = _load(root / ARTIFACT_INDEX_FILENAME)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        verifier.fail("stage3-root-json", str(error))
        return verifier.result(root, run_id)
    verifier.check(
        "stage3-manifest-identity",
        manifest.get("run_id") == run_id
        and manifest.get("current_stage") == STRUCTURE_STAGE_ID
        and manifest.get("project_id") == index.get("project_id")
        and index.get("run_id") == run_id,
        "Manifest and artifact index identify this Stage 3 run",
        "Manifest/artifact index identity mismatch",
    )
    try:
        rebuilt_index = build_artifact_index(root, manifest["project_id"], run_id)
        index_matches = index == rebuilt_index
    except (OSError, ValueError) as error:
        index_matches = False
        verifier.fail("stage3-artifact-index-build", str(error))
    verifier.check(
        "stage3-artifact-integrity",
        index_matches,
        "Every Stage 3 artifact size and SHA256 matches the index",
        "Stage 3 artifact index differs from the current files",
    )
    verifier.check(
        "stage3-workflow-contract",
        _workflow_blueprint_matches(workflow)
        and workflow.get("run_id") == run_id
        and workflow.get("current_stage") == STRUCTURE_STAGE_ID,
        "Workflow matches the frozen contract and current stage",
        "Workflow contract or current-stage identity mismatch",
    )

    lineage = manifest.get("lineage", {})
    parent_path = Path(str(lineage.get("parent_run_path", ""))).expanduser().resolve()
    parent_index_snapshot = root / "inputs" / "parent_run_artifact_index.json"
    parent_manifest_snapshot = root / "inputs" / "parent_run_manifest.json"
    try:
        parent_index = _load(parent_index_snapshot)
        parent_manifest = _load(parent_manifest_snapshot)
        parent_seal_ok = (
            parent_manifest.get("run_id") == lineage.get("parent_run_id")
            and sha256_file(parent_index_snapshot)
            == lineage.get("parent_artifact_index_sha256")
            and parent_index.get("run_id") == lineage.get("parent_run_id")
        )
    except (OSError, ValueError, json.JSONDecodeError):
        parent_index = {}
        parent_seal_ok = False
    verifier.check(
        "stage3-parent-seal",
        parent_seal_ok,
        "Parent manifest and artifact index snapshots match lineage",
        "Parent seal snapshots do not match Stage 3 lineage",
    )
    copied_parent_ok = True
    parent_entries = parent_index.get("artifacts", {}) if isinstance(parent_index, dict) else {}
    if isinstance(parent_entries, dict):
        for relative, identity in parent_entries.items():
            if not relative.startswith(("inputs/", "nodes/")):
                continue
            copied = root / relative
            if (
                not copied.is_file()
                or copied.stat().st_size != identity.get("size_bytes")
                or sha256_file(copied) != identity.get("sha256")
            ):
                copied_parent_ok = False
                break
    else:
        copied_parent_ok = False
    verifier.check(
        "stage3-parent-artifacts-copied",
        copied_parent_ok,
        "Copied Stage 1/2 artifacts match the sealed parent index",
        "Copied parent artifacts differ from the sealed parent index",
    )
    if check_external_inputs:
        parent_external_ok = False
        if parent_path.is_dir():
            parent_verification = verify_run(parent_path)
            parent_external_ok = (
                parent_verification["status"] == "pass"
                and sha256_file(parent_path / ARTIFACT_INDEX_FILENAME)
                == lineage.get("parent_artifact_index_sha256")
            )
        verifier.check(
            "stage3-external-parent",
            parent_external_ok,
            "External parent run remains valid and checksum-identical",
            f"External parent run is missing, invalid, or changed: {parent_path}",
        )

    try:
        candidate_batch = _load(
            root / "nodes" / CANDIDATE_STAGE_ID / "candidate_batch.json"
        )
        job = _load(node_dir / "inputs" / "job-manifest.json")
        gpu_manifest = _load(node_dir / "model_results" / "run-manifest.json")
        gpu_summary = _load(node_dir / "model_results" / "summary.json")
        assessments_document = _load(node_dir / "structure_assessments.json")
        input_audit = _load(node_dir / "input_audit.json")
        output_audit = _load(node_dir / "output_audit.json")
        handoff = _load(node_dir / "handoff.json")
        summary = _load(node_dir / "summary.json")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        verifier.fail("stage3-node-json", str(error))
        return verifier.result(root, run_id)
    verifier.check(
        "stage3-job-and-gpu-identity",
        job.get("job_identity") == _identity(job, "job_identity")
        and gpu_manifest.get("run_identity") == _identity(gpu_manifest, "run_identity")
        and gpu_manifest.get("job_identity") == job.get("job_identity")
        and gpu_manifest.get("job_manifest_sha256") == _document_sha256(job)
        and gpu_manifest.get("candidate_ids")
        == [record.get("candidate_id") for record in job.get("records", [])],
        "Job and GPU run identities are valid and linked",
        "Job or GPU run identity/candidate linkage mismatch",
    )
    candidate_by_id = {
        candidate.get("candidate_id"): candidate
        for candidate in candidate_batch.get("candidates", [])
        if isinstance(candidate, dict)
    }
    records = job.get("records", [])
    candidate_links_ok = isinstance(records, list) and all(
        isinstance(record, dict)
        and record.get("candidate_id") in candidate_by_id
        and record.get("sequence")
        == candidate_by_id[record["candidate_id"]].get("amino_acid_sequence")
        and record.get("sequence_sha256")
        == candidate_by_id[record["candidate_id"]].get("amino_acid_sha256")
        for record in records
    )
    verifier.check(
        "stage3-candidate-links",
        candidate_links_ok,
        "Every GPU record maps to the exact Stage 2 candidate sequence",
        "GPU records and Stage 2 candidates differ",
    )
    expected_count = len(records) if isinstance(records, list) else 0
    gpu_records = gpu_summary.get("records", {})
    verifier.check(
        "stage3-gpu-summary",
        gpu_summary.get("run_identity") == gpu_manifest.get("run_identity")
        and gpu_summary.get("status") == "passed"
        and gpu_records
        == {
            "selected": expected_count,
            "succeeded": expected_count,
            "failed": 0,
            "pending": 0,
        },
        "GPU summary reports every requested candidate succeeded",
        "GPU summary is incomplete or inconsistent",
    )

    recomputed = []
    parsed_by_id = {}
    raw_valid = True
    for record in records if isinstance(records, list) else []:
        candidate_id = record["candidate_id"]
        result_path = node_dir / "model_results" / "records" / candidate_id / "result.json"
        model_pdb = node_dir / "model_results" / "records" / candidate_id / "prediction.pdb"
        structure_pdb = node_dir / "structures" / f"{candidate_id}.pdb"
        try:
            result = _load(result_path)
            artifact = result.get("artifact", {})
            if (
                result.get("run_identity") != gpu_manifest.get("run_identity")
                or result.get("candidate_id") != candidate_id
                or result.get("sequence_sha256") != record.get("sequence_sha256")
                or result.get("status") != "succeeded"
                or artifact.get("sha256") != sha256_file(model_pdb)
                or artifact.get("bytes") != model_pdb.stat().st_size
                or sha256_file(structure_pdb) != sha256_file(model_pdb)
            ):
                raise ValueError("raw result identity mismatch")
            parsed = parse_ca_pdb(structure_pdb)
            assessment = assess_candidate_structure(
                candidate_by_id[candidate_id], parsed, result
            )
            assessment["pdb_sha256"] = sha256_file(structure_pdb)
            assessment["pdb_bytes"] = structure_pdb.stat().st_size
            assessment["runtime_seconds"] = round(float(result["runtime_seconds"]), 6)
            parsed_by_id[candidate_id] = parsed
            recomputed.append(assessment)
        except (OSError, ValueError, KeyError, TypeError):
            raw_valid = False
            break
    if raw_valid:
        selected_candidates = [candidate_by_id[record["candidate_id"]] for record in records]
        try:
            add_source_geometry_comparisons(selected_candidates, recomputed, parsed_by_id)
        except (ValueError, KeyError, TypeError):
            raw_valid = False
    verifier.check(
        "stage3-pdb-semantic-recompute",
        raw_valid,
        "Every PDB sequence and deterministic metric recomputed successfully",
        "PDB/result semantics could not be recomputed",
    )
    expected_assessments = []
    if raw_valid:
        for item in recomputed:
            expected = copy.deepcopy(item)
            candidate_id = item["candidate_id"]
            expected["structure_artifact"] = {
                "path": f"structures/{candidate_id}.pdb",
                "sha256": item["pdb_sha256"],
                "bytes": item["pdb_bytes"],
            }
            expected["raw_result_path"] = (
                f"model_results/records/{candidate_id}/result.json"
            )
            expected_assessments.append(expected)
    verifier.check(
        "stage3-assessment-reproducibility",
        assessments_document.get("ruleset_id") == RULESET_ID
        and assessments_document.get("assessments") == expected_assessments,
        "Stored structure assessments exactly match deterministic recomputation",
        "Stored structure assessments differ from deterministic recomputation",
    )
    stored_assessments = assessments_document.get("assessments", [])
    output_rows = output_audit.get("candidates", [])
    verifier.check(
        "stage3-output-cross-references",
        summary.get("candidate_count") == expected_count
        and len(stored_assessments) == expected_count
        and [row.get("candidate_id") for row in output_rows]
        == [record.get("candidate_id") for record in records]
        and all(row.get("status") == "assessed" for row in stored_assessments),
        "Summary, output audit, and assessments cover the same candidates",
        "Stage 3 output candidate cross-references differ",
    )
    verifier.check(
        "stage3-handoff-assessment-seal",
        handoff.get("carried_forward", {}).get("structure_assessments_sha256")
        == sha256_file(node_dir / "structure_assessments.json")
        and set(handoff.get("carried_forward", {}).get("candidate_ids", []))
        == {record.get("candidate_id") for record in records},
        "Handoff seals the assessment artifact and candidate set",
        "Handoff assessment hash or candidate set mismatch",
    )
    result_archive = node_dir / "inputs" / "result-archive.tar.gz"
    verifier.check(
        "stage3-result-archive-seal",
        input_audit.get("result_archive", {}).get("sha256")
        == sha256_file(result_archive)
        and manifest.get("inputs", {}).get("result_archive_sha256")
        == sha256_file(result_archive),
        "Result archive SHA256 is consistent across manifest and input audit",
        "Result archive SHA256 cross-reference mismatch",
    )
    return verifier.result(root, run_id)
