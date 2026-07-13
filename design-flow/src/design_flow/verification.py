"""Integrity and cross-artifact verification for immutable design-flow runs."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from .workflow import CURRENT_STAGE_ID, FULL_WORKFLOW, validate_workflow


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
    if not isinstance(stages, list) or len(stages) != len(FULL_WORKFLOW):
        return False
    for actual, expected in zip(stages, FULL_WORKFLOW, strict=True):
        if not isinstance(actual, dict):
            return False
        expected_fields = {
            "order": expected.order,
            "stage_id": expected.stage_id,
            "name": expected.name,
            "purpose": expected.purpose,
            "capabilities": list(expected.capabilities),
            "input_audit_contract": list(expected.input_audit),
            "process_contract": list(expected.process),
            "output_audit_contract": list(expected.output_audit),
            "human_intervention_contract": list(expected.human_intervention),
            "depends_on": list(expected.depends_on),
        }
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
