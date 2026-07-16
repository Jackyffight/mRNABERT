"""Write one immutable continuation run for both Stage 6 product branches."""

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
from .design_loop import redesign_request_document, request_id
from .product_design import ProductDesignAnalysis
from .product_html import render_mrna_product_report, render_protein_product_report
from .product_specs import MRNA_PRODUCT_STAGE_ID, PROTEIN_PRODUCT_STAGE_ID
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


def _fasta(records: list[tuple[str, str]]) -> str:
    return "".join(
        f">{record_id}\n" + "\n".join(
            sequence[index : index + 80] for index in range(0, len(sequence), 80)
        ) + "\n"
        for record_id, sequence in records
    )


def _parent_actions(analysis: ProductDesignAnalysis) -> list[dict[str, Any]]:
    actions: dict[str, dict[str, Any]] = {}
    for stage_id in ("immune_evidence_assessment", "developability_assessment"):
        handoff_path = analysis.source_run_dir / "nodes" / stage_id / "handoff.json"
        handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
        for action in handoff.get("carried_human_actions", []):
            actions[action["action_id"]] = dict(action)
    return list(actions.values())


def _design_round_id(analysis: ProductDesignAnalysis) -> str:
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


def _actions(
    parent: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
    *,
    required_before_stage: str,
) -> list[dict[str, Any]]:
    merged = {action["action_id"]: dict(action) for action in parent}
    for requirement in requirements:
        merged.setdefault(
            requirement["requirement_id"],
            {
                "action_id": requirement["requirement_id"],
                "question": requirement["description"],
                "question_zh": "补充、审核并版本化该输入；在完成前不得作为正式放行依据。",
                "required_before_stage": required_before_stage,
                "status": "open",
                "owner": "unassigned",
                "resolution": "",
                "resolution_zh": "",
            },
        )
    return list(merged.values())


def _bundle(
    analysis: ProductDesignAnalysis,
    *,
    run_id: str,
    stage_id: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    actions = _actions(
        _parent_actions(analysis),
        result["requirements"],
        required_before_stage="integrated_ranking",
    )
    open_actions = [action for action in actions if action["status"] == "open"]
    due_actions = [
        action
        for action in open_actions
        if action_due_for_handoff(
            action["required_before_stage"],
            current_stage=stage_id,
            to_stages=("integrated_ranking",),
        )
    ]
    item_name = "products" if stage_id == PROTEIN_PRODUCT_STAGE_ID else "designs"
    requests = []
    if stage_id == MRNA_PRODUCT_STAGE_ID:
        requests = [
            {
                "request_id": request_id(
                    stage_id,
                    rejected["candidate_id"],
                    str(rejected["reason"]),
                    index,
                ),
                "status": "proposed",
                "candidate_id": rejected["candidate_id"],
                "trigger": str(rejected["reason"]),
                "evidence_ref": f"rejected_designs.csv#row={index + 1}",
                "requested_variable_ids": ["mrna.synonymous_coding_sequence"],
                "instruction": (
                    "Generate a new synonymous CDS child in the next round under the same protein "
                    "identity and hard constraints; retain this rejected design as evidence."
                ),
                "authority": "deterministic_mrna_constraint",
            }
            for index, rejected in enumerate(result["rejected_designs"])
        ]
    redesign_requests = redesign_request_document(
        project_id=analysis.config.project_id,
        run_id=run_id,
        round_id=_design_round_id(analysis),
        stage_id=stage_id,
        requests=requests,
    )
    summary = {
        "schema_version": 1,
        "run_id": run_id,
        "stage_id": stage_id,
        "stage_name": STAGE_BY_ID[stage_id].name,
        "status": (
            "needs_data"
            if result["requirements"]
            else "needs_human_input" if due_actions else "complete"
        ),
        "computational_audit_status": "pass",
        "mode": "exploratory",
        "ruleset_id": result["ruleset_id"],
        "design_count": len(result[item_name]),
        "routing_id": result["routing"]["routing_id"],
        "routing_counts": result["routing"]["counts"],
        "missing_requirement_count": len(result["requirements"]),
        "open_human_actions": len(open_actions),
        "due_human_actions": len(due_actions),
    }
    relevant_prefix = "protein_" if stage_id == PROTEIN_PRODUCT_STAGE_ID else "mrna_"
    specification_name = f"{relevant_prefix}specification"
    inputs = {
        name: {"source_path": str(path), "sha256": sha256_file(path)}
        for name, path in analysis.input_paths.items()
        if name == specification_name or name.startswith(relevant_prefix)
    }
    return {
        "summary": summary,
        "input_audit": {
            "stage_id": stage_id,
            "status": "pass",
            "source_run_id": analysis.source_manifest["run_id"],
            "inputs": inputs,
            "checks": [
                {"check_id": "stage4-5-parent-verified", "status": "pass"},
                {"check_id": "candidate-bindings-exact", "status": "pass"},
                {"check_id": "candidate-routing-exact", "status": "pass"},
                {"check_id": "translation-identity-enforced", "status": "pass"},
                {"check_id": "product-release-gate-disabled", "status": "pass"},
            ],
        },
        "process_record": {
            "stage_id": stage_id,
            "pipeline_version": __version__,
            "ruleset_id": result["ruleset_id"],
            "operations": (
                [
                    "bind_antigen_candidate_hashes",
                    "enforce_stage6_candidate_routing",
                    "assemble_declared_expression_elements",
                    "separate_antigen_expression_and_final_product_sequences",
                    "verify_exact_cds_translation",
                    "prepare_structure_recheck_payload_for_changed_constructs",
                ]
                if stage_id == PROTEIN_PRODUCT_STAGE_ID
                else [
                    "bind_antigen_candidate_hashes",
                    "enforce_stage6_candidate_routing",
                    "retain_source_cds_controls",
                    "import_declared_coding_controls_with_exact_translation_audit",
                    "generate_seeded_synonymous_trials_when_enabled",
                    "apply_hard_sequence_constraints",
                    "select_deterministic_pareto_designs",
                    "verify_exact_translation_for_every_design",
                ]
            ),
        },
        "output_audit": {
            "stage_id": stage_id,
            "status": "pass",
            "summary": summary,
            "requirements": result["requirements"],
            "checks": [
                {"check_id": "exact-sequences-and-hashes-present", "status": "pass"},
                {"check_id": "expensive-followup-routing-enforced", "status": "pass"},
                {"check_id": "missing-model-evidence-not-imputed", "status": "pass"},
                {"check_id": "no-synthesis-release-claim", "status": "pass"},
            ],
        },
        "human_actions": {
            "stage_id": stage_id,
            "open_count": len(open_actions),
            "actions": actions,
        },
        "handoff": {
            "schema_version": 1,
            "run_id": run_id,
            "from_stage": stage_id,
            "to_stage": "integrated_ranking",
            "readiness": "needs_data" if result["requirements"] else "exploratory_ready",
            "formal_readiness": "needs_human_input" if due_actions else "ready",
            "blocking_action_ids": [action["action_id"] for action in due_actions],
            "carried_human_actions": open_actions,
            "carried_forward": {
                "result_sha256": None,
                "routing_id": result["routing"]["routing_id"],
            },
            "limitations": result["limitations"],
        },
        "result": result,
        "redesign_requests": redesign_requests,
    }


def _snapshot_inputs(
    analysis: ProductDesignAnalysis,
    protein_node: Path,
    mrna_node: Path,
) -> dict[str, tuple[Path, str]]:
    snapshots: dict[str, tuple[Path, str]] = {}
    for name, source in analysis.input_paths.items():
        node = protein_node if name.startswith("protein_") else mrna_node
        destination_root = node / "inputs"
        destination_root.mkdir(parents=True, exist_ok=True)
        safe_name = name.replace(":", "--")
        suffix = "".join(source.suffixes) or ".dat"
        destination = destination_root / f"{safe_name}{suffix}"
        shutil.copyfile(source, destination)
        snapshots[name] = (node, destination.relative_to(node).as_posix())
    return snapshots


def _write_node(
    node: Path,
    bundle: dict[str, Any],
    *,
    result_name: str,
    report: str,
) -> None:
    node.mkdir(parents=True, exist_ok=True)
    _atomic_write(node / result_name, _json_text(bundle["result"]))
    _atomic_write(
        node / "redesign_requests.json",
        _json_text(bundle["redesign_requests"]),
    )
    for name in ("summary", "input_audit", "process_record", "output_audit", "human_actions", "handoff"):
        _atomic_write(node / f"{name}.json", _json_text(bundle[name]))
    _atomic_write(node / "report.html", report)


def write_product_design_run(
    analysis: ProductDesignAnalysis,
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
            sha256_file(analysis.protein_specification_path)
            + sha256_file(analysis.mrna_specification_path)
            + analysis.source_manifest["run_id"]
        ).encode("ascii")
    ).hexdigest()
    run_id = f"{created.strftime('%Y%m%dT%H%M%S%fZ')}-stage6-{identity[:8]}"
    run_dir = analysis.config.run_root / run_id
    protein_node = run_dir / "nodes" / PROTEIN_PRODUCT_STAGE_ID
    mrna_node = run_dir / "nodes" / MRNA_PRODUCT_STAGE_ID
    if run_dir.exists():
        raise ValueError(f"Refusing to overwrite Stage 6 run: {run_dir}")
    try:
        shutil.copytree(analysis.source_run_dir / "nodes", run_dir / "nodes")
        shutil.copytree(analysis.source_run_dir / "inputs", run_dir / "inputs")
        lineage = run_dir / "inputs/lineage"
        lineage.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(
            analysis.source_run_dir / "manifest.json",
            lineage / "stage5_parent_manifest.json",
        )
        shutil.copyfile(
            analysis.source_run_dir / ARTIFACT_INDEX_FILENAME,
            lineage / "stage5_parent_artifact_index.json",
        )
        protein_bundle = _bundle(
            analysis,
            run_id=run_id,
            stage_id=PROTEIN_PRODUCT_STAGE_ID,
            result=analysis.protein_result,
        )
        mrna_bundle = _bundle(
            analysis,
            run_id=run_id,
            stage_id=MRNA_PRODUCT_STAGE_ID,
            result=analysis.mrna_result,
        )
        protein_bundle["summary"]["created_at_utc"] = created_at
        mrna_bundle["summary"]["created_at_utc"] = created_at
        snapshots = _snapshot_inputs(analysis, protein_node, mrna_node)
        for name, (_, relative) in snapshots.items():
            bundle = protein_bundle if name.startswith("protein_") else mrna_bundle
            if name in bundle["input_audit"]["inputs"]:
                bundle["input_audit"]["inputs"][name]["snapshot_path"] = relative
        _write_node(
            protein_node,
            protein_bundle,
            result_name="protein_products.json",
            report=render_protein_product_report(
                analysis.protein_result,
                protein_bundle["human_actions"]["actions"],
                run_id,
                created_at,
            ),
        )
        _write_node(
            mrna_node,
            mrna_bundle,
            result_name="mrna_products.json",
            report=render_mrna_product_report(
                analysis.mrna_result,
                mrna_bundle["human_actions"]["actions"],
                run_id,
                created_at,
            ),
        )
        products = analysis.protein_result["products"]
        designs = analysis.mrna_result["designs"]
        _atomic_write(
            protein_node / "products.csv",
            _csv_text(
                [
                    "design_id", "candidate_id", "candidate_key", "coding_source",
                    "routing_lane", "expensive_followup_eligible",
                    "translation_verified", "requires_structure_recheck", "status",
                ],
                products,
            ),
        )
        _atomic_write(
            protein_node / "expression_constructs.fasta",
            _fasta([(item["design_id"], item["expression_sequence"]) for item in products]),
        )
        _atomic_write(
            protein_node / "final_products.fasta",
            _fasta([(item["design_id"], item["final_product_sequence"]) for item in products]),
        )
        _atomic_write(
            protein_node / "coding_sequences.fasta",
            _fasta([
                (item["design_id"], item["coding_sequence_dna"])
                for item in products
                if item["coding_sequence_dna"]
            ]),
        )
        recheck = [
            item
            for item in products
            if item["requires_structure_recheck"]
            and item["expensive_followup_eligible"]
        ]
        _atomic_write(
            protein_node / "structure_recheck_candidates.fasta",
            _fasta([(item["design_id"], item["expression_sequence"]) for item in recheck]),
        )
        _atomic_write(
            protein_node / "structure_recheck_job.json",
            _json_text({
                "schema_version": "vaxflow.structure-recheck-job.v1",
                "product_batch_sha256": analysis.protein_result["product_batch_sha256"],
                "model_adapter": "ESMFold2",
                "records": [
                    {
                        "design_id": item["design_id"],
                        "sequence_sha256": item["expression_sequence_sha256"],
                        "length": len(item["expression_sequence"]),
                    }
                    for item in recheck
                ],
                "status": "ready" if recheck else "not_required",
            }),
        )
        _atomic_write(
            protein_node / "model_followup_manifest.json",
            _json_text(
                {
                    "schema_version": "vaxflow.stage6-model-followup.v1",
                    "routing_id": analysis.routing_manifest["routing_id"],
                    "modality": "recombinant_protein",
                    "records": [
                        {
                            "design_id": item["design_id"],
                            "candidate_id": item["candidate_id"],
                            "routing_lane": item["routing_lane"],
                            "sequence_sha256": item[
                                "expression_sequence_sha256"
                            ],
                            "requires_structure_recheck": item[
                                "requires_structure_recheck"
                            ],
                        }
                        for item in products
                        if item["expensive_followup_eligible"]
                    ],
                }
            ),
        )
        _atomic_write(
            mrna_node / "designs.csv",
            _csv_text(
                [
                    "design_id", "candidate_id", "candidate_key", "design_type",
                    "routing_lane", "expensive_followup_eligible",
                    "selection_basis", "translation_verified", "status",
                    "coding_sequence_sha256",
                ],
                designs,
            ),
        )
        _atomic_write(
            mrna_node / "coding_designs.fasta",
            _fasta([(item["design_id"], item["coding_sequence_dna"]) for item in designs]),
        )
        _atomic_write(
            mrna_node / "full_mrna_designs.fasta",
            _fasta([
                (item["design_id"], item["full_mrna_sequence"])
                for item in designs
                if item["full_mrna_sequence"]
            ]),
        )
        _atomic_write(
            mrna_node / "rejected_designs.csv",
            _csv_text(
                ["candidate_id", "coding_sequence_sha256", "reason", "metrics"],
                [
                    {**item, "metrics": json.dumps(item["metrics"], sort_keys=True)}
                    for item in analysis.mrna_result["rejected_designs"]
                ],
            ),
        )
        _atomic_write(
            mrna_node / "model_followup_manifest.json",
            _json_text(
                {
                    "schema_version": "vaxflow.stage6-model-followup.v1",
                    "routing_id": analysis.routing_manifest["routing_id"],
                    "modality": "mrna",
                    "records": [
                        {
                            "design_id": item["design_id"],
                            "candidate_id": item["candidate_id"],
                            "routing_lane": item["routing_lane"],
                            "coding_sequence_sha256": item[
                                "coding_sequence_sha256"
                            ],
                        }
                        for item in designs
                        if item["expensive_followup_eligible"]
                    ],
                }
            ),
        )
        protein_bundle["handoff"]["carried_forward"]["result_sha256"] = sha256_file(
            protein_node / "protein_products.json"
        )
        mrna_bundle["handoff"]["carried_forward"]["result_sha256"] = sha256_file(
            mrna_node / "mrna_products.json"
        )
        _atomic_write(protein_node / "handoff.json", _json_text(protein_bundle["handoff"]))
        _atomic_write(mrna_node / "handoff.json", _json_text(mrna_bundle["handoff"]))

        workflow = workflow_contract()
        workflow["contract_sha256"] = workflow_contract_sha256()
        workflow["run_id"] = run_id
        workflow["current_stage"] = MRNA_PRODUCT_STAGE_ID
        parent_workflow = json.loads(
            (analysis.source_run_dir / "workflow.json").read_text(encoding="utf-8")
        )
        parent_status = {stage["stage_id"]: stage["status"] for stage in parent_workflow["stages"]}
        for stage in workflow["stages"]:
            if stage["stage_id"] == PROTEIN_PRODUCT_STAGE_ID:
                stage["status"] = protein_bundle["summary"]["status"]
            elif stage["stage_id"] == MRNA_PRODUCT_STAGE_ID:
                stage["status"] = mrna_bundle["summary"]["status"]
            else:
                stage["status"] = parent_status.get(stage["stage_id"], "not_evaluated")
        _atomic_write(run_dir / "workflow.json", _json_text(workflow))
        nodes = copy.deepcopy(analysis.source_manifest["nodes"])
        for stage_id, bundle in (
            (PROTEIN_PRODUCT_STAGE_ID, protein_bundle),
            (MRNA_PRODUCT_STAGE_ID, mrna_bundle),
        ):
            nodes[stage_id] = {
                "status": bundle["summary"]["status"],
                "summary": f"nodes/{stage_id}/summary.json",
                "report": f"nodes/{stage_id}/report.html",
            }
        parent_index_sha = sha256_file(analysis.source_run_dir / ARTIFACT_INDEX_FILENAME)
        status = "needs_data" if (
            analysis.protein_result["requirements"] or analysis.mrna_result["requirements"]
        ) else "needs_human_input"
        manifest = {
            "schema_version": 1,
            "pipeline_version": __version__,
            "project_id": analysis.config.project_id,
            "run_id": run_id,
            "created_at_utc": created_at,
            "status": status,
            "runtime_root": str(analysis.config.runtime_root),
            "current_stage": MRNA_PRODUCT_STAGE_ID,
            "executed_stages": [PROTEIN_PRODUCT_STAGE_ID, MRNA_PRODUCT_STAGE_ID],
            "lineage": {
                "parent_run_id": analysis.source_manifest["run_id"],
                "parent_run_path": str(analysis.source_run_dir),
                "parent_artifact_index_sha256": parent_index_sha,
            },
            "context": analysis.source_manifest["context"],
            "counts": {
                **analysis.source_manifest["counts"],
                "protein_products": len(products),
                "mrna_designs": len(designs),
                "stage6_priority_candidates": analysis.routing_manifest["counts"][
                    "priority"
                ],
                "stage6_diversity_rescue_candidates": analysis.routing_manifest[
                    "counts"
                ]["diversity_rescue"],
                "stage6_archive_candidates": analysis.routing_manifest["counts"][
                    "archive"
                ],
                "protein_missing_requirements": len(analysis.protein_result["requirements"]),
                "mrna_missing_requirements": len(analysis.mrna_result["requirements"]),
            },
            "inputs": {
                "parent_run_id": analysis.source_manifest["run_id"],
                "protein_specification_sha256": sha256_file(analysis.protein_specification_path),
                "mrna_specification_sha256": sha256_file(analysis.mrna_specification_path),
                "routing_manifest_sha256": sha256_file(
                    analysis.routing_manifest_path
                ),
                "routing_policy_sha256": sha256_file(
                    analysis.routing_policy_path
                ),
            },
            "nodes": nodes,
            "artifacts": {
                "workflow": "workflow.json",
                "protein_handoff": f"nodes/{PROTEIN_PRODUCT_STAGE_ID}/handoff.json",
                "mrna_handoff": f"nodes/{MRNA_PRODUCT_STAGE_ID}/handoff.json",
                "artifact_index": ARTIFACT_INDEX_FILENAME,
            },
        }
        _atomic_write(run_dir / "manifest.json", _json_text(manifest))
        _atomic_write(
            run_dir / ARTIFACT_INDEX_FILENAME,
            _json_text(build_artifact_index(run_dir, analysis.config.project_id, run_id)),
        )
        verification = verify_run(run_dir)
        if verification["status"] != "pass":
            raise ValueError(
                "Stage 6 run verification failed; latest was not updated: "
                + "; ".join(verification["errors"][:5])
            )
        _atomic_write(
            analysis.config.run_root / "latest.json",
            _json_text({
                "schema_version": 1,
                "project_id": analysis.config.project_id,
                "run_id": run_id,
                "run_path": str(run_dir),
                "current_stage": MRNA_PRODUCT_STAGE_ID,
                "executed_stages": [PROTEIN_PRODUCT_STAGE_ID, MRNA_PRODUCT_STAGE_ID],
                "status": status,
                "reports": {
                    PROTEIN_PRODUCT_STAGE_ID: str(protein_node / "report.html"),
                    MRNA_PRODUCT_STAGE_ID: str(mrna_node / "report.html"),
                },
                "report_path": str(mrna_node / "report.html"),
                "artifact_index_path": str(run_dir / ARTIFACT_INDEX_FILENAME),
                "artifact_index_sha256": sha256_file(run_dir / ARTIFACT_INDEX_FILENAME),
                "verification_status": "pass",
            }),
        )
        return run_dir
    except Exception:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise
