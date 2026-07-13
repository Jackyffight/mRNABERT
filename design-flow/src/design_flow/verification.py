"""Integrity and cross-artifact verification for immutable design-flow runs."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from .workflow import (
    CURRENT_STAGE_ID,
    FULL_WORKFLOW,
    SYSTEM_ARCHITECTURE_VERSION,
    WORKFLOW_ID,
    WORKFLOW_VERSION,
    approved_workflow_hash,
    stage_contract,
    validate_workflow,
    workflow_contract_sha256,
)


ARTIFACT_INDEX_FILENAME = "artifact_index.json"
SOURCE_SNAPSHOT_PATHS = {
    "project_config": "inputs/project.json",
    "amino_acid_fasta": "inputs/proteins_aa.fasta",
    "nucleotide_fasta": "inputs/proteins_cds.fasta",
}
NODE_ARTIFACT_NAMES = (
    "summary.json",
    "report.html",
    "input_audit.json",
    "process_record.json",
    "output_audit.json",
    "human_actions.json",
    "handoff.json",
    "proteins.json",
    "proteins.csv",
    "qc_issues.csv",
)
CANDIDATE_STAGE_ID = "candidate_specification"
STRUCTURE_STAGE_ID = "protein_structure_assessment"
DEVELOPABILITY_STAGE_ID = "developability_assessment"
PROTEIN_PRODUCT_STAGE_ID = "protein_product_design"
MRNA_PRODUCT_STAGE_ID = "mrna_product_design"
RANKING_STAGE_ID = "integrated_ranking"
CANDIDATE_NODE_ARTIFACT_NAMES = (
    "summary.json",
    "report.html",
    "input_audit.json",
    "process_record.json",
    "output_audit.json",
    "human_actions.json",
    "handoff.json",
    "candidate_batch.json",
    "candidates.csv",
    "candidate_components.csv",
    "findings.csv",
    "structure_candidates.fasta",
    "model_inputs.json",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _indexed_files(run_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for path in run_dir.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Run artifacts may not contain symlinks: {path}")
        if path.is_file() and path.name != ARTIFACT_INDEX_FILENAME:
            paths.append(path)
    return sorted(paths, key=lambda path: path.relative_to(run_dir).as_posix())


def build_artifact_index(run_dir: Path, project_id: str, run_id: str) -> dict[str, Any]:
    """Build an index over every run file except the index itself."""
    run_dir = run_dir.resolve()
    artifacts: dict[str, dict[str, Any]] = {}
    for path in _indexed_files(run_dir):
        relative_path = path.relative_to(run_dir).as_posix()
        artifacts[relative_path] = {
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
    return {
        "schema_version": 1,
        "project_id": project_id,
        "run_id": run_id,
        "hash_algorithm": "sha256",
        "artifacts": artifacts,
    }


@dataclass
class _Verification:
    checks: list[dict[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def check(
        self,
        check_id: str,
        condition: bool,
        success: str,
        failure: str,
        *,
        warning: bool = False,
    ) -> None:
        if condition:
            self.checks.append({"check_id": check_id, "status": "pass", "detail": success})
            return
        status = "warning" if warning else "fail"
        self.checks.append({"check_id": check_id, "status": status, "detail": failure})
        (self.warnings if warning else self.errors).append(f"{check_id}: {failure}")

    def fail(self, check_id: str, detail: str) -> None:
        self.check(check_id, False, "", detail)

    def result(self, run_dir: Path, run_id: str) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "run_id": run_id,
            "run_path": str(run_dir),
            "status": "fail" if self.errors else "pass",
            "checks": self.checks,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def _load_json(verifier: _Verification, path: Path, check_id: str) -> Any | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        verifier.fail(check_id, f"Cannot read valid JSON from {path}: {error}")
        return None
    verifier.check(check_id, True, f"Loaded {path.name}", "")
    return value


def _safe_run_path(run_dir: Path, relative_path: Any) -> Path | None:
    if not isinstance(relative_path, str) or not relative_path:
        return None
    pure_path = PurePosixPath(relative_path)
    if pure_path.is_absolute() or ".." in pure_path.parts:
        return None
    candidate = run_dir.joinpath(*pure_path.parts).resolve()
    if not candidate.is_relative_to(run_dir):
        return None
    return candidate


def _candidate_triples(records: Any) -> list[tuple[str, str, str]] | None:
    if not isinstance(records, list):
        return None
    triples: list[tuple[str, str, str]] = []
    for record in records:
        if not isinstance(record, dict):
            return None
        values = (record.get("protein_id"), record.get("candidate_id"), record.get("status"))
        if not all(isinstance(value, str) and value for value in values):
            return None
        triples.append(values)
    return triples


def _workflow_blueprint_matches(workflow: dict[str, Any]) -> bool:
    stages = workflow.get("stages")
    if (
        workflow.get("system_architecture_version") != SYSTEM_ARCHITECTURE_VERSION
        or workflow.get("workflow_id") != WORKFLOW_ID
        or workflow.get("workflow_version") != WORKFLOW_VERSION
        or workflow.get("entry_stage") != CURRENT_STAGE_ID
        or workflow.get("contract_sha256") != workflow_contract_sha256()
        or workflow.get("contract_sha256")
        != approved_workflow_hash(
            workflow.get("system_architecture_version"),
            workflow.get("workflow_version"),
        )
        or not isinstance(stages, list)
        or len(stages) != len(FULL_WORKFLOW)
    ):
        return False
    for actual, expected in zip(stages, FULL_WORKFLOW, strict=True):
        if not isinstance(actual, dict):
            return False
        expected_fields = stage_contract(expected)
        if any(actual.get(key) != value for key, value in expected_fields.items()):
            return False
    return True


def _read_csv(verifier: _Verification, path: Path, check_id: str) -> list[dict[str, str]] | None:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, csv.Error) as error:
        verifier.fail(check_id, f"Cannot read CSV from {path}: {error}")
        return None
    verifier.check(check_id, True, f"Loaded {path.name}", "")
    return rows


def verify_run(run_dir: Path, *, check_external_inputs: bool = True) -> dict[str, Any]:
    """Verify file integrity, provenance snapshots, and cross-file semantics."""
    run_dir = run_dir.expanduser().resolve()
    try:
        current_manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        current_manifest = None
    if isinstance(current_manifest, dict) and current_manifest.get("current_stage") == CANDIDATE_STAGE_ID:
        return _verify_candidate_run(
            run_dir,
            check_external_inputs=check_external_inputs,
        )
    if isinstance(current_manifest, dict) and current_manifest.get("current_stage") == STRUCTURE_STAGE_ID:
        from .structure_verification import verify_structure_run

        return verify_structure_run(
            run_dir,
            check_external_inputs=check_external_inputs,
        )
    if (
        isinstance(current_manifest, dict)
        and current_manifest.get("current_stage") == DEVELOPABILITY_STAGE_ID
        and current_manifest.get("executed_stages")
        == ["immune_evidence_assessment", DEVELOPABILITY_STAGE_ID]
    ):
        from .post_structure_verification import verify_post_structure_run

        return verify_post_structure_run(
            run_dir,
            check_external_inputs=check_external_inputs,
        )
    if (
        isinstance(current_manifest, dict)
        and current_manifest.get("current_stage") == MRNA_PRODUCT_STAGE_ID
        and current_manifest.get("executed_stages")
        == [PROTEIN_PRODUCT_STAGE_ID, MRNA_PRODUCT_STAGE_ID]
    ):
        from .product_verification import verify_product_run

        return verify_product_run(
            run_dir,
            check_external_inputs=check_external_inputs,
        )
    if (
        isinstance(current_manifest, dict)
        and current_manifest.get("current_stage") == RANKING_STAGE_ID
        and current_manifest.get("executed_stages") == [RANKING_STAGE_ID]
    ):
        from .ranking_verification import verify_ranking_run

        return verify_ranking_run(
            run_dir,
            check_external_inputs=check_external_inputs,
        )
    verifier = _Verification()
    run_id = run_dir.name
    if not run_dir.is_dir():
        verifier.fail("run-directory", f"Run directory does not exist: {run_dir}")
        return verifier.result(run_dir, run_id)

    expected_files = {
        "manifest.json",
        "workflow.json",
        *SOURCE_SNAPSHOT_PATHS.values(),
        *(
            f"nodes/{CURRENT_STAGE_ID}/{name}"
            for name in NODE_ARTIFACT_NAMES
        ),
    }
    actual_files: set[str] = set()
    symlinks: list[str] = []
    for path in run_dir.rglob("*"):
        relative_path = path.relative_to(run_dir).as_posix()
        if path.is_symlink():
            symlinks.append(relative_path)
        elif path.is_file() and relative_path != ARTIFACT_INDEX_FILENAME:
            actual_files.add(relative_path)
    verifier.check(
        "no-symlinks",
        not symlinks,
        "Run contains no symlinked artifacts",
        f"Symlinked artifacts are not allowed: {symlinks}",
    )
    verifier.check(
        "required-artifacts",
        expected_files <= actual_files,
        "All required run artifacts are present",
        f"Missing required artifacts: {sorted(expected_files - actual_files)}",
    )

    index_path = run_dir / ARTIFACT_INDEX_FILENAME
    index = _load_json(verifier, index_path, "artifact-index-json")
    if isinstance(index, dict):
        entries = index.get("artifacts")
        valid_entries = isinstance(entries, dict) and all(
            isinstance(relative_path, str) and isinstance(identity, dict)
            for relative_path, identity in entries.items()
        )
        verifier.check(
            "artifact-index-shape",
            valid_entries and index.get("hash_algorithm") == "sha256",
            "Artifact index uses SHA-256 and has a path map",
            "Artifact index is malformed or uses an unsupported hash algorithm",
        )
        if valid_entries:
            indexed_paths = set(entries)
            verifier.check(
                "artifact-index-coverage",
                indexed_paths == actual_files,
                f"Artifact index covers all {len(actual_files)} run files",
                (
                    f"Index mismatch: missing={sorted(actual_files - indexed_paths)}, "
                    f"unexpected={sorted(indexed_paths - actual_files)}"
                ),
            )
            for relative_path, identity in sorted(entries.items()):
                artifact_path = _safe_run_path(run_dir, relative_path)
                if artifact_path is None or not artifact_path.is_file():
                    verifier.fail(
                        f"artifact-path:{relative_path}",
                        "Indexed path is unsafe or does not identify a regular file",
                    )
                    continue
                actual_sha256 = sha256_file(artifact_path)
                actual_size = artifact_path.stat().st_size
                verifier.check(
                    f"artifact-integrity:{relative_path}",
                    identity.get("sha256") == actual_sha256
                    and identity.get("size_bytes") == actual_size,
                    f"SHA-256 and size match for {relative_path}",
                    f"SHA-256 or size mismatch for {relative_path}",
                )

    node_dir = run_dir / "nodes" / CURRENT_STAGE_ID
    document_paths = {
        "manifest": run_dir / "manifest.json",
        "workflow": run_dir / "workflow.json",
        "summary": node_dir / "summary.json",
        "input_audit": node_dir / "input_audit.json",
        "process_record": node_dir / "process_record.json",
        "output_audit": node_dir / "output_audit.json",
        "human_actions": node_dir / "human_actions.json",
        "handoff": node_dir / "handoff.json",
        "proteins": node_dir / "proteins.json",
    }
    documents = {
        name: _load_json(verifier, path, f"json:{name}")
        for name, path in document_paths.items()
    }
    if not all(isinstance(document, dict) for document in documents.values()):
        return verifier.result(run_dir, run_id)

    manifest = documents["manifest"]
    workflow = documents["workflow"]
    summary = documents["summary"]
    input_audit = documents["input_audit"]
    process_record = documents["process_record"]
    output_audit = documents["output_audit"]
    human_actions = documents["human_actions"]
    handoff = documents["handoff"]
    proteins = documents["proteins"]

    project_id = manifest.get("project_id")
    manifest_nodes = manifest.get("nodes")
    manifest_nodes = manifest_nodes if isinstance(manifest_nodes, dict) else {}
    verifier.check(
        "run-identity",
        isinstance(project_id, str)
        and project_id
        and manifest.get("run_id") == run_id
        and workflow.get("run_id") == run_id
        and summary.get("run_id") == run_id
        and handoff.get("run_id") == run_id
        and proteins.get("run_id") == run_id
        and proteins.get("project_id") == project_id,
        f"Run and project identity agree on {run_id}",
        "Run or project identity differs across artifacts",
    )
    if isinstance(index, dict):
        verifier.check(
            "artifact-index-identity",
            index.get("run_id") == run_id and index.get("project_id") == project_id,
            "Artifact index identity matches the manifest",
            "Artifact index identity differs from the manifest",
        )

    try:
        validate_workflow()
        workflow_valid = _workflow_blueprint_matches(workflow)
    except ValueError:
        workflow_valid = False
    verifier.check(
        "workflow-blueprint",
        workflow_valid,
        "Workflow stage IDs, order, and dependencies match the validated system DAG",
        "workflow.json differs from the validated system DAG",
    )
    workflow_stages = workflow.get("stages")
    stage_statuses = (
        {stage.get("stage_id"): stage.get("status") for stage in workflow_stages}
        if isinstance(workflow_stages, list) and all(isinstance(stage, dict) for stage in workflow_stages)
        else {}
    )
    verifier.check(
        "stage-identity",
        manifest.get("current_stage") == CURRENT_STAGE_ID
        and workflow.get("current_stage") == CURRENT_STAGE_ID
        and summary.get("stage_id") == CURRENT_STAGE_ID
        and input_audit.get("stage_id") == CURRENT_STAGE_ID
        and process_record.get("stage_id") == CURRENT_STAGE_ID
        and output_audit.get("stage_id") == CURRENT_STAGE_ID
        and human_actions.get("stage_id") == CURRENT_STAGE_ID
        and handoff.get("from_stage") == CURRENT_STAGE_ID
        and proteins.get("stage_id") == CURRENT_STAGE_ID,
        f"All current-node artifacts identify {CURRENT_STAGE_ID}",
        "One or more artifacts identify a different current stage",
    )
    verifier.check(
        "stage-status",
        manifest.get("status") == summary.get("status")
        and isinstance(manifest_nodes.get(CURRENT_STAGE_ID), dict)
        and manifest_nodes[CURRENT_STAGE_ID].get("status")
        == summary.get("status")
        and stage_statuses.get(CURRENT_STAGE_ID) == summary.get("status")
        and all(
            status == "not_evaluated"
            for stage_id, status in stage_statuses.items()
            if stage_id != CURRENT_STAGE_ID
        ),
        "Manifest, node summary, and workflow agree on stage status",
        "Stage status differs across manifest, summary, or workflow",
    )

    input_records = input_audit.get("inputs")
    verifier.check(
        "input-records",
        isinstance(input_records, dict)
        and set(input_records) == set(SOURCE_SNAPSHOT_PATHS)
        and manifest.get("inputs") == input_records,
        "Manifest and input audit carry the same three input identities",
        "Manifest and input audit input identities are missing or inconsistent",
    )
    if isinstance(input_records, dict):
        for input_name, snapshot_relative in SOURCE_SNAPSHOT_PATHS.items():
            record = input_records.get(input_name)
            if not isinstance(record, dict):
                verifier.fail(f"input-snapshot:{input_name}", "Input identity is not an object")
                continue
            snapshot_path = _safe_run_path(run_dir, record.get("snapshot_path"))
            snapshot_matches = (
                record.get("snapshot_path") == snapshot_relative
                and snapshot_path is not None
                and snapshot_path.is_file()
                and record.get("sha256") == sha256_file(snapshot_path)
            )
            verifier.check(
                f"input-snapshot:{input_name}",
                snapshot_matches,
                f"Input snapshot matches recorded SHA-256 for {input_name}",
                f"Input snapshot is missing or differs for {input_name}",
            )
            if check_external_inputs:
                external_path_value = record.get("path")
                external_path = (
                    Path(external_path_value).expanduser()
                    if isinstance(external_path_value, str) and external_path_value
                    else None
                )
                try:
                    external_matches = (
                        external_path is not None
                        and external_path.is_file()
                        and record.get("sha256") == sha256_file(external_path)
                    )
                except OSError:
                    external_matches = False
                verifier.check(
                    f"external-input-current:{input_name}",
                    external_matches,
                    f"Current external source still matches {input_name}",
                    f"External source is unavailable or has drifted for {input_name}",
                    warning=True,
                )

    project_snapshot = _load_json(
        verifier,
        run_dir / SOURCE_SNAPSHOT_PATHS["project_config"],
        "json:project-snapshot",
    )
    manifest_counts = manifest.get("counts")
    manifest_counts = manifest_counts if isinstance(manifest_counts, dict) else {}
    verifier.check(
        "project-snapshot-identity",
        isinstance(project_snapshot, dict)
        and project_snapshot.get("project_id") == project_id
        and project_snapshot.get("expected_protein_count")
        == manifest_counts.get("expected_proteins"),
        "Project snapshot identity and expected count match the manifest",
        "Project snapshot identity or expected count differs from the manifest",
    )

    protein_records = proteins.get("proteins")
    protein_triples = _candidate_triples(protein_records)
    output_triples = _candidate_triples(output_audit.get("candidates"))
    carried_forward = handoff.get("carried_forward")
    handoff_triples = _candidate_triples(
        carried_forward.get("candidates") if isinstance(carried_forward, dict) else None
    )
    verifier.check(
        "candidate-cross-reference",
        protein_triples is not None
        and protein_triples == output_triples == handoff_triples
        and len({candidate_id for _, candidate_id, _ in protein_triples}) == len(protein_triples),
        "Candidate IDs and statuses agree across protein, output-audit, and handoff artifacts",
        "Candidate IDs or statuses differ across protein, output-audit, and handoff artifacts",
    )
    protein_csv_rows = _read_csv(verifier, node_dir / "proteins.csv", "csv:proteins")
    csv_triples = _candidate_triples(protein_csv_rows)
    verifier.check(
        "candidate-csv-cross-reference",
        protein_triples is not None and csv_triples == protein_triples,
        "proteins.csv contains the same candidate identities and statuses",
        "proteins.csv differs from proteins.json",
    )

    raw_findings = input_audit.get("findings")
    findings = raw_findings if isinstance(raw_findings, list) else []
    verifier.check(
        "findings-schema",
        isinstance(raw_findings, list) and all(isinstance(finding, dict) for finding in findings),
        "QC findings are a machine-readable list",
        "QC findings are missing or malformed",
    )
    errors = sum(
        isinstance(finding, dict) and finding.get("severity") == "error" for finding in findings
    )
    warnings = sum(
        isinstance(finding, dict) and finding.get("severity") == "warning" for finding in findings
    )
    paired = len(protein_triples) if protein_triples is not None else -1
    accepted = sum(status == "pass" for _, _, status in protein_triples or [])
    rejected = sum(status == "fail" for _, _, status in protein_triples or [])
    output_summary = output_audit.get("summary")
    output_summary = output_summary if isinstance(output_summary, dict) else {}
    verifier.check(
        "count-consistency",
        manifest_counts.get("paired_proteins") == paired
        and manifest_counts.get("errors") == errors
        and manifest_counts.get("warnings") == warnings
        and output_summary.get("accepted_candidates") == accepted
        and output_summary.get("rejected_candidates") == rejected
        and output_summary.get("errors") == errors
        and output_summary.get("warnings") == warnings
        and summary.get("accepted_candidates") == accepted
        and summary.get("errors") == errors
        and summary.get("warnings") == warnings,
        "Candidate and QC counts agree across all summaries",
        "Candidate or QC counts differ across artifacts",
    )
    computational_status = "fail" if errors else "pass"
    verifier.check(
        "computational-status",
        input_audit.get("status") == computational_status
        and output_audit.get("status") == computational_status
        and summary.get("computational_audit_status") == computational_status,
        f"Computational audit status is consistently {computational_status}",
        "Computational audit status is inconsistent with error findings",
    )
    candidate_issue_statuses_valid = isinstance(protein_records, list)
    if candidate_issue_statuses_valid:
        for record in protein_records:
            if not isinstance(record, dict):
                candidate_issue_statuses_valid = False
                break
            issues = record.get("issues")
            metrics = record.get("metrics")
            if not isinstance(issues, list) or not isinstance(metrics, dict):
                candidate_issue_statuses_valid = False
                break
            expected_status = (
                "fail"
                if any(
                    isinstance(issue, dict) and issue.get("severity") == "error"
                    for issue in issues
                )
                else "pass"
            )
            if record.get("status") != expected_status or (
                expected_status == "pass" and metrics.get("translation_matches") is not True
            ):
                candidate_issue_statuses_valid = False
                break
    verifier.check(
        "candidate-status-derived",
        candidate_issue_statuses_valid,
        "Candidate statuses derive from findings and accepted translations match exactly",
        "Candidate status or accepted translation conflicts with detailed protein evidence",
    )

    qc_rows = _read_csv(verifier, node_dir / "qc_issues.csv", "csv:qc-issues")
    expected_qc_rows = [
        {
            "scope": "protein" if finding.get("protein_id") else "project",
            "protein_id": finding.get("protein_id") or "",
            "severity": finding.get("severity"),
            "code": finding.get("code"),
            "message": finding.get("message"),
        }
        for finding in findings
        if isinstance(finding, dict)
    ]
    verifier.check(
        "qc-csv-cross-reference",
        qc_rows == expected_qc_rows,
        "qc_issues.csv exactly matches machine-readable findings",
        "qc_issues.csv differs from input-audit findings",
    )

    raw_actions = human_actions.get("actions")
    actions = raw_actions if isinstance(raw_actions, list) else []
    valid_action_records = all(isinstance(action, dict) for action in actions)
    action_ids = [action.get("action_id") for action in actions if isinstance(action, dict)]
    known_stage_ids = {stage.stage_id for stage in FULL_WORKFLOW}
    next_stage = summary.get("next_stage")
    open_actions = [
        action for action in actions if isinstance(action, dict) and action.get("status") == "open"
    ]
    due_actions = [
        action
        for action in open_actions
        if action.get("required_before_stage") in {CURRENT_STAGE_ID, next_stage}
    ]
    verifier.check(
        "human-action-schema",
        isinstance(raw_actions, list)
        and valid_action_records
        and len(action_ids) == len(set(action_ids))
        and all(action.get("required_before_stage") in known_stage_ids for action in actions)
        and all(
            action.get("status") != "resolved" or bool(action.get("resolution"))
            for action in actions
        ),
        "Human actions have unique IDs, valid stages, and resolved evidence",
        "Human actions contain duplicate IDs, invalid stages, or unresolved resolutions",
    )
    verifier.check(
        "human-action-counts",
        human_actions.get("open_count") == len(open_actions)
        and human_actions.get("due_before_next_stage_count") == len(due_actions)
        and summary.get("open_human_actions") == len(open_actions)
        and summary.get("due_human_actions") == len(due_actions),
        "Open and due human-action counts agree",
        "Open or due human-action counts differ across artifacts",
    )
    verifier.check(
        "handoff-human-actions",
        handoff.get("blocking_action_ids")
        == [action.get("action_id") for action in due_actions]
        and handoff.get("carried_human_actions") == open_actions,
        "Handoff carries every open action and exactly the actions blocking the next node",
        "Handoff human actions differ from the node action record",
    )
    if computational_status == "fail":
        expected_node_status, expected_readiness = "blocked", "blocked"
    elif due_actions:
        expected_node_status, expected_readiness = "needs_human_input", "needs_human_input"
    else:
        expected_node_status, expected_readiness = "complete", "ready"
    verifier.check(
        "handoff-readiness",
        summary.get("status") == expected_node_status
        and summary.get("handoff_readiness") == expected_readiness
        and handoff.get("readiness") == expected_readiness
        and handoff.get("to_stage") == next_stage
        and next_stage in known_stage_ids,
        f"Node status and handoff readiness correctly resolve to {expected_readiness}",
        "Node status or handoff readiness conflicts with findings and blocking actions",
    )

    if isinstance(input_records, dict) and isinstance(carried_forward, dict):
        expected_digests = {
            name: record.get("sha256")
            for name, record in input_records.items()
            if isinstance(record, dict)
        }
        verifier.check(
            "handoff-provenance",
            carried_forward.get("project_id") == project_id
            and carried_forward.get("input_digests") == expected_digests,
            "Handoff preserves project and input identities",
            "Handoff provenance differs from the input audit",
        )

    source_artifacts = handoff.get("source_node_artifacts")
    expected_source_artifact_keys = {
        "summary",
        "input_audit",
        "process_record",
        "output_audit",
        "human_actions",
        "report",
    }
    safe_source_refs = (
        isinstance(source_artifacts, dict)
        and set(source_artifacts) == expected_source_artifact_keys
        and all(
        isinstance(relative_path, str)
        and "/" not in relative_path
        and (node_dir / relative_path).is_file()
        for relative_path in source_artifacts.values()
        )
    )
    verifier.check(
        "handoff-artifact-references",
        safe_source_refs,
        "Every handoff source-artifact reference resolves inside the current node",
        "A handoff source-artifact reference is missing or unsafe",
    )
    manifest_artifacts = manifest.get("artifacts")
    manifest_artifacts = manifest_artifacts if isinstance(manifest_artifacts, dict) else {}
    verifier.check(
        "manifest-artifact-references",
        manifest_artifacts.get("workflow") == "workflow.json"
        and manifest_artifacts.get("node_root") == f"nodes/{CURRENT_STAGE_ID}"
        and manifest_artifacts.get("handoff")
        == f"nodes/{CURRENT_STAGE_ID}/handoff.json"
        and manifest_artifacts.get("source_inputs") == "inputs"
        and manifest_artifacts.get("artifact_index") == ARTIFACT_INDEX_FILENAME,
        "Manifest artifact references match the run layout",
        "Manifest artifact references are missing or inconsistent",
    )
    verifier.check(
        "pipeline-version",
        process_record.get("pipeline_version") == manifest.get("pipeline_version"),
        "Process and manifest pipeline versions agree",
        "Process and manifest pipeline versions differ",
    )

    return verifier.result(run_dir, run_id)


def _verify_candidate_run(
    run_dir: Path,
    *,
    check_external_inputs: bool,
) -> dict[str, Any]:
    """Verify a stage-2 continuation run and its sealed stage-1 evidence."""
    verifier = _Verification()
    run_id = run_dir.name
    source_stage_id = CURRENT_STAGE_ID
    candidate_node_dir = run_dir / "nodes" / CANDIDATE_STAGE_ID
    source_node_dir = run_dir / "nodes" / source_stage_id
    if not run_dir.is_dir():
        verifier.fail("run-directory", f"Run directory does not exist: {run_dir}")
        return verifier.result(run_dir, run_id)

    fixed_expected_files = {
        "manifest.json",
        "workflow.json",
        *SOURCE_SNAPSHOT_PATHS.values(),
        "inputs/source_run_manifest.json",
        "inputs/source_run_artifact_index.json",
        *(
            f"nodes/{source_stage_id}/{name}"
            for name in NODE_ARTIFACT_NAMES
        ),
        *(
            f"nodes/{CANDIDATE_STAGE_ID}/{name}"
            for name in CANDIDATE_NODE_ARTIFACT_NAMES
        ),
    }
    actual_files: set[str] = set()
    symlinks: list[str] = []
    for path in run_dir.rglob("*"):
        relative_path = path.relative_to(run_dir).as_posix()
        if path.is_symlink():
            symlinks.append(relative_path)
        elif path.is_file() and relative_path != ARTIFACT_INDEX_FILENAME:
            actual_files.add(relative_path)
    verifier.check(
        "no-symlinks",
        not symlinks,
        "Run contains no symlinked artifacts",
        f"Symlinked artifacts are not allowed: {symlinks}",
    )
    verifier.check(
        "required-artifacts",
        fixed_expected_files <= actual_files,
        "All fixed stage-1 and stage-2 artifacts are present",
        f"Missing required artifacts: {sorted(fixed_expected_files - actual_files)}",
    )

    index = _load_json(verifier, run_dir / ARTIFACT_INDEX_FILENAME, "artifact-index-json")
    if isinstance(index, dict):
        entries = index.get("artifacts")
        valid_entries = isinstance(entries, dict) and all(
            isinstance(relative_path, str) and isinstance(identity, dict)
            for relative_path, identity in entries.items()
        )
        verifier.check(
            "artifact-index-shape",
            valid_entries and index.get("hash_algorithm") == "sha256",
            "Artifact index uses SHA-256 and has a path map",
            "Artifact index is malformed or uses an unsupported hash algorithm",
        )
        if valid_entries:
            verifier.check(
                "artifact-index-coverage",
                set(entries) == actual_files,
                f"Artifact index covers all {len(actual_files)} run files",
                (
                    f"Index mismatch: missing={sorted(actual_files - set(entries))}, "
                    f"unexpected={sorted(set(entries) - actual_files)}"
                ),
            )
            for relative_path, identity in sorted(entries.items()):
                artifact_path = _safe_run_path(run_dir, relative_path)
                valid = (
                    artifact_path is not None
                    and artifact_path.is_file()
                    and identity.get("sha256") == sha256_file(artifact_path)
                    and identity.get("size_bytes") == artifact_path.stat().st_size
                )
                verifier.check(
                    f"artifact-integrity:{relative_path}",
                    valid,
                    f"SHA-256 and size match for {relative_path}",
                    f"SHA-256 or size mismatch for {relative_path}",
                )

    document_paths = {
        "manifest": run_dir / "manifest.json",
        "workflow": run_dir / "workflow.json",
        "summary": candidate_node_dir / "summary.json",
        "input_audit": candidate_node_dir / "input_audit.json",
        "process_record": candidate_node_dir / "process_record.json",
        "output_audit": candidate_node_dir / "output_audit.json",
        "human_actions": candidate_node_dir / "human_actions.json",
        "handoff": candidate_node_dir / "handoff.json",
        "candidate_batch": candidate_node_dir / "candidate_batch.json",
        "model_inputs": candidate_node_dir / "model_inputs.json",
        "source_manifest": run_dir / "inputs" / "source_run_manifest.json",
        "source_index": run_dir / "inputs" / "source_run_artifact_index.json",
        "source_summary": source_node_dir / "summary.json",
        "source_proteins": source_node_dir / "proteins.json",
    }
    documents = {
        name: _load_json(verifier, path, f"json:{name}")
        for name, path in document_paths.items()
    }
    if not all(isinstance(document, dict) for document in documents.values()):
        return verifier.result(run_dir, run_id)

    manifest = documents["manifest"]
    workflow = documents["workflow"]
    summary = documents["summary"]
    input_audit = documents["input_audit"]
    process_record = documents["process_record"]
    output_audit = documents["output_audit"]
    human_actions = documents["human_actions"]
    handoff = documents["handoff"]
    candidate_batch = documents["candidate_batch"]
    model_inputs = documents["model_inputs"]
    source_manifest = documents["source_manifest"]
    source_index = documents["source_index"]
    source_summary = documents["source_summary"]
    source_proteins_document = documents["source_proteins"]
    project_id = manifest.get("project_id")

    verifier.check(
        "run-identity",
        isinstance(project_id, str)
        and bool(project_id)
        and manifest.get("run_id") == run_id
        and workflow.get("run_id") == run_id
        and summary.get("run_id") == run_id
        and handoff.get("run_id") == run_id
        and candidate_batch.get("run_id") == run_id
        and candidate_batch.get("project_id") == project_id,
        f"Stage-2 run and project identity agree on {run_id}",
        "Run or project identity differs across stage-2 artifacts",
    )
    if isinstance(index, dict):
        verifier.check(
            "artifact-index-identity",
            index.get("run_id") == run_id and index.get("project_id") == project_id,
            "Artifact index identity matches the manifest",
            "Artifact index identity differs from the manifest",
        )

    try:
        validate_workflow()
        workflow_valid = _workflow_blueprint_matches(workflow)
    except ValueError:
        workflow_valid = False
    verifier.check(
        "workflow-blueprint",
        workflow_valid,
        "Workflow contract matches the validated system DAG",
        "workflow.json differs from the validated system DAG",
    )
    workflow_stages = workflow.get("stages")
    stage_statuses = (
        {stage.get("stage_id"): stage.get("status") for stage in workflow_stages}
        if isinstance(workflow_stages, list)
        and all(isinstance(stage, dict) for stage in workflow_stages)
        else {}
    )
    manifest_nodes = manifest.get("nodes")
    manifest_nodes = manifest_nodes if isinstance(manifest_nodes, dict) else {}
    verifier.check(
        "stage-identity",
        manifest.get("current_stage") == CANDIDATE_STAGE_ID
        and workflow.get("current_stage") == CANDIDATE_STAGE_ID
        and summary.get("stage_id") == CANDIDATE_STAGE_ID
        and input_audit.get("stage_id") == CANDIDATE_STAGE_ID
        and process_record.get("stage_id") == CANDIDATE_STAGE_ID
        and output_audit.get("stage_id") == CANDIDATE_STAGE_ID
        and human_actions.get("stage_id") == CANDIDATE_STAGE_ID
        and handoff.get("from_stage") == CANDIDATE_STAGE_ID
        and candidate_batch.get("stage_id") == CANDIDATE_STAGE_ID
        and model_inputs.get("stage_id") == CANDIDATE_STAGE_ID,
        "All current-node artifacts identify candidate_specification",
        "One or more stage-2 artifacts identify a different stage",
    )
    future_statuses_valid = all(
        status == "not_evaluated"
        for stage_id, status in stage_statuses.items()
        if stage_id not in {source_stage_id, CANDIDATE_STAGE_ID}
    )
    verifier.check(
        "stage-status",
        manifest.get("status") == summary.get("status")
        and isinstance(manifest_nodes.get(CANDIDATE_STAGE_ID), dict)
        and manifest_nodes[CANDIDATE_STAGE_ID].get("status") == summary.get("status")
        and stage_statuses.get(CANDIDATE_STAGE_ID) == summary.get("status")
        and stage_statuses.get(source_stage_id) == source_summary.get("status")
        and future_statuses_valid,
        "Source, current, and future stage statuses are consistent",
        "Stage status differs across manifest, summary, or workflow",
    )

    lineage = manifest.get("lineage")
    lineage = lineage if isinstance(lineage, dict) else {}
    parent_run_id = source_manifest.get("run_id")
    source_entries = source_index.get("artifacts")
    source_entries = source_entries if isinstance(source_entries, dict) else {}
    verifier.check(
        "parent-run-lineage",
        isinstance(parent_run_id, str)
        and parent_run_id
        and lineage.get("parent_run_id") == parent_run_id
        and candidate_batch.get("source_run_id") == parent_run_id
        and input_audit.get("source_run", {}).get("run_id") == parent_run_id
        and lineage.get("parent_artifact_index_sha256")
        == sha256_file(run_dir / "inputs" / "source_run_artifact_index.json")
        and source_manifest.get("project_id") == project_id,
        f"Stage-2 lineage is sealed to source run {parent_run_id}",
        "Source-run identity or artifact-index seal differs",
    )
    copied_parent_paths = {
        *SOURCE_SNAPSHOT_PATHS.values(),
        *(
            f"nodes/{source_stage_id}/{name}"
            for name in NODE_ARTIFACT_NAMES
        ),
    }
    parent_copy_matches = bool(source_entries) and all(
        relative_path in source_entries
        and (run_dir / relative_path).is_file()
        and source_entries[relative_path].get("sha256") == sha256_file(run_dir / relative_path)
        and source_entries[relative_path].get("size_bytes") == (run_dir / relative_path).stat().st_size
        for relative_path in copied_parent_paths
    )
    verifier.check(
        "source-node-seal",
        parent_copy_matches,
        "Copied source snapshots and stage-1 node exactly match the parent artifact index",
        "One or more copied stage-1 artifacts differ from the sealed parent run",
    )

    input_records = input_audit.get("inputs")
    input_records = input_records if isinstance(input_records, dict) else {}
    snapshots_valid = bool(input_records)
    for input_name, record in input_records.items():
        if not isinstance(record, dict):
            snapshots_valid = False
            continue
        snapshot_relative = record.get("snapshot_path")
        snapshot_path = _safe_run_path(candidate_node_dir, snapshot_relative)
        matches = (
            isinstance(snapshot_relative, str)
            and snapshot_relative.startswith("inputs/")
            and snapshot_path is not None
            and snapshot_path.is_file()
            and record.get("sha256") == sha256_file(snapshot_path)
        )
        snapshots_valid = snapshots_valid and matches
        if check_external_inputs:
            external_value = record.get("path")
            external_path = Path(external_value) if isinstance(external_value, str) else None
            try:
                external_matches = (
                    external_path is not None
                    and external_path.is_file()
                    and record.get("sha256") == sha256_file(external_path)
                )
            except OSError:
                external_matches = False
            verifier.check(
                f"external-input-current:{input_name}",
                external_matches,
                f"Current external input still matches {input_name}",
                f"External input is unavailable or has drifted for {input_name}",
                warning=True,
            )
    verifier.check(
        "candidate-input-snapshots",
        snapshots_valid,
        f"All {len(input_records)} candidate inputs have matching snapshots",
        "Candidate input snapshots are missing, unsafe, or inconsistent",
    )
    manifest_inputs = manifest.get("inputs")
    manifest_candidate_inputs = (
        manifest_inputs.get("candidate_specification")
        if isinstance(manifest_inputs, dict)
        else None
    )
    verifier.check(
        "candidate-input-records",
        manifest_candidate_inputs == input_records
        and "candidate_specification" in input_records,
        "Manifest and input audit carry identical candidate input identities",
        "Manifest and input audit candidate inputs differ",
    )

    raw_candidates = candidate_batch.get("candidates")
    candidates = raw_candidates if isinstance(raw_candidates, list) else []
    valid_candidate_shape = bool(candidates) and all(
        isinstance(candidate, dict)
        and isinstance(candidate.get("candidate_key"), str)
        and isinstance(candidate.get("candidate_id"), str)
        and isinstance(candidate.get("amino_acid_sequence"), str)
        and isinstance(candidate.get("inferred_components"), list)
        for candidate in candidates
    )
    candidate_keys = [candidate.get("candidate_key") for candidate in candidates if isinstance(candidate, dict)]
    candidate_ids = [candidate.get("candidate_id") for candidate in candidates if isinstance(candidate, dict)]
    verifier.check(
        "candidate-schema-and-identity",
        valid_candidate_shape
        and len(candidate_keys) == len(set(candidate_keys))
        and len(candidate_ids) == len(set(candidate_ids)),
        "Candidate keys and IDs are present and unique",
        "Candidate records are malformed or contain duplicate identities",
    )

    source_records = source_proteins_document.get("proteins")
    source_records = source_records if isinstance(source_records, list) else []
    source_sequences = {
        record.get("protein_id"): record.get("amino_acid_sequence")
        for record in source_records
        if isinstance(record, dict)
        and isinstance(record.get("protein_id"), str)
        and isinstance(record.get("amino_acid_sequence"), str)
    }
    component_maps_valid = valid_candidate_shape
    for candidate in candidates:
        aa = candidate["amino_acid_sequence"]
        components = candidate["inferred_components"]
        expected_start = 1
        rebuilt: list[str] = []
        for component in components:
            if not isinstance(component, dict):
                component_maps_valid = False
                break
            sequence = component.get("sequence")
            start = component.get("candidate_start")
            end = component.get("candidate_end")
            if (
                not isinstance(sequence, str)
                or start != expected_start
                or end != start + len(sequence) - 1
                or component.get("sequence_sha256")
                != hashlib.sha256(sequence.encode("utf-8")).hexdigest()
            ):
                component_maps_valid = False
                break
            if component.get("component_type") == "source_segment":
                source_id = component.get("source_protein_id")
                source_start = component.get("source_start")
                source_end = component.get("source_end")
                source_sequence = source_sequences.get(source_id)
                if (
                    not isinstance(source_sequence, str)
                    or not isinstance(source_start, int)
                    or not isinstance(source_end, int)
                    or source_sequence[source_start - 1 : source_end] != sequence
                ):
                    component_maps_valid = False
                    break
            rebuilt.append(sequence)
            expected_start = end + 1
        if "".join(rebuilt) != aa:
            component_maps_valid = False
        if candidate.get("amino_acid_sha256") != hashlib.sha256(aa.encode("utf-8")).hexdigest():
            component_maps_valid = False
    verifier.check(
        "component-maps-cover-sequences",
        component_maps_valid,
        "Every component map is contiguous, source-backed, and reconstructs its candidate",
        "A component map does not reconstruct the candidate or source interval",
    )

    output_candidates = output_audit.get("candidates")
    expected_output_candidates = [
        {
            "candidate_key": candidate["candidate_key"],
            "candidate_id": candidate["candidate_id"],
            "computational_status": candidate["computational_status"],
            "release_status": candidate["release_status"],
            "aa_length": len(candidate["amino_acid_sequence"]),
            "translation_relation": candidate["translation_relation"]["relation"],
            "exploratory_structure_ready": candidate["exploratory_structure_ready"],
            "formal_structure_ready": candidate["formal_structure_ready"],
        }
        for candidate in candidates
    ] if valid_candidate_shape else []
    verifier.check(
        "candidate-output-cross-reference",
        output_candidates == expected_output_candidates,
        "Output audit is an exact projection of the candidate batch",
        "Output audit candidates differ from candidate_batch.json",
    )

    expected_structure = {
        candidate["candidate_id"]: candidate["amino_acid_sequence"]
        for candidate in candidates
        if candidate.get("exploratory_structure_ready") is True
        and candidate.get("duplicate_of") is None
    }
    try:
        from .fasta import parse_fasta

        structure_records = parse_fasta(candidate_node_dir / "structure_candidates.fasta")
        observed_structure = {record.record_id: record.sequence for record in structure_records}
    except ValueError:
        observed_structure = {}
    verifier.check(
        "structure-fasta-cross-reference",
        observed_structure == expected_structure,
        f"Structure FASTA contains exactly {len(expected_structure)} eligible unique candidates",
        "Structure FASTA differs from eligible candidates in the batch",
    )
    models = model_inputs.get("models")
    models = models if isinstance(models, dict) else {}
    esmfold = models.get("ESMFold2")
    expected_structure_ids = list(expected_structure)
    verifier.check(
        "model-input-cross-reference",
        isinstance(esmfold, dict)
        and esmfold.get("input_path") == "structure_candidates.fasta"
        and esmfold.get("candidate_ids") == expected_structure_ids
        and models.get("Evo2", {}).get("status") == "deferred"
        and models.get("mRNABERT", {}).get("status") == "deferred",
        "Model handoff matches the candidate batch and stage responsibilities",
        "Model handoff contains inconsistent candidate IDs or stage assignments",
    )

    findings = [
        finding
        for candidate in candidates
        for finding in candidate.get("issues", [])
        if isinstance(finding, dict)
    ]
    error_count = sum(finding.get("severity") == "error" for finding in findings)
    warning_count = sum(finding.get("severity") == "warning" for finding in findings)
    computational_status = "fail" if error_count else "pass"
    output_summary = output_audit.get("summary")
    output_summary = output_summary if isinstance(output_summary, dict) else {}
    expected_counts = {
        "candidate_count": len(candidates),
        "source_control_count": sum(candidate.get("candidate_type") == "source_control" for candidate in candidates),
        "manual_candidate_count": sum(candidate.get("candidate_type") != "source_control" for candidate in candidates),
        "released_count": sum(candidate.get("release_status") == "released" for candidate in candidates),
        "quarantined_count": sum(candidate.get("release_status") == "quarantined" for candidate in candidates),
        "rejected_count": sum(candidate.get("release_status") == "rejected" for candidate in candidates),
        "exploratory_structure_ready_count": len(expected_structure),
        "formal_structure_ready_count": sum(candidate.get("formal_structure_ready") is True for candidate in candidates),
        "errors": error_count,
        "warnings": warning_count,
    }
    verifier.check(
        "candidate-counts-and-status",
        output_summary == expected_counts
        and input_audit.get("status") == computational_status
        and output_audit.get("status") == computational_status
        and summary.get("computational_audit_status") == computational_status
        and manifest.get("counts", {}).get("candidate_count") == len(candidates),
        "Candidate counts and computational status agree across artifacts",
        "Candidate counts or computational status differ across artifacts",
    )

    raw_actions = human_actions.get("actions")
    actions = raw_actions if isinstance(raw_actions, list) else []
    action_ids = [action.get("action_id") for action in actions if isinstance(action, dict)]
    known_stage_ids = {stage.stage_id for stage in FULL_WORKFLOW}
    open_actions = [
        action for action in actions if isinstance(action, dict) and action.get("status") == "open"
    ]
    due_actions = [
        action
        for action in open_actions
        if action.get("required_before_stage") in {CANDIDATE_STAGE_ID, handoff.get("to_stage")}
    ]
    actions_valid = (
        isinstance(raw_actions, list)
        and len(action_ids) == len(set(action_ids))
        and all(
            isinstance(action, dict)
            and action.get("required_before_stage") in known_stage_ids
            and action.get("status") in {"open", "resolved", "waived"}
            for action in actions
        )
    )
    verifier.check(
        "human-actions",
        actions_valid
        and human_actions.get("open_count") == len(open_actions)
        and human_actions.get("due_before_next_stage_count") == len(due_actions)
        and summary.get("open_human_actions") == len(open_actions)
        and summary.get("due_human_actions") == len(due_actions)
        and handoff.get("blocking_action_ids")
        == [action.get("action_id") for action in due_actions]
        and handoff.get("carried_human_actions") == open_actions,
        "Human actions, counts, and handoff gates agree",
        "Human actions or handoff gates are inconsistent",
    )
    if computational_status == "fail":
        expected_status, expected_readiness = "blocked", "blocked"
    elif due_actions:
        expected_status, expected_readiness = "needs_human_input", "needs_human_input"
    else:
        expected_status, expected_readiness = "complete", "ready"
    verifier.check(
        "handoff-readiness",
        summary.get("status") == expected_status
        and summary.get("handoff_readiness") == expected_readiness
        and handoff.get("readiness") == expected_readiness
        and handoff.get("to_stage") == "protein_structure_assessment",
        f"Node status and handoff correctly resolve to {expected_readiness}",
        "Node status or handoff readiness conflicts with findings and actions",
    )
    candidate_batch_sha256 = sha256_file(candidate_node_dir / "candidate_batch.json")
    carried_forward = handoff.get("carried_forward")
    carried_forward = carried_forward if isinstance(carried_forward, dict) else {}
    verifier.check(
        "handoff-candidate-batch",
        carried_forward.get("candidate_batch_sha256") == candidate_batch_sha256
        and carried_forward.get("source_run_id") == parent_run_id
        and carried_forward.get("project_id") == project_id
        and carried_forward.get("candidates") == expected_output_candidates
        and carried_forward.get("exploratory_structure_candidate_ids") == expected_structure_ids,
        "Handoff carries the exact candidate batch and exploratory structure set",
        "Handoff candidate batch identity or candidates differ",
    )

    manifest_artifacts = manifest.get("artifacts")
    manifest_artifacts = manifest_artifacts if isinstance(manifest_artifacts, dict) else {}
    verifier.check(
        "manifest-artifact-references",
        manifest_artifacts.get("workflow") == "workflow.json"
        and manifest_artifacts.get("current_node_root") == f"nodes/{CANDIDATE_STAGE_ID}"
        and manifest_artifacts.get("handoff") == f"nodes/{CANDIDATE_STAGE_ID}/handoff.json"
        and manifest_artifacts.get("candidate_batch")
        == f"nodes/{CANDIDATE_STAGE_ID}/candidate_batch.json"
        and manifest_artifacts.get("structure_candidates")
        == f"nodes/{CANDIDATE_STAGE_ID}/structure_candidates.fasta"
        and manifest_artifacts.get("artifact_index") == ARTIFACT_INDEX_FILENAME,
        "Manifest artifact references match the continuation-run layout",
        "Manifest artifact references are missing or inconsistent",
    )
    verifier.check(
        "pipeline-version",
        process_record.get("pipeline_version") == manifest.get("pipeline_version"),
        "Process and manifest pipeline versions agree",
        "Process and manifest pipeline versions differ",
    )
    return verifier.result(run_dir, run_id)
