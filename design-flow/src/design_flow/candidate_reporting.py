"""Immutable continuation-run artifacts for candidate specification."""

from __future__ import annotations

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
from .candidate_html_report import render_candidate_report
from .candidate_specification import (
    CANDIDATE_STAGE_ID,
    NEXT_STAGE_ID,
    CandidateBatchAnalysis,
)
from .verification import ARTIFACT_INDEX_FILENAME, build_artifact_index, sha256_file, verify_run
from .workflow import STAGE_BY_ID, workflow_contract, workflow_contract_sha256


SOURCE_STAGE_ID = "program_and_source_intake"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def _csv_text(fieldnames: list[str], rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _wrap_fasta(sequence: str, width: int = 80) -> str:
    return "\n".join(sequence[offset : offset + width] for offset in range(0, len(sequence), width))


def _candidate_csv_rows(analysis: CandidateBatchAnalysis) -> list[dict[str, Any]]:
    return [
        {
            "candidate_key": candidate.candidate_key,
            "candidate_id": candidate.candidate_id,
            "display_name": candidate.display_name,
            "candidate_type": candidate.candidate_type,
            "computational_status": candidate.computational_status,
            "release_status": candidate.release_status,
            "annotation_status": candidate.annotation_status,
            "aa_length": len(candidate.amino_acid_sequence),
            "cds_length_nt": len(candidate.nucleotide_sequence or ""),
            "translation_relation": candidate.translation_relation["relation"],
            "exploratory_structure_ready": candidate.exploratory_structure_ready,
            "formal_structure_ready": candidate.formal_structure_ready,
            "duplicate_of": candidate.duplicate_of or "",
        }
        for candidate in analysis.candidates
    ]


def _component_rows(analysis: CandidateBatchAnalysis) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in analysis.candidates:
        for index, component in enumerate(candidate.inferred_components, start=1):
            rows.append(
                {
                    "candidate_key": candidate.candidate_key,
                    "candidate_id": candidate.candidate_id,
                    "component_index": index,
                    "component_type": component["component_type"],
                    "source_protein_id": component.get("source_protein_id", ""),
                    "source_start": component.get("source_start", ""),
                    "source_end": component.get("source_end", ""),
                    "candidate_start": component["candidate_start"],
                    "candidate_end": component["candidate_end"],
                    "sequence": component["sequence"],
                    "sequence_sha256": component["sequence_sha256"],
                }
            )
    return rows


def _finding_rows(analysis: CandidateBatchAnalysis) -> list[dict[str, Any]]:
    return [issue.to_dict() for issue in analysis.all_issues]


def _merge_actions(analysis: CandidateBatchAnalysis) -> list[dict[str, Any]]:
    actions = [dict(action) for action in analysis.source_handoff.get("carried_human_actions", [])]
    known_ids = {action.get("action_id") for action in actions}
    generated: list[dict[str, str]] = []
    if analysis.specification.generation_grammar["status"] != "approved":
        generated.append(
            {
                "action_id": "approve-candidate-generation-grammar",
                "question": "Approve the allowed source segments, ordering rules, linker families, tags, and generation limits.",
                "question_zh": "确认允许使用的源片段、排列规则、linker 家族、标签和候选生成边界。",
                "required_before_stage": NEXT_STAGE_ID,
                "status": "open",
                "owner": "unassigned",
                "resolution": "",
                "resolution_zh": "",
            }
        )
    if analysis.specification.release_mode != "approved":
        generated.append(
            {
                "action_id": "approve-candidate-batch",
                "question": "Approve the exact candidate batch before formal structure-model evaluation.",
                "question_zh": "在正式结构模型评估前确认本批候选的精确序列和组成。",
                "required_before_stage": NEXT_STAGE_ID,
                "status": "open",
                "owner": "unassigned",
                "resolution": "",
                "resolution_zh": "",
            }
        )
    actions.extend(action for action in generated if action["action_id"] not in known_ids)
    return actions


def build_candidate_node_bundle(
    analysis: CandidateBatchAnalysis,
    run_id: str,
) -> dict[str, Any]:
    actions = _merge_actions(analysis)
    open_actions = [action for action in actions if action["status"] == "open"]
    due_actions = [
        action
        for action in open_actions
        if action["required_before_stage"] in {CANDIDATE_STAGE_ID, NEXT_STAGE_ID}
    ]
    if analysis.computational_status == "fail":
        status, readiness = "blocked", "blocked"
    elif due_actions:
        status, readiness = "needs_human_input", "needs_human_input"
    else:
        status, readiness = "complete", "ready"

    candidates = [candidate.to_dict() for candidate in analysis.candidates]
    eligible = [
        candidate
        for candidate in candidates
        if candidate["exploratory_structure_ready"] and candidate["duplicate_of"] is None
    ]
    release_ready = [candidate for candidate in candidates if candidate["formal_structure_ready"]]
    errors = sum(issue.severity == "error" for issue in analysis.all_issues)
    warnings = sum(issue.severity == "warning" for issue in analysis.all_issues)
    input_records = {
        name: {"path": str(path), "sha256": analysis.input_digests[name]}
        for name, path in analysis.input_paths.items()
    }
    input_audit = {
        "stage_id": CANDIDATE_STAGE_ID,
        "status": analysis.computational_status,
        "source_run": {
            "run_id": analysis.source_run_id,
            "path": str(analysis.source_run_dir),
            "artifact_index_sha256": sha256_file(
                analysis.source_run_dir / ARTIFACT_INDEX_FILENAME
            ),
            "handoff_readiness": analysis.source_handoff.get("readiness"),
        },
        "inputs": input_records,
        "checks": [
            {
                "check_id": "source-run-verified",
                "status": "pass",
                "evidence": f"source_run={analysis.source_run_id}",
            },
            {
                "check_id": "source-handoff-released",
                "status": "pass" if analysis.source_handoff.get("readiness") == "ready" else "warning",
                "evidence": f"readiness={analysis.source_handoff.get('readiness')}",
            },
            {
                "check_id": "candidate-specification-hashed",
                "status": "pass",
                "evidence": analysis.specification.sha256,
            },
            {
                "check_id": "manual-inputs-hashed",
                "status": "pass",
                "evidence": f"files={len(input_records) - 1}",
            },
            {
                "check_id": "generation-grammar-approved",
                "status": (
                    "pass"
                    if analysis.specification.generation_grammar["status"] == "approved"
                    else "warning"
                ),
                "evidence": f"status={analysis.specification.generation_grammar['status']}",
            },
        ],
        "findings": _finding_rows(analysis),
    }
    process_record = {
        "stage_id": CANDIDATE_STAGE_ID,
        "pipeline_version": __version__,
        "operations": [
            {
                "operation": "verify_source_handoff",
                "behavior": "Verify the immutable stage-1 run and preserve its exact node artifacts.",
            },
            {
                "operation": "normalize_manual_candidates",
                "behavior": "Parse one AA record and optional CDS record per explicitly declared manual candidate.",
            },
            {
                "operation": "reconcile_translation",
                "behavior": "Classify exact translation, terminal additions, or irreconcilable mismatch without changing either sequence.",
            },
            {
                "operation": "infer_component_lineage",
                "behavior": "Map exact AA segments back to source proteins and compare inferred ranges/order with supplied claims.",
            },
            {
                "operation": "deduplicate_model_inputs",
                "behavior": "Retain every alias in the manifest while executing identical AA sequences only once per model adapter.",
            },
            {
                "operation": "prepare_structure_handoff",
                "behavior": "Export canonical exploratory AA inputs; keep formal release blocked until human gates are resolved.",
            },
        ],
        "parameters": {
            "specification_id": analysis.specification.specification_id,
            "release_mode": analysis.specification.release_mode,
            "generation_grammar": analysis.specification.generation_grammar,
        },
    }
    output_summary = {
        "candidate_count": len(candidates),
        "source_control_count": sum(candidate["candidate_type"] == "source_control" for candidate in candidates),
        "manual_candidate_count": sum(candidate["candidate_type"] != "source_control" for candidate in candidates),
        "released_count": sum(candidate["release_status"] == "released" for candidate in candidates),
        "quarantined_count": sum(candidate["release_status"] == "quarantined" for candidate in candidates),
        "rejected_count": sum(candidate["release_status"] == "rejected" for candidate in candidates),
        "exploratory_structure_ready_count": len(eligible),
        "formal_structure_ready_count": len(release_ready),
        "errors": errors,
        "warnings": warnings,
    }
    output_audit = {
        "stage_id": CANDIDATE_STAGE_ID,
        "status": analysis.computational_status,
        "summary": output_summary,
        "candidates": [
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
        ],
        "checks": [
            {
                "check_id": "candidate-identities-unique",
                "status": "pass" if len({candidate["candidate_id"] for candidate in candidates}) == len(candidates) else "fail",
            },
            {
                "check_id": "component-maps-cover-sequences",
                "status": "pass" if all(
                    "".join(component["sequence"] for component in candidate["inferred_components"])
                    == candidate["amino_acid_sequence"]
                    for candidate in candidates
                ) else "fail",
            },
            {
                "check_id": "implicit-generation-disabled",
                "status": "pass",
            },
            {
                "check_id": "structure-inputs-explicit",
                "status": "pass",
            },
        ],
    }
    human_actions = {
        "stage_id": CANDIDATE_STAGE_ID,
        "open_count": len(open_actions),
        "due_before_next_stage_count": len(due_actions),
        "actions": actions,
    }
    model_inputs = {
        "schema_version": 1,
        "stage_id": CANDIDATE_STAGE_ID,
        "models": {
            "ESMFold2": {
                "status": "exploratory_ready" if eligible else "blocked",
                "input_path": "structure_candidates.fasta",
                "candidate_ids": [candidate["candidate_id"] for candidate in eligible],
                "formal_release": bool(eligible) and not due_actions,
                "summary": "Canonical AA inputs are ready for exploratory structure inference; formal execution remains gated.",
                "summary_zh": "标准化 AA 输入已可用于探索性结构推理；正式运行仍受人工 gate 约束。",
            },
            "Evo2": {
                "status": "deferred",
                "input_path": None,
                "candidate_ids": [],
                "formal_release": False,
                "summary": "Evo2 is not a structure backend. Add it through a pinned sequence-evidence adapter after the candidate batch is frozen.",
                "summary_zh": "Evo2 不是结构折叠后端；候选批次冻结后再通过固定版本的序列证据 adapter 接入。",
            },
            "mRNABERT": {
                "status": "deferred",
                "input_path": None,
                "candidate_ids": [],
                "formal_release": False,
                "summary": "mRNABERT belongs to mRNA product design after the protein sequence and coding-product policy are fixed.",
                "summary_zh": "mRNABERT 属于后续 mRNA 产品设计；需先固定蛋白序列和编码产物策略。",
            },
        },
    }
    handoff = {
        "schema_version": 1,
        "run_id": run_id,
        "from_stage": CANDIDATE_STAGE_ID,
        "to_stage": NEXT_STAGE_ID,
        "readiness": readiness,
        "blocking_action_ids": [action["action_id"] for action in due_actions],
        "carried_human_actions": open_actions,
        "source_node_artifacts": {
            "summary": "summary.json",
            "input_audit": "input_audit.json",
            "process_record": "process_record.json",
            "output_audit": "output_audit.json",
            "human_actions": "human_actions.json",
            "report": "report.html",
        },
        "carried_forward": {
            "project_id": analysis.config.project_id,
            "source_run_id": analysis.source_run_id,
            "specification_id": analysis.specification.specification_id,
            "candidate_batch_sha256": None,
            "candidates": output_audit["candidates"],
            "exploratory_structure_candidate_ids": [
                candidate["candidate_id"] for candidate in eligible
            ],
        },
        "model_inputs": model_inputs["models"],
    }
    summary = {
        "schema_version": 1,
        "run_id": run_id,
        "stage_id": CANDIDATE_STAGE_ID,
        "stage_name": STAGE_BY_ID[CANDIDATE_STAGE_ID].name,
        "status": status,
        "computational_audit_status": analysis.computational_status,
        "next_stage": NEXT_STAGE_ID,
        "handoff_readiness": readiness,
        "candidate_count": len(candidates),
        "exploratory_structure_ready_count": len(eligible),
        "formal_structure_ready_count": len(release_ready),
        "errors": errors,
        "warnings": warnings,
        "open_human_actions": len(open_actions),
        "due_human_actions": len(due_actions),
    }
    candidate_batch = {
        "schema_version": 1,
        "project_id": analysis.config.project_id,
        "run_id": run_id,
        "stage_id": CANDIDATE_STAGE_ID,
        "source_run_id": analysis.source_run_id,
        "specification_id": analysis.specification.specification_id,
        "batch_label": analysis.specification.batch_label,
        "release_mode": analysis.specification.release_mode,
        "generation_grammar": analysis.specification.generation_grammar,
        "candidates": candidates,
    }
    return {
        "status": status,
        "summary": summary,
        "input_audit": input_audit,
        "process_record": process_record,
        "output_audit": output_audit,
        "human_actions": human_actions,
        "handoff": handoff,
        "model_inputs": model_inputs,
        "candidate_batch": candidate_batch,
    }


def _snapshot_candidate_inputs(
    analysis: CandidateBatchAnalysis,
    node_dir: Path,
    bundle: dict[str, Any],
) -> None:
    for input_name, source_path in analysis.input_paths.items():
        if input_name == "candidate_specification":
            snapshot_relative = "inputs/candidate_specification.json"
        else:
            _, candidate_key, input_type = input_name.split(":", maxsplit=2)
            suffix = "aa.fasta" if input_type == "amino_acid_fasta" else "cds.fasta"
            snapshot_relative = f"inputs/{candidate_key}.{suffix}"
        destination = node_dir / snapshot_relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, destination)
        bundle["input_audit"]["inputs"][input_name]["snapshot_path"] = snapshot_relative


def write_candidate_run(
    analysis: CandidateBatchAnalysis,
    *,
    now: datetime | None = None,
) -> Path:
    created = now or datetime.now(timezone.utc)
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    created = created.astimezone(timezone.utc)
    created_at = created.isoformat()
    run_id = (
        f"{created.strftime('%Y%m%dT%H%M%S%fZ')}-"
        f"stage2-{analysis.specification.sha256[:8]}"
    )
    run_dir = analysis.config.run_root / run_id
    node_dir = run_dir / "nodes" / CANDIDATE_STAGE_ID
    if run_dir.exists():
        raise ValueError(f"Refusing to overwrite candidate run: {run_dir}")
    try:
        (run_dir / "inputs").mkdir(parents=True)
        shutil.copytree(
            analysis.source_run_dir / "nodes" / SOURCE_STAGE_ID,
            run_dir / "nodes" / SOURCE_STAGE_ID,
        )
        for name in ("project.json", "proteins_aa.fasta", "proteins_cds.fasta"):
            shutil.copyfile(analysis.source_run_dir / "inputs" / name, run_dir / "inputs" / name)
        shutil.copyfile(
            analysis.source_run_dir / "manifest.json",
            run_dir / "inputs" / "source_run_manifest.json",
        )
        shutil.copyfile(
            analysis.source_run_dir / ARTIFACT_INDEX_FILENAME,
            run_dir / "inputs" / "source_run_artifact_index.json",
        )
        node_dir.mkdir(parents=True)
        bundle = build_candidate_node_bundle(analysis, run_id)
        bundle["summary"]["created_at_utc"] = created_at
        _snapshot_candidate_inputs(analysis, node_dir, bundle)

        _atomic_write(node_dir / "candidate_batch.json", _json_text(bundle["candidate_batch"]))
        candidate_batch_sha256 = sha256_file(node_dir / "candidate_batch.json")
        bundle["handoff"]["carried_forward"]["candidate_batch_sha256"] = candidate_batch_sha256
        _atomic_write(node_dir / "summary.json", _json_text(bundle["summary"]))
        _atomic_write(node_dir / "input_audit.json", _json_text(bundle["input_audit"]))
        _atomic_write(node_dir / "process_record.json", _json_text(bundle["process_record"]))
        _atomic_write(node_dir / "output_audit.json", _json_text(bundle["output_audit"]))
        _atomic_write(node_dir / "human_actions.json", _json_text(bundle["human_actions"]))
        _atomic_write(node_dir / "handoff.json", _json_text(bundle["handoff"]))
        _atomic_write(node_dir / "model_inputs.json", _json_text(bundle["model_inputs"]))
        candidate_fields = [
            "candidate_key", "candidate_id", "display_name", "candidate_type",
            "computational_status", "release_status", "annotation_status", "aa_length",
            "cds_length_nt", "translation_relation", "exploratory_structure_ready",
            "formal_structure_ready", "duplicate_of",
        ]
        _atomic_write(
            node_dir / "candidates.csv",
            _csv_text(candidate_fields, _candidate_csv_rows(analysis)),
        )
        component_fields = [
            "candidate_key", "candidate_id", "component_index", "component_type",
            "source_protein_id", "source_start", "source_end", "candidate_start",
            "candidate_end", "sequence", "sequence_sha256",
        ]
        _atomic_write(
            node_dir / "candidate_components.csv",
            _csv_text(component_fields, _component_rows(analysis)),
        )
        _atomic_write(
            node_dir / "findings.csv",
            _csv_text(["severity", "code", "message", "protein_id"], _finding_rows(analysis)),
        )
        eligible = [
            candidate
            for candidate in analysis.candidates
            if candidate.exploratory_structure_ready and candidate.duplicate_of is None
        ]
        fasta_text = "".join(
            f">{candidate.candidate_id} key={candidate.candidate_key} release={candidate.release_status}\n"
            f"{_wrap_fasta(candidate.amino_acid_sequence)}\n"
            for candidate in eligible
        )
        _atomic_write(node_dir / "structure_candidates.fasta", fasta_text)
        _atomic_write(
            node_dir / "report.html",
            render_candidate_report(analysis, bundle, run_id, created_at),
        )

        workflow = workflow_contract()
        workflow["contract_sha256"] = workflow_contract_sha256()
        workflow["run_id"] = run_id
        workflow["current_stage"] = CANDIDATE_STAGE_ID
        for stage in workflow["stages"]:
            if stage["stage_id"] == SOURCE_STAGE_ID:
                stage["status"] = analysis.source_summary["status"]
            elif stage["stage_id"] == CANDIDATE_STAGE_ID:
                stage["status"] = bundle["status"]
            else:
                stage["status"] = "not_evaluated"
        _atomic_write(run_dir / "workflow.json", _json_text(workflow))

        parent_index_sha256 = sha256_file(
            analysis.source_run_dir / ARTIFACT_INDEX_FILENAME
        )
        source_manifest = analysis.source_manifest
        source_node_relative = f"nodes/{SOURCE_STAGE_ID}"
        node_relative = f"nodes/{CANDIDATE_STAGE_ID}"
        manifest = {
            "schema_version": 1,
            "pipeline_version": __version__,
            "project_id": analysis.config.project_id,
            "run_id": run_id,
            "created_at_utc": created_at,
            "status": bundle["status"],
            "runtime_root": str(analysis.config.runtime_root),
            "current_stage": CANDIDATE_STAGE_ID,
            "lineage": {
                "parent_run_id": analysis.source_run_id,
                "parent_run_path": str(analysis.source_run_dir),
                "parent_artifact_index_sha256": parent_index_sha256,
            },
            "counts": {
                "source_proteins": len(analysis.source_proteins),
                **bundle["output_audit"]["summary"],
            },
            "context": source_manifest["context"],
            "inputs": {
                "source_snapshots": source_manifest["inputs"],
                "candidate_specification": bundle["input_audit"]["inputs"],
            },
            "nodes": {
                SOURCE_STAGE_ID: {
                    "status": analysis.source_summary["status"],
                    "origin_run_id": analysis.source_run_id,
                    "summary": f"{source_node_relative}/summary.json",
                    "report": f"{source_node_relative}/report.html",
                },
                CANDIDATE_STAGE_ID: {
                    "status": bundle["status"],
                    "summary": f"{node_relative}/summary.json",
                    "report": f"{node_relative}/report.html",
                },
            },
            "artifacts": {
                "workflow": "workflow.json",
                "current_node_root": node_relative,
                "handoff": f"{node_relative}/handoff.json",
                "candidate_batch": f"{node_relative}/candidate_batch.json",
                "structure_candidates": f"{node_relative}/structure_candidates.fasta",
                "source_inputs": "inputs",
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
                "Candidate run verification failed; latest was not updated: "
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
                    "current_stage": CANDIDATE_STAGE_ID,
                    "status": bundle["status"],
                    "summary_path": str(node_dir / "summary.json"),
                    "report_path": str(node_dir / "report.html"),
                    "candidate_batch_path": str(node_dir / "candidate_batch.json"),
                    "structure_input_path": str(node_dir / "structure_candidates.fasta"),
                    "artifact_index_path": str(run_dir / ARTIFACT_INDEX_FILENAME),
                    "artifact_index_sha256": sha256_file(run_dir / ARTIFACT_INDEX_FILENAME),
                    "verification_status": verification["status"],
                }
            ),
        )
        return run_dir
    except Exception:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise
