"""Immutable continuation-run artifacts for Stage 3 structure assessment."""

from __future__ import annotations

import copy
import csv
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from . import __version__
from .design_loop import redesign_request_document, request_id
from .structure_assessment import STRUCTURE_STAGE_ID, StructureAssessmentAnalysis
from .structure_html_report import render_structure_report
from .structure_metrics import RULESET_ID
from .verification import ARTIFACT_INDEX_FILENAME, build_artifact_index, sha256_file, verify_run
from .workflow import (
    STAGE_BY_ID,
    action_due_for_handoff,
    workflow_contract,
    workflow_contract_sha256,
)


CANDIDATE_STAGE_ID = "candidate_specification"
NEXT_STAGES = ("immune_evidence_assessment", "developability_assessment")


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def _csv_text(fieldnames: list[str], rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _assessment_rows(assessments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": item["candidate_id"],
            "candidate_key": item["candidate_key"],
            "candidate_type": item["candidate_type"],
            "length": item["length"],
            "confidence_band": item["confidence_band"],
            "mean_plddt": item["mean_plddt"],
            "minimum_plddt": item["minimum_plddt"],
            "ptm": item["ptm"],
            "fraction_plddt_below_70": item["fraction_plddt_below_70"],
            "low_confidence_segment_count": len(item["low_confidence_segments"]),
            "review_flag_count": len(item["review_flags"]),
            "radius_of_gyration_angstrom": item["geometry"]["radius_of_gyration_angstrom"],
            "end_to_end_distance_angstrom": item["geometry"]["end_to_end_distance_angstrom"],
            "shape_anisotropy": item["geometry"]["shape_anisotropy"],
            "runtime_seconds": item["runtime_seconds"],
            "pdb_sha256": item["pdb_sha256"],
        }
        for item in assessments
    ]


def _component_rows(assessments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for assessment in assessments:
        for component in assessment["components"]:
            rows.append(
                {
                    "candidate_id": assessment["candidate_id"],
                    "candidate_key": assessment["candidate_key"],
                    **component,
                    "geometry": json.dumps(component["geometry"], sort_keys=True),
                }
            )
    return rows


def _boundary_rows(assessments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for assessment in assessments:
        for boundary in assessment["boundaries"]:
            rows.append(
                {
                    "candidate_id": assessment["candidate_id"],
                    "candidate_key": assessment["candidate_key"],
                    **boundary,
                }
            )
    return rows


def _comparison_rows(assessments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for assessment in assessments:
        for comparison in assessment["source_geometry_comparisons"]:
            rows.append(
                {
                    "candidate_id": assessment["candidate_id"],
                    "candidate_key": assessment["candidate_key"],
                    **comparison,
                }
            )
    return rows


def _human_actions(analysis: StructureAssessmentAnalysis) -> list[dict[str, Any]]:
    parent_handoff = json.loads(
        (
            analysis.source_run_dir
            / "nodes"
            / CANDIDATE_STAGE_ID
            / "handoff.json"
        ).read_text(encoding="utf-8")
    )
    actions = [dict(action) for action in parent_handoff.get("carried_human_actions", [])]
    known = {action.get("action_id") for action in actions}
    generated = [
        {
            "action_id": "confirm-structure-model-context",
            "question": "Confirm monomer, oligomer, membrane, glycosylation, and construct-context assumptions before formal structural release.",
            "question_zh": "在正式结构放行前确认单体、寡聚体、膜环境、糖基化和构建上下文假设。",
            "required_before_stage": "integrated_ranking",
            "status": "open",
            "owner": "unassigned",
            "resolution": "",
            "resolution_zh": "",
        }
    ]
    if analysis.findings:
        generated.append(
            {
                "action_id": "review-exploratory-structure-flags",
                "question": "Review candidates and residue ranges flagged by the deterministic exploratory structure rules.",
                "question_zh": "复核确定性探索结构规则标记的候选和残基区段。",
                "required_before_stage": "integrated_ranking",
                "status": "open",
                "owner": "unassigned",
                "resolution": "",
                "resolution_zh": "",
            }
        )
    if any(item["confidence_band"] == "low_confidence" for item in analysis.assessments):
        generated.append(
            {
                "action_id": "select-structure-cross-checks",
                "question": "Select low-confidence candidates for repeat seeds, an alternative predictor, or a revised construct hypothesis.",
                "question_zh": "为低置信候选选择重复 seed、替代结构预测器或修订后的构建假设。",
                "required_before_stage": "integrated_ranking",
                "status": "open",
                "owner": "unassigned",
                "resolution": "",
                "resolution_zh": "",
            }
        )
    actions.extend(action for action in generated if action["action_id"] not in known)
    return actions


def build_structure_node_bundle(
    analysis: StructureAssessmentAnalysis,
    run_id: str,
) -> dict[str, Any]:
    assessments = copy.deepcopy(analysis.assessments)
    round_id = str(analysis.source_candidate_batch["design_round_id"])
    for item in assessments:
        candidate_id = item["candidate_id"]
        item["structure_artifact"] = {
            "path": f"structures/{candidate_id}.pdb",
            "sha256": item["pdb_sha256"],
            "bytes": item["pdb_bytes"],
        }
        item["raw_result_path"] = f"model_results/records/{candidate_id}/result.json"
    actions = _human_actions(analysis)
    open_actions = [action for action in actions if action["status"] == "open"]
    due_actions = [
        action
        for action in open_actions
        if action_due_for_handoff(
            action["required_before_stage"],
            current_stage=STRUCTURE_STAGE_ID,
            to_stages=NEXT_STAGES,
        )
    ]
    band_counts = {
        band: sum(item["confidence_band"] == band for item in assessments)
        for band in ("higher_confidence", "mixed_confidence", "low_confidence")
    }
    summary = {
        "schema_version": 1,
        "run_id": run_id,
        "stage_id": STRUCTURE_STAGE_ID,
        "stage_name": STAGE_BY_ID[STRUCTURE_STAGE_ID].name,
        "status": "needs_human_input" if due_actions else "complete",
        "computational_audit_status": "pass",
        "mode": "exploratory",
        "ruleset_id": RULESET_ID,
        "candidate_count": len(assessments),
        "higher_confidence_count": band_counts["higher_confidence"],
        "mixed_confidence_count": band_counts["mixed_confidence"],
        "low_confidence_count": band_counts["low_confidence"],
        "review_flag_count": sum(len(item["review_flags"]) for item in assessments),
        "open_human_actions": len(open_actions),
        "due_human_actions": len(due_actions),
        "next_stages": list(NEXT_STAGES),
    }
    redesign_requests = redesign_request_document(
        project_id=analysis.config.project_id,
        run_id=run_id,
        round_id=round_id,
        stage_id=STRUCTURE_STAGE_ID,
        requests=[
            {
                "request_id": request_id(
                    STRUCTURE_STAGE_ID,
                    finding["candidate_id"],
                    finding["code"],
                    index,
                ),
                "status": "proposed",
                "candidate_id": finding["candidate_id"],
                "trigger": finding["code"],
                "evidence_ref": f"findings.csv#row={index + 1}",
                "requested_variable_ids": ["antigen.recipe"],
                "instruction": (
                    "Review the flagged structural region. If a sequence change is authorized, "
                    "create a child proposal in the next immutable round; do not mutate this candidate."
                ),
                "authority": "deterministic_structure_rule",
            }
            for index, finding in enumerate(analysis.findings)
        ],
    )
    input_audit = {
        "stage_id": STRUCTURE_STAGE_ID,
        "status": "pass",
        "mode": "exploratory",
        "source_run": {
            "run_id": analysis.source_manifest["run_id"],
            "path": str(analysis.source_run_dir),
            "artifact_index_sha256": sha256_file(
                analysis.source_run_dir / ARTIFACT_INDEX_FILENAME
            ),
        },
        "job": {
            "job_identity": analysis.job_manifest["job_identity"],
            "manifest_sha256": sha256_file(analysis.job_dir / "job-manifest.json"),
            "fasta_sha256": sha256_file(analysis.job_dir / "sequences.fasta"),
        },
        "result_archive": {
            "source_path": str(analysis.result_archive),
            "sha256": analysis.result_archive_sha256,
            "bytes": analysis.result_archive.stat().st_size,
        },
        "gpu_run": {
            "run_identity": analysis.result_run_manifest["run_identity"],
            "runtime_identity": analysis.result_run_manifest["runtime_identity"],
            "model": analysis.result_run_manifest["model"],
            "execution": analysis.result_run_manifest["execution"],
        },
        "checks": [
            {"check_id": "stage2-run-verified", "status": "pass"},
            {"check_id": "job-identity-verified", "status": "pass"},
            {"check_id": "result-archive-bounded", "status": "pass"},
            {"check_id": "gpu-run-identity-verified", "status": "pass"},
            {"check_id": "all-pdb-checksums-verified", "status": "pass"},
            {"check_id": "all-pdb-sequences-match", "status": "pass"},
        ],
    }
    process_record = {
        "stage_id": STRUCTURE_STAGE_ID,
        "pipeline_version": __version__,
        "mode": "exploratory",
        "ruleset_id": RULESET_ID,
        "operations": [
            {
                "operation": "validate_remote_result_contract",
                "behavior": "Verify job, runtime, model, candidate, result, and PDB identities before interpretation.",
            },
            {
                "operation": "parse_common_residue_map",
                "behavior": "Require one C-alpha per candidate residue and exact PDB-to-candidate sequence equality.",
            },
            {
                "operation": "compute_confidence_and_geometry",
                "behavior": "Compute normalized residue confidence, low-confidence spans, principal axes, extents, radius of gyration, and clash indicators.",
            },
            {
                "operation": "assess_components_and_boundaries",
                "behavior": "Project Stage 2 component ranges onto each structure and compute component/boundary confidence without changing ranges.",
            },
            {
                "operation": "compare_source_geometry",
                "behavior": "Compare exact source-derived segments using alignment-free C-alpha distance-matrix RMSD.",
            },
            {
                "operation": "emit_review_flags",
                "behavior": "Apply versioned review-only thresholds; no efficacy, safety, or wet-lab conclusion is inferred.",
            },
        ],
        "thresholds": {
            "low_residue_plddt": 70.0,
            "very_low_residue_plddt": 50.0,
            "low_candidate_ptm": 0.5,
            "extended_low_confidence_length": 10,
            "source_geometry_review_drmsd_angstrom": 3.0,
            "nonlocal_ca_clash_angstrom": 3.0,
        },
    }
    output_audit = {
        "stage_id": STRUCTURE_STAGE_ID,
        "status": "pass",
        "mode": "exploratory",
        "ruleset_id": RULESET_ID,
        "summary": summary,
        "candidates": [
            {
                "candidate_id": item["candidate_id"],
                "candidate_key": item["candidate_key"],
                "status": item["status"],
                "confidence_band": item["confidence_band"],
                "mean_plddt": item["mean_plddt"],
                "ptm": item["ptm"],
                "review_flag_count": len(item["review_flags"]),
                "pdb_sha256": item["pdb_sha256"],
            }
            for item in assessments
        ],
        "findings": analysis.findings,
        "checks": [
            {"check_id": "all-candidates-assessed", "status": "pass"},
            {"check_id": "all-structures-checksum-bound", "status": "pass"},
            {"check_id": "all-components-use-stage2-ranges", "status": "pass"},
            {"check_id": "review-flags-are-not-release-gates", "status": "pass"},
        ],
    }
    human_actions = {
        "stage_id": STRUCTURE_STAGE_ID,
        "open_count": len(open_actions),
        "actions": actions,
    }
    handoff = {
        "schema_version": 1,
        "run_id": run_id,
        "from_stage": STRUCTURE_STAGE_ID,
        "to_stages": list(NEXT_STAGES),
        "readiness": "exploratory_ready",
        "formal_readiness": "needs_human_input" if due_actions else "ready",
        "blocking_action_ids": [action["action_id"] for action in due_actions],
        "carried_human_actions": open_actions,
        "carried_forward": {
            "project_id": analysis.config.project_id,
            "candidate_batch_sha256": analysis.job_manifest["source"][
                "candidate_batch_sha256"
            ],
            "structure_assessments_sha256": None,
            "candidate_ids": [item["candidate_id"] for item in assessments],
            "structure_artifacts": {
                item["candidate_id"]: item["structure_artifact"] for item in assessments
            },
        },
        "limitations": analysis.result_run_manifest["limitations"],
    }
    return {
        "status": summary["status"],
        "summary": summary,
        "input_audit": input_audit,
        "process_record": process_record,
        "output_audit": output_audit,
        "human_actions": human_actions,
        "handoff": handoff,
        "structure_assessments": {
            "schema_version": 1,
            "project_id": analysis.config.project_id,
            "run_id": run_id,
            "stage_id": STRUCTURE_STAGE_ID,
            "mode": "exploratory",
            "ruleset_id": RULESET_ID,
            "job_identity": analysis.job_manifest["job_identity"],
            "gpu_run_identity": analysis.result_run_manifest["run_identity"],
            "assessments": assessments,
        },
        "redesign_requests": redesign_requests,
    }


def _snapshot_stage3_inputs(
    analysis: StructureAssessmentAnalysis,
    node_dir: Path,
) -> None:
    inputs = node_dir / "inputs"
    inputs.mkdir(parents=True)
    shutil.copyfile(analysis.job_dir / "job-manifest.json", inputs / "job-manifest.json")
    shutil.copyfile(analysis.job_dir / "sequences.fasta", inputs / "sequences.fasta")
    shutil.copyfile(analysis.result_archive, inputs / "result-archive.tar.gz")
    model_results = node_dir / "model_results"
    shutil.copytree(analysis.result_dir, model_results)
    structures = node_dir / "structures"
    structures.mkdir()
    for candidate_id, path in analysis.pdb_paths.items():
        shutil.copyfile(path, structures / f"{candidate_id}.pdb")


def write_structure_run(
    analysis: StructureAssessmentAnalysis,
    *,
    now: datetime | None = None,
) -> Path:
    created = now or datetime.now(timezone.utc)
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    created = created.astimezone(timezone.utc)
    created_at = created.isoformat()
    run_id = (
        f"{created.strftime('%Y%m%dT%H%M%S%fZ')}-stage3-"
        f"{analysis.result_run_manifest['run_identity'][:8]}"
    )
    run_dir = analysis.config.run_root / run_id
    node_dir = run_dir / "nodes" / STRUCTURE_STAGE_ID
    if run_dir.exists():
        raise ValueError(f"Refusing to overwrite structure run: {run_dir}")
    try:
        shutil.copytree(analysis.source_run_dir / "nodes", run_dir / "nodes")
        shutil.copytree(analysis.source_run_dir / "inputs", run_dir / "inputs")
        shutil.copyfile(
            analysis.source_run_dir / "manifest.json",
            run_dir / "inputs" / "parent_run_manifest.json",
        )
        shutil.copyfile(
            analysis.source_run_dir / ARTIFACT_INDEX_FILENAME,
            run_dir / "inputs" / "parent_run_artifact_index.json",
        )
        node_dir.mkdir(parents=True)
        _snapshot_stage3_inputs(analysis, node_dir)
        bundle = build_structure_node_bundle(analysis, run_id)
        bundle["summary"]["created_at_utc"] = created_at
        _atomic_write(
            node_dir / "structure_assessments.json",
            _json_text(bundle["structure_assessments"]),
        )
        _atomic_write(
            node_dir / "redesign_requests.json",
            _json_text(bundle["redesign_requests"]),
        )
        assessments_sha = sha256_file(node_dir / "structure_assessments.json")
        bundle["handoff"]["carried_forward"][
            "structure_assessments_sha256"
        ] = assessments_sha
        for name in (
            "summary",
            "input_audit",
            "process_record",
            "output_audit",
            "human_actions",
            "handoff",
        ):
            _atomic_write(node_dir / f"{name}.json", _json_text(bundle[name]))
        _atomic_write(
            node_dir / "structures.csv",
            _csv_text(
                [
                    "candidate_id", "candidate_key", "candidate_type", "length",
                    "confidence_band", "mean_plddt", "minimum_plddt", "ptm",
                    "fraction_plddt_below_70", "low_confidence_segment_count",
                    "review_flag_count", "radius_of_gyration_angstrom",
                    "end_to_end_distance_angstrom", "shape_anisotropy",
                    "runtime_seconds", "pdb_sha256",
                ],
                _assessment_rows(analysis.assessments),
            ),
        )
        _atomic_write(
            node_dir / "components.csv",
            _csv_text(
                [
                    "candidate_id", "candidate_key", "component_index",
                    "component_type", "candidate_start", "candidate_end",
                    "source_protein_id", "source_start", "source_end",
                    "sequence_sha256", "mean_plddt", "minimum_plddt",
                    "low_confidence_fraction", "geometry",
                ],
                _component_rows(analysis.assessments),
            ),
        )
        _atomic_write(
            node_dir / "boundaries.csv",
            _csv_text(
                [
                    "candidate_id", "candidate_key", "left_component_index",
                    "right_component_index", "boundary_after_residue", "window_start",
                    "window_end", "window_mean_plddt", "window_minimum_plddt",
                    "junction_ca_distance_angstrom",
                ],
                _boundary_rows(analysis.assessments),
            ),
        )
        _atomic_write(
            node_dir / "source_comparisons.csv",
            _csv_text(
                [
                    "candidate_id", "candidate_key", "component_index",
                    "source_protein_id", "source_candidate_id", "source_start",
                    "source_end", "residue_count", "distance_matrix_rmsd_angstrom",
                    "mean_plddt_delta",
                ],
                _comparison_rows(analysis.assessments),
            ),
        )
        _atomic_write(
            node_dir / "findings.csv",
            _csv_text(
                [
                    "severity", "code", "candidate_id", "candidate_key",
                    "component_index", "message",
                ],
                analysis.findings,
            ),
        )
        _atomic_write(
            node_dir / "report.html",
            render_structure_report(analysis, bundle, run_id, created_at),
        )

        workflow = workflow_contract()
        workflow["contract_sha256"] = workflow_contract_sha256()
        workflow["run_id"] = run_id
        workflow["current_stage"] = STRUCTURE_STAGE_ID
        parent_workflow = json.loads(
            (analysis.source_run_dir / "workflow.json").read_text(encoding="utf-8")
        )
        parent_status = {
            stage["stage_id"]: stage["status"] for stage in parent_workflow["stages"]
        }
        for stage in workflow["stages"]:
            if stage["stage_id"] == STRUCTURE_STAGE_ID:
                stage["status"] = bundle["status"]
            else:
                stage["status"] = parent_status.get(stage["stage_id"], "not_evaluated")
        _atomic_write(run_dir / "workflow.json", _json_text(workflow))

        parent_index_sha = sha256_file(
            analysis.source_run_dir / ARTIFACT_INDEX_FILENAME
        )
        parent_nodes = copy.deepcopy(analysis.source_manifest["nodes"])
        parent_nodes[STRUCTURE_STAGE_ID] = {
            "status": bundle["status"],
            "summary": f"nodes/{STRUCTURE_STAGE_ID}/summary.json",
            "report": f"nodes/{STRUCTURE_STAGE_ID}/report.html",
        }
        manifest = {
            "schema_version": 1,
            "pipeline_version": __version__,
            "project_id": analysis.config.project_id,
            "run_id": run_id,
            "created_at_utc": created_at,
            "status": bundle["status"],
            "runtime_root": str(analysis.config.runtime_root),
            "current_stage": STRUCTURE_STAGE_ID,
            "lineage": {
                "parent_run_id": analysis.source_manifest["run_id"],
                "parent_run_path": str(analysis.source_run_dir),
                "parent_artifact_index_sha256": parent_index_sha,
            },
            "context": analysis.source_manifest["context"],
            "counts": {
                **analysis.source_manifest["counts"],
                "structure_candidates": len(analysis.assessments),
                "structure_review_flags": len(analysis.findings),
            },
            "inputs": {
                "parent_run_id": analysis.source_manifest["run_id"],
                "job_identity": analysis.job_manifest["job_identity"],
                "result_archive_sha256": analysis.result_archive_sha256,
                "gpu_run_identity": analysis.result_run_manifest["run_identity"],
            },
            "nodes": parent_nodes,
            "artifacts": {
                "workflow": "workflow.json",
                "current_node_root": f"nodes/{STRUCTURE_STAGE_ID}",
                "handoff": f"nodes/{STRUCTURE_STAGE_ID}/handoff.json",
                "structure_assessments": (
                    f"nodes/{STRUCTURE_STAGE_ID}/structure_assessments.json"
                ),
                "result_archive": f"nodes/{STRUCTURE_STAGE_ID}/inputs/result-archive.tar.gz",
                "artifact_index": ARTIFACT_INDEX_FILENAME,
            },
        }
        _atomic_write(run_dir / "manifest.json", _json_text(manifest))
        artifact_index = build_artifact_index(
            run_dir,
            project_id=analysis.config.project_id,
            run_id=run_id,
        )
        _atomic_write(run_dir / ARTIFACT_INDEX_FILENAME, _json_text(artifact_index))
        verification = verify_run(run_dir)
        if verification["status"] != "pass":
            raise ValueError(
                "Structure run verification failed; latest was not updated: "
                + "; ".join(verification["errors"][:5])
            )
        _atomic_write(
            analysis.config.run_root / "latest.json",
            _json_text(
                {
                    "schema_version": 1,
                    "project_id": analysis.config.project_id,
                    "run_id": run_id,
                    "run_path": str(run_dir),
                    "current_stage": STRUCTURE_STAGE_ID,
                    "status": bundle["status"],
                    "summary_path": str(node_dir / "summary.json"),
                    "report_path": str(node_dir / "report.html"),
                    "structure_assessments_path": str(
                        node_dir / "structure_assessments.json"
                    ),
                    "artifact_index_path": str(run_dir / ARTIFACT_INDEX_FILENAME),
                    "artifact_index_sha256": sha256_file(
                        run_dir / ARTIFACT_INDEX_FILENAME
                    ),
                    "verification_status": verification["status"],
                }
            ),
        )
        return run_dir
    except Exception:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise
