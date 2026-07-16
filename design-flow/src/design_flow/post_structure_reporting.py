"""Write one immutable run containing the parallel Stage 4 and Stage 5 nodes."""

from __future__ import annotations

import copy
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from . import __version__
from .assessment_specs import DEVELOPABILITY_STAGE_ID, IMMUNE_STAGE_ID
from .continuation_state import (
    merge_requirement_actions,
    project_context,
    reconcile_human_actions,
)
from .design_loop import redesign_request_document, request_id
from .post_structure_assessment import PostStructureAnalysis
from .post_structure_html import render_developability_report, render_immune_report
from .requirement_gates import requirement_class_counts
from .verification import ARTIFACT_INDEX_FILENAME, build_artifact_index, sha256_file, verify_run
from .workflow import (
    STAGE_BY_ID,
    action_due_for_handoff,
    workflow_contract,
    workflow_contract_sha256,
)


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


def _parent_actions(analysis: PostStructureAnalysis) -> list[dict[str, Any]]:
    handoff = json.loads(
        (
            analysis.source_run_dir
            / "nodes/protein_structure_assessment/handoff.json"
        ).read_text(encoding="utf-8")
    )
    return reconcile_human_actions(
        [dict(action) for action in handoff.get("carried_human_actions", [])],
        analysis.config,
    )


def _design_round_id(analysis: PostStructureAnalysis) -> str:
    candidate_batch = json.loads(
        (
            analysis.source_run_dir
            / "nodes/candidate_specification/candidate_batch.json"
        ).read_text(encoding="utf-8")
    )
    round_id = candidate_batch.get("design_round_id")
    if not isinstance(round_id, str) or not round_id:
        raise ValueError("Candidate batch has no design_round_id")
    return round_id


def _blocking_actions_by_stage(
    open_actions: list[dict[str, Any]],
    *,
    current_stage: str,
    target_stages: tuple[str, ...],
) -> dict[str, list[str]]:
    return {
        target_stage: [
            action["action_id"]
            for action in open_actions
            if action_due_for_handoff(
                action["required_before_stage"],
                current_stage=current_stage,
                to_stages=(target_stage,),
            )
        ]
        for target_stage in target_stages
    }


def _due_actions(
    open_actions: list[dict[str, Any]],
    blocking_by_stage: dict[str, list[str]],
) -> list[dict[str, Any]]:
    blocking_ids = {
        action_id
        for action_ids in blocking_by_stage.values()
        for action_id in action_ids
    }
    return [action for action in open_actions if action["action_id"] in blocking_ids]


def _execution_blocking_actions(
    due_actions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        action
        for action in due_actions
        if action.get("requirement_class") == "blocking_now"
    ]


def _execution_blocking_by_stage(
    open_actions: list[dict[str, Any]],
    blocking_by_stage: dict[str, list[str]],
) -> dict[str, list[str]]:
    action_by_id = {action["action_id"]: action for action in open_actions}
    return {
        stage_id: [
            action_id
            for action_id in action_ids
            if action_by_id[action_id].get("requirement_class") == "blocking_now"
        ]
        for stage_id, action_ids in blocking_by_stage.items()
    }


def _immune_bundle(
    analysis: PostStructureAnalysis,
    run_id: str,
) -> dict[str, Any]:
    result = analysis.immune_result
    redesign_requests = redesign_request_document(
        project_id=analysis.config.project_id,
        run_id=run_id,
        round_id=_design_round_id(analysis),
        stage_id=IMMUNE_STAGE_ID,
        requests=[],
    )
    actions = merge_requirement_actions(
        _parent_actions(analysis),
        result["requirements"],
    )
    open_actions = [action for action in actions if action["status"] == "open"]
    target_stages = ("integrated_ranking",)
    blocking_by_stage = _blocking_actions_by_stage(
        open_actions,
        current_stage=IMMUNE_STAGE_ID,
        target_stages=target_stages,
    )
    due_actions = _due_actions(open_actions, blocking_by_stage)
    execution_blocking_actions = _execution_blocking_actions(due_actions)
    execution_blocking_by_stage = _execution_blocking_by_stage(
        open_actions, blocking_by_stage
    )
    exploratory_progress_allowed = not execution_blocking_actions
    summary = {
        "schema_version": 1,
        "run_id": run_id,
        "stage_id": IMMUNE_STAGE_ID,
        "stage_name": STAGE_BY_ID[IMMUNE_STAGE_ID].name,
        "status": (
            "needs_data"
            if result["requirements"]
            else "needs_human_input" if due_actions else "complete"
        ),
        "computational_audit_status": "pass",
        "mode": "exploratory",
        "ruleset_id": result["ruleset_id"],
        "candidate_count": len(result["candidates"]),
        "evaluated_alignment_count": len(result["alignment_profiles"]),
        "evaluated_adapter_count": sum(
            state["status"] == "evaluated" for state in result["adapter_states"].values()
        ),
        "missing_requirement_count": len(result["requirements"]),
        "requirement_class_counts": requirement_class_counts(result["requirements"]),
        "open_human_actions": len(open_actions),
        "due_human_actions": len(due_actions),
        "exploratory_progress_allowed": exploratory_progress_allowed,
    }
    input_audit = {
        "stage_id": IMMUNE_STAGE_ID,
        "status": "pass",
        "source_run_id": analysis.source_manifest["run_id"],
        "specification": {
            "path": str(analysis.immune_specification_path),
            "sha256": sha256_file(analysis.immune_specification_path),
        },
        "inputs": {
            name: {"source_path": str(path), "sha256": sha256_file(path)}
            for name, path in analysis.input_paths.items()
            if name == "immune_specification"
            or name == "mhc_panel"
            or name.startswith("alignment:")
            or name in {f"adapter:{adapter_id}" for adapter_id in result["adapter_states"]}
        },
        "checks": [
            {"check_id": "stage3-parent-verified", "status": "pass"},
            {"check_id": "candidate-and-structure-maps-exact", "status": "pass"},
            {"check_id": "missing-evidence-remains-not-evaluated", "status": "pass"},
            {"check_id": "immune-release-gate-disabled", "status": "pass"},
        ],
    }
    process_record = {
        "stage_id": IMMUNE_STAGE_ID,
        "pipeline_version": __version__,
        "ruleset_id": result["ruleset_id"],
        "operations": [
            "verify_stage3_candidate_and_structure_identity",
            "compute_ca_neighbor_surface_proxy",
            "validate_and_project_optional_source_alignments",
            "validate_optional_checksum_bound_residue_evidence_adapters",
            "retain_missing_categories_as_not_evaluated",
        ],
        "policy": analysis.immune_specification["policy"],
    }
    output_audit = {
        "stage_id": IMMUNE_STAGE_ID,
        "status": "pass",
        "summary": summary,
        "candidate_ids": [item["candidate_id"] for item in result["candidates"]],
        "requirements": result["requirements"],
        "checks": [
            {"check_id": "all-candidates-have-residue-maps", "status": "pass"},
            {"check_id": "all-external-evidence-is-versioned", "status": "pass"},
            {"check_id": "no-missing-evidence-imputation", "status": "pass"},
            {"check_id": "no-efficacy-conclusion", "status": "pass"},
        ],
    }
    human_actions = {
        "stage_id": IMMUNE_STAGE_ID,
        "open_count": len(open_actions),
        "actions": actions,
    }
    handoff = {
        "schema_version": 1,
        "run_id": run_id,
        "from_stage": IMMUNE_STAGE_ID,
        "to_stage": "integrated_ranking",
        "readiness": "needs_data" if result["requirements"] else "exploratory_ready",
        "formal_readiness": "needs_human_input" if due_actions else "ready",
        "blocking_action_ids": [action["action_id"] for action in due_actions],
        "blocking_action_ids_by_stage": blocking_by_stage,
        "execution_blocking_action_ids": [
            action["action_id"] for action in execution_blocking_actions
        ],
        "execution_blocking_action_ids_by_stage": execution_blocking_by_stage,
        "exploratory_progress_allowed": exploratory_progress_allowed,
        "execution_readiness": (
            "exploratory_ready" if exploratory_progress_allowed else "blocked"
        ),
        "carried_human_actions": open_actions,
        "carried_forward": {
            "candidate_ids": [item["candidate_id"] for item in result["candidates"]],
            "immune_evidence_sha256": None,
        },
        "limitations": result["limitations"],
    }
    return {
        "summary": summary,
        "input_audit": input_audit,
        "process_record": process_record,
        "output_audit": output_audit,
        "human_actions": human_actions,
        "handoff": handoff,
        "result": result,
        "redesign_requests": redesign_requests,
    }


def _developability_bundle(
    analysis: PostStructureAnalysis,
    run_id: str,
) -> dict[str, Any]:
    result = analysis.developability_result
    requests = []
    ordinal = 0
    liability_row = 0
    for candidate in result["candidates"]:
        for liability in candidate["liabilities"]:
            liability_row += 1
            if liability["severity"] != "review":
                continue
            requests.append(
                {
                    "request_id": request_id(
                        DEVELOPABILITY_STAGE_ID,
                        candidate["candidate_id"],
                        liability["code"],
                        ordinal,
                    ),
                    "status": "proposed",
                    "candidate_id": candidate["candidate_id"],
                    "trigger": liability["code"],
                    "evidence_ref": f"liabilities.csv#row={liability_row}",
                    "requested_variable_ids": ["antigen.recipe"],
                    "instruction": (
                        "Review whether this liability should be accepted, mitigated experimentally, "
                        "or addressed by a child sequence proposal in the next immutable round."
                    ),
                    "authority": "deterministic_developability_rule",
                }
            )
            ordinal += 1
    redesign_requests = redesign_request_document(
        project_id=analysis.config.project_id,
        run_id=run_id,
        round_id=_design_round_id(analysis),
        stage_id=DEVELOPABILITY_STAGE_ID,
        requests=requests,
    )
    actions = merge_requirement_actions(
        _parent_actions(analysis),
        result["requirements"],
    )
    open_actions = [action for action in actions if action["status"] == "open"]
    target_stages = (
        "protein_product_design",
        "mrna_product_design",
    )
    blocking_by_stage = _blocking_actions_by_stage(
        open_actions,
        current_stage=DEVELOPABILITY_STAGE_ID,
        target_stages=target_stages,
    )
    due_actions = _due_actions(open_actions, blocking_by_stage)
    execution_blocking_actions = _execution_blocking_actions(due_actions)
    execution_blocking_by_stage = _execution_blocking_by_stage(
        open_actions, blocking_by_stage
    )
    exploratory_progress_allowed = not execution_blocking_actions
    review_count = sum(
        item["review_liability_count"] for item in result["candidates"]
    )
    summary = {
        "schema_version": 1,
        "run_id": run_id,
        "stage_id": DEVELOPABILITY_STAGE_ID,
        "stage_name": STAGE_BY_ID[DEVELOPABILITY_STAGE_ID].name,
        "status": (
            "needs_data"
            if result["requirements"]
            else "needs_human_input" if due_actions else "complete"
        ),
        "computational_audit_status": "pass",
        "mode": "exploratory",
        "ruleset_id": result["ruleset_id"],
        "candidate_count": len(result["candidates"]),
        "review_liability_count": review_count,
        "evaluated_adapter_count": sum(
            state["status"] == "evaluated" for state in result["adapter_states"].values()
        ),
        "missing_requirement_count": len(result["requirements"]),
        "requirement_class_counts": requirement_class_counts(result["requirements"]),
        "open_human_actions": len(open_actions),
        "due_human_actions": len(due_actions),
        "exploratory_progress_allowed": exploratory_progress_allowed,
    }
    relevant_adapter_names = {
        f"adapter:{adapter_id}" for adapter_id in result["adapter_states"]
    }
    input_audit = {
        "stage_id": DEVELOPABILITY_STAGE_ID,
        "status": "pass",
        "source_run_id": analysis.source_manifest["run_id"],
        "specification": {
            "path": str(analysis.developability_specification_path),
            "sha256": sha256_file(analysis.developability_specification_path),
        },
        "inputs": {
            name: {"source_path": str(path), "sha256": sha256_file(path)}
            for name, path in analysis.input_paths.items()
            if name == "developability_specification" or name in relevant_adapter_names
        },
        "checks": [
            {"check_id": "stage3-parent-verified", "status": "pass"},
            {"check_id": "intrinsic-rules-versioned", "status": "pass"},
            {"check_id": "external-gaps-not-evaluated", "status": "pass"},
            {"check_id": "developability-release-gate-disabled", "status": "pass"},
        ],
    }
    process_record = {
        "stage_id": DEVELOPABILITY_STAGE_ID,
        "pipeline_version": __version__,
        "ruleset_id": result["ruleset_id"],
        "operations": [
            "verify_stage3_candidate_and_structure_identity",
            "compute_intrinsic_hydrophobicity_charge_complexity_and_motif_descriptors",
            "carry_stage3_structure_confidence_and_boundary_flags",
            "validate_optional_checksum_bound_predictor_evidence",
            "retain_missing_predictors_as_not_evaluated",
        ],
        "policy": analysis.developability_specification["policy"],
    }
    output_audit = {
        "stage_id": DEVELOPABILITY_STAGE_ID,
        "status": "pass",
        "summary": summary,
        "candidate_ids": [item["candidate_id"] for item in result["candidates"]],
        "requirements": result["requirements"],
        "checks": [
            {"check_id": "all-liabilities-have-evidence", "status": "pass"},
            {"check_id": "intrinsic-and-predictive-evidence-separated", "status": "pass"},
            {"check_id": "no-missing-predictor-imputation", "status": "pass"},
            {"check_id": "no-manufacturing-success-conclusion", "status": "pass"},
        ],
    }
    human_actions = {
        "stage_id": DEVELOPABILITY_STAGE_ID,
        "open_count": len(open_actions),
        "actions": actions,
    }
    handoff = {
        "schema_version": 1,
        "run_id": run_id,
        "from_stage": DEVELOPABILITY_STAGE_ID,
        "to_stages": list(target_stages),
        "readiness": "intrinsic_evidence_ready",
        "formal_readiness": "needs_human_input" if due_actions else "ready",
        "blocking_action_ids": [action["action_id"] for action in due_actions],
        "blocking_action_ids_by_stage": blocking_by_stage,
        "execution_blocking_action_ids": [
            action["action_id"] for action in execution_blocking_actions
        ],
        "execution_blocking_action_ids_by_stage": execution_blocking_by_stage,
        "exploratory_progress_allowed": exploratory_progress_allowed,
        "execution_readiness": (
            "exploratory_ready" if exploratory_progress_allowed else "blocked"
        ),
        "carried_human_actions": open_actions,
        "carried_forward": {
            "candidate_ids": [item["candidate_id"] for item in result["candidates"]],
            "developability_assessments_sha256": None,
        },
        "limitations": result["limitations"],
    }
    return {
        "summary": summary,
        "input_audit": input_audit,
        "process_record": process_record,
        "output_audit": output_audit,
        "human_actions": human_actions,
        "handoff": handoff,
        "result": result,
        "redesign_requests": redesign_requests,
    }


def _write_node(
    node_dir: Path,
    bundle: dict[str, Any],
    *,
    result_name: str,
    report_html: str,
    candidate_csv: str,
    candidate_rows: list[dict[str, Any]],
    candidate_fields: list[str],
    detail_csv: str,
    detail_rows: list[dict[str, Any]],
    detail_fields: list[str],
) -> None:
    node_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(node_dir / result_name, _json_text(bundle["result"]))
    _atomic_write(
        node_dir / "redesign_requests.json",
        _json_text(bundle["redesign_requests"]),
    )
    for name in (
        "summary",
        "input_audit",
        "process_record",
        "output_audit",
        "human_actions",
        "handoff",
    ):
        _atomic_write(node_dir / f"{name}.json", _json_text(bundle[name]))
    _atomic_write(node_dir / candidate_csv, _csv_text(candidate_fields, candidate_rows))
    _atomic_write(node_dir / detail_csv, _csv_text(detail_fields, detail_rows))
    _atomic_write(node_dir / "report.html", report_html)


def _snapshot_inputs(
    analysis: PostStructureAnalysis,
    immune_node: Path,
    developability_node: Path,
) -> dict[str, tuple[str, str]]:
    snapshots: dict[str, tuple[str, str]] = {}
    for name, source in analysis.input_paths.items():
        if name == "developability_specification" or name.startswith(
            tuple(f"adapter:{adapter_id}" for adapter_id in analysis.developability_result["adapter_states"])
        ):
            destination_root = developability_node / "inputs"
        else:
            destination_root = immune_node / "inputs"
        destination_root.mkdir(parents=True, exist_ok=True)
        safe_name = name.replace(":", "--")
        suffix = "".join(source.suffixes) or ".dat"
        destination = destination_root / f"{safe_name}{suffix}"
        shutil.copyfile(source, destination)
        snapshots[name] = (
            destination_root.parent.name,
            destination.relative_to(destination_root.parent).as_posix(),
        )
    return snapshots


def write_post_structure_run(
    analysis: PostStructureAnalysis,
    *,
    now: datetime | None = None,
) -> Path:
    created = now or datetime.now(timezone.utc)
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    created = created.astimezone(timezone.utc)
    created_at = created.isoformat()
    identity = hashlib.sha256(
        (
            sha256_file(analysis.immune_specification_path)
            + sha256_file(analysis.developability_specification_path)
            + sha256_file(analysis.config.config_path)
            + analysis.source_manifest["run_id"]
        ).encode("ascii")
    ).hexdigest()
    run_id = f"{created.strftime('%Y%m%dT%H%M%S%fZ')}-stage4-5-{identity[:8]}"
    run_dir = analysis.config.run_root / run_id
    immune_node = run_dir / "nodes" / IMMUNE_STAGE_ID
    developability_node = run_dir / "nodes" / DEVELOPABILITY_STAGE_ID
    if run_dir.exists():
        raise ValueError(f"Refusing to overwrite Stage 4/5 run: {run_dir}")
    try:
        shutil.copytree(analysis.source_run_dir / "nodes", run_dir / "nodes")
        shutil.copytree(analysis.source_run_dir / "inputs", run_dir / "inputs")
        lineage_dir = run_dir / "inputs" / "lineage"
        lineage_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(
            analysis.source_run_dir / "manifest.json",
            lineage_dir / "stage3_parent_manifest.json",
        )
        shutil.copyfile(
            analysis.source_run_dir / ARTIFACT_INDEX_FILENAME,
            lineage_dir / "stage3_parent_artifact_index.json",
        )
        shutil.copyfile(
            analysis.source_run_dir / "inputs/project.json",
            lineage_dir / "stage3_parent_project.json",
        )
        continuation_dir = run_dir / "inputs" / "continuation"
        continuation_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(
            analysis.config.config_path,
            continuation_dir / "project.json",
        )
        immune_bundle = _immune_bundle(analysis, run_id)
        developability_bundle = _developability_bundle(analysis, run_id)
        immune_bundle["summary"]["created_at_utc"] = created_at
        developability_bundle["summary"]["created_at_utc"] = created_at
        snapshots = _snapshot_inputs(analysis, immune_node, developability_node)
        for name, (_, relative_path) in snapshots.items():
            if name in immune_bundle["input_audit"]["inputs"]:
                immune_bundle["input_audit"]["inputs"][name][
                    "snapshot_path"
                ] = relative_path
            if name in developability_bundle["input_audit"]["inputs"]:
                developability_bundle["input_audit"]["inputs"][name][
                    "snapshot_path"
                ] = relative_path
        _write_node(
            immune_node,
            immune_bundle,
            result_name="immune_evidence.json",
            report_html=render_immune_report(
                analysis.immune_result,
                immune_bundle["human_actions"]["actions"],
                run_id,
                created_at,
            ),
            candidate_csv="immune_candidates.csv",
            candidate_rows=[
                {
                    "candidate_id": item["candidate_id"],
                    "candidate_key": item["candidate_key"],
                    "status": item["status"],
                    "surface_proxy_exposed_fraction": item["categories"][
                        "surface_accessibility_proxy"
                    ]["exposed_fraction"],
                    "conservation_evaluated_fraction": item["categories"][
                        "pathogen_conservation"
                    ]["evaluated_residue_fraction"],
                    "mean_conservation_fraction": item["categories"][
                        "pathogen_conservation"
                    ]["mean_conservation_fraction"],
                }
                for item in analysis.immune_result["candidates"]
            ],
            candidate_fields=[
                "candidate_id", "candidate_key", "status",
                "surface_proxy_exposed_fraction", "conservation_evaluated_fraction",
                "mean_conservation_fraction",
            ],
            detail_csv="immune_requirements.csv",
            detail_rows=analysis.immune_result["requirements"],
            detail_fields=[
                "requirement_id",
                "status",
                "requirement_class",
                "required_before_stage",
                "resolution_strategy",
                "exploratory_progress_allowed",
                "description",
                "description_zh",
            ],
        )
        _write_node(
            developability_node,
            developability_bundle,
            result_name="developability_assessments.json",
            report_html=render_developability_report(
                analysis.developability_result,
                developability_bundle["human_actions"]["actions"],
                run_id,
                created_at,
            ),
            candidate_csv="developability_candidates.csv",
            candidate_rows=[
                {
                    "candidate_id": item["candidate_id"],
                    "candidate_key": item["candidate_key"],
                    **item["descriptors"],
                    "review_liability_count": item["review_liability_count"],
                    "information_liability_count": item["information_liability_count"],
                }
                for item in analysis.developability_result["candidates"]
            ],
            candidate_fields=[
                "candidate_id", "candidate_key", "gravy", "charge_proxy",
                "cysteine_count", "n_linked_glycosylation_sequon_count",
                "hydrophobic_region_count", "low_complexity_region_count",
                "homopolymer_count", "review_liability_count",
                "information_liability_count",
            ],
            detail_csv="liabilities.csv",
            detail_rows=[
                {
                    "candidate_id": candidate["candidate_id"],
                    "candidate_key": candidate["candidate_key"],
                    **liability,
                    "evidence": json.dumps(liability["evidence"], sort_keys=True),
                }
                for candidate in analysis.developability_result["candidates"]
                for liability in candidate["liabilities"]
            ],
            detail_fields=[
                "candidate_id", "candidate_key", "code", "severity", "start",
                "end", "evidence", "interpretation",
            ],
        )
        immune_bundle["handoff"]["carried_forward"]["immune_evidence_sha256"] = sha256_file(
            immune_node / "immune_evidence.json"
        )
        developability_bundle["handoff"]["carried_forward"][
            "developability_assessments_sha256"
        ] = sha256_file(developability_node / "developability_assessments.json")
        _atomic_write(immune_node / "handoff.json", _json_text(immune_bundle["handoff"]))
        _atomic_write(
            developability_node / "handoff.json",
            _json_text(developability_bundle["handoff"]),
        )

        workflow = workflow_contract()
        workflow["contract_sha256"] = workflow_contract_sha256()
        workflow["run_id"] = run_id
        workflow["current_stage"] = DEVELOPABILITY_STAGE_ID
        parent_workflow = json.loads(
            (analysis.source_run_dir / "workflow.json").read_text(encoding="utf-8")
        )
        parent_status = {
            stage["stage_id"]: stage["status"] for stage in parent_workflow["stages"]
        }
        for stage in workflow["stages"]:
            if stage["stage_id"] == IMMUNE_STAGE_ID:
                stage["status"] = immune_bundle["summary"]["status"]
            elif stage["stage_id"] == DEVELOPABILITY_STAGE_ID:
                stage["status"] = developability_bundle["summary"]["status"]
            else:
                stage["status"] = parent_status.get(stage["stage_id"], "not_evaluated")
        _atomic_write(run_dir / "workflow.json", _json_text(workflow))

        parent_nodes = copy.deepcopy(analysis.source_manifest["nodes"])
        parent_nodes[IMMUNE_STAGE_ID] = {
            "status": immune_bundle["summary"]["status"],
            "summary": f"nodes/{IMMUNE_STAGE_ID}/summary.json",
            "report": f"nodes/{IMMUNE_STAGE_ID}/report.html",
        }
        parent_nodes[DEVELOPABILITY_STAGE_ID] = {
            "status": developability_bundle["summary"]["status"],
            "summary": f"nodes/{DEVELOPABILITY_STAGE_ID}/summary.json",
            "report": f"nodes/{DEVELOPABILITY_STAGE_ID}/report.html",
        }
        parent_index_sha = sha256_file(
            analysis.source_run_dir / ARTIFACT_INDEX_FILENAME
        )
        manifest = {
            "schema_version": 1,
            "pipeline_version": __version__,
            "project_id": analysis.config.project_id,
            "run_id": run_id,
            "created_at_utc": created_at,
            "status": "needs_data"
            if analysis.immune_result["requirements"]
            or analysis.developability_result["requirements"]
            else "needs_human_input",
            "runtime_root": str(analysis.config.runtime_root),
            "current_stage": DEVELOPABILITY_STAGE_ID,
            "executed_stages": [IMMUNE_STAGE_ID, DEVELOPABILITY_STAGE_ID],
            "lineage": {
                "parent_run_id": analysis.source_manifest["run_id"],
                "parent_run_path": str(analysis.source_run_dir),
                "parent_artifact_index_sha256": parent_index_sha,
            },
            "context": project_context(analysis.config),
            "counts": {
                **analysis.source_manifest["counts"],
                "immune_missing_requirements": len(analysis.immune_result["requirements"]),
                "developability_missing_requirements": len(
                    analysis.developability_result["requirements"]
                ),
                "developability_review_liabilities": sum(
                    item["review_liability_count"]
                    for item in analysis.developability_result["candidates"]
                ),
            },
            "inputs": {
                "parent_run_id": analysis.source_manifest["run_id"],
                "project_configuration_sha256": sha256_file(
                    analysis.config.config_path
                ),
                "immune_specification_sha256": sha256_file(
                    analysis.immune_specification_path
                ),
                "developability_specification_sha256": sha256_file(
                    analysis.developability_specification_path
                ),
            },
            "nodes": parent_nodes,
            "artifacts": {
                "workflow": "workflow.json",
                "immune_handoff": f"nodes/{IMMUNE_STAGE_ID}/handoff.json",
                "developability_handoff": (
                    f"nodes/{DEVELOPABILITY_STAGE_ID}/handoff.json"
                ),
                "artifact_index": ARTIFACT_INDEX_FILENAME,
            },
        }
        _atomic_write(run_dir / "manifest.json", _json_text(manifest))
        index = build_artifact_index(run_dir, analysis.config.project_id, run_id)
        _atomic_write(run_dir / ARTIFACT_INDEX_FILENAME, _json_text(index))
        verification = verify_run(run_dir)
        if verification["status"] != "pass":
            raise ValueError(
                "Stage 4/5 run verification failed; latest was not updated: "
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
                    "current_stage": DEVELOPABILITY_STAGE_ID,
                    "executed_stages": [IMMUNE_STAGE_ID, DEVELOPABILITY_STAGE_ID],
                    "status": manifest["status"],
                    "reports": {
                        IMMUNE_STAGE_ID: str(immune_node / "report.html"),
                        DEVELOPABILITY_STAGE_ID: str(
                            developability_node / "report.html"
                        ),
                    },
                    "report_path": str(developability_node / "report.html"),
                    "artifact_index_path": str(run_dir / ARTIFACT_INDEX_FILENAME),
                    "artifact_index_sha256": sha256_file(
                        run_dir / ARTIFACT_INDEX_FILENAME
                    ),
                    "verification_status": "pass",
                }
            ),
        )
        return run_dir
    except Exception:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise
