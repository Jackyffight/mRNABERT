"""Semantic verifier for Stage 7 integrated ranking."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .ranking import _compute_ranking_result
from .ranking_reporting import _csv_text
from .ranking_specs import RANKING_STAGE_ID


REQUIRED_FILES = {
    "summary.json", "report.html", "input_audit.json", "process_record.json",
    "output_audit.json", "human_actions.json", "handoff.json", "ranking_result.json",
    "rankings.csv", "provisional_portfolios.csv", "sensitivity.csv",
    "inputs/ranking_specification.json",
}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def verify_ranking_run(
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
    node = root / "nodes" / RANKING_STAGE_ID
    symlinks = [path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_symlink()]
    actual = {
        path.relative_to(node).as_posix() for path in node.rglob("*") if path.is_file()
    } if node.is_dir() else set()
    verifier.check(
        "stage7-no-symlinks", not symlinks, "No symlinked artifacts", f"Symlinks: {symlinks}"
    )
    verifier.check(
        "stage7-required-artifacts", REQUIRED_FILES <= actual,
        "All required Stage 7 artifacts are present",
        f"Missing artifacts: {sorted(REQUIRED_FILES - actual)}",
    )
    try:
        manifest = _load(root / "manifest.json")
        workflow = _load(root / "workflow.json")
        index = _load(root / ARTIFACT_INDEX_FILENAME)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        verifier.fail("stage7-root-json", str(error))
        return verifier.result(root, run_id)
    verifier.check(
        "stage7-root-identity",
        manifest.get("run_id") == run_id
        and manifest.get("current_stage") == RANKING_STAGE_ID
        and manifest.get("executed_stages") == [RANKING_STAGE_ID]
        and index.get("run_id") == run_id,
        "Manifest identifies the Stage 7 run", "Manifest or index identity mismatch",
    )
    try:
        integrity = build_artifact_index(root, manifest["project_id"], run_id) == index
    except (OSError, ValueError, KeyError):
        integrity = False
    verifier.check(
        "stage7-artifact-integrity", integrity,
        "Every artifact matches the SHA256 index", "Artifact index differs from current files",
    )
    verifier.check(
        "stage7-workflow-contract",
        _workflow_blueprint_matches(workflow)
        and workflow.get("run_id") == run_id
        and workflow.get("current_stage") == RANKING_STAGE_ID,
        "Workflow matches the frozen contract", "Workflow contract or current stage mismatch",
    )
    lineage = manifest.get("lineage", {})
    parent_path = Path(str(lineage.get("parent_run_path", ""))).expanduser().resolve()
    try:
        parent_index_snapshot = root / "inputs/lineage/stage6_parent_artifact_index.json"
        parent_manifest_snapshot = root / "inputs/lineage/stage6_parent_manifest.json"
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
        "stage7-parent-seal", parent_seal,
        "Stage 6 parent snapshots are sealed", "Parent seal mismatch",
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
        "stage7-parent-artifacts-copied", copied_parent,
        "Copied Stage 1-6 artifacts match the parent index",
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
            "stage7-external-parent", external_ok,
            "External Stage 6 parent remains valid",
            f"External parent missing, invalid, or changed: {parent_path}",
        )
    try:
        candidate_batch = _load(root / "nodes/candidate_specification/candidate_batch.json")
        spec_path = node / "inputs/ranking_specification.json"
        spec = _load(spec_path)
        recomputed = _compute_ranking_result(root, spec, spec_path, candidate_batch)
        stored = _load(node / "ranking_result.json")
        semantic_loaded = True
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        recomputed = stored = {}
        semantic_loaded = False
    verifier.check(
        "stage7-semantic-recompute", semantic_loaded,
        "Ranking recomputed from copied Stage 3-6 evidence and frozen policy",
        "Stage 7 deterministic recomputation failed",
    )
    verifier.check(
        "stage7-ranking-reproducibility",
        semantic_loaded and stored == recomputed,
        "Stored ranking exactly matches deterministic recomputation",
        "Stored ranking differs from deterministic recomputation",
    )
    ranking_csv_ok = False
    if semantic_loaded:
        expected_csv = _csv_text(
            [
                "modality", "rank", "candidate_id", "candidate_key", "candidate_type",
                "eligible", "score", "evidence_coverage", "exclusion_reasons", "components",
            ],
            [
                {
                    **row,
                    "exclusion_reasons": json.dumps(row["exclusion_reasons"], sort_keys=True),
                    "components": json.dumps(row["components"], sort_keys=True),
                }
                for row in recomputed["rankings"]
            ],
        )
        try:
            ranking_csv_ok = (
                (node / "rankings.csv").read_bytes().decode("utf-8") == expected_csv
            )
        except OSError:
            ranking_csv_ok = False
    verifier.check(
        "stage7-ranking-csv-reproducibility", ranking_csv_ok,
        "Ranking CSV matches the recomputed component records",
        "Ranking CSV differs from deterministic recomputation",
    )
    try:
        handoff = _load(node / "handoff.json")
        handoff_ok = (
            handoff["carried_forward"]["ranking_result_sha256"]
            == sha256_file(node / "ranking_result.json")
            and handoff.get("formal_readiness") == "not_released"
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        handoff_ok = False
    verifier.check(
        "stage7-handoff-seal", handoff_ok,
        "Stage 7 handoff seals the ranking and remains unreleased",
        "Stage 7 handoff hash or formal readiness differs",
    )
    return verifier.result(root, run_id)
