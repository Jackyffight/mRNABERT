"""Write the immutable Stage 7 integrated-ranking continuation run."""

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
from .ranking import RankingAnalysis
from .ranking_html import render_ranking_report
from .ranking_specs import RANKING_STAGE_ID
from .verification import ARTIFACT_INDEX_FILENAME, build_artifact_index, sha256_file, verify_run
from .workflow import STAGE_BY_ID, workflow_contract, workflow_contract_sha256


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


def _parent_actions(analysis: RankingAnalysis) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for stage_id in ("protein_product_design", "mrna_product_design"):
        handoff = json.loads(
            (analysis.source_run_dir / "nodes" / stage_id / "handoff.json").read_text(
                encoding="utf-8"
            )
        )
        for action in handoff.get("carried_human_actions", []):
            merged[action["action_id"]] = dict(action)
    return list(merged.values())


def _actions(analysis: RankingAnalysis) -> list[dict[str, Any]]:
    merged = {action["action_id"]: action for action in _parent_actions(analysis)}
    for requirement in analysis.result["requirements"]:
        merged.setdefault(
            requirement["requirement_id"],
            {
                "action_id": requirement["requirement_id"],
                "question": requirement["description"],
                "question_zh": "补充或批准该排序输入；完成前仅允许临时技术排序。",
                "required_before_stage": "experiment_release",
                "status": "open",
                "owner": "unassigned",
                "resolution": "",
                "resolution_zh": "",
            },
        )
    merged.setdefault(
        "human-signoff-provisional-portfolio",
        {
            "action_id": "human-signoff-provisional-portfolio",
            "question": "Review rank stability, exclusions, controls, and modality portfolios before experiment release.",
            "question_zh": "在实验放行前，复核排名稳定性、排除原因、对照组成和各模态候选组合。",
            "required_before_stage": "experiment_release",
            "status": "open",
            "owner": "unassigned",
            "resolution": "",
            "resolution_zh": "",
        },
    )
    return list(merged.values())


def write_ranking_run(
    analysis: RankingAnalysis,
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
            sha256_file(analysis.ranking_specification_path)
            + analysis.source_manifest["run_id"]
        ).encode("ascii")
    ).hexdigest()
    run_id = f"{created.strftime('%Y%m%dT%H%M%S%fZ')}-stage7-{identity[:8]}"
    run_dir = analysis.config.run_root / run_id
    node = run_dir / "nodes" / RANKING_STAGE_ID
    if run_dir.exists():
        raise ValueError(f"Refusing to overwrite Stage 7 run: {run_dir}")
    try:
        shutil.copytree(analysis.source_run_dir / "nodes", run_dir / "nodes")
        shutil.copytree(analysis.source_run_dir / "inputs", run_dir / "inputs")
        lineage = run_dir / "inputs/lineage"
        lineage.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(
            analysis.source_run_dir / "manifest.json",
            lineage / "stage6_parent_manifest.json",
        )
        shutil.copyfile(
            analysis.source_run_dir / ARTIFACT_INDEX_FILENAME,
            lineage / "stage6_parent_artifact_index.json",
        )
        node.mkdir(parents=True, exist_ok=True)
        spec_snapshot = node / "inputs/ranking_specification.json"
        spec_snapshot.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(analysis.ranking_specification_path, spec_snapshot)
        actions = _actions(analysis)
        open_actions = [action for action in actions if action["status"] == "open"]
        summary = {
            "schema_version": 1,
            "run_id": run_id,
            "created_at_utc": created_at,
            "stage_id": RANKING_STAGE_ID,
            "stage_name": STAGE_BY_ID[RANKING_STAGE_ID].name,
            "status": "needs_data" if analysis.result["requirements"] else "needs_human_input",
            "computational_audit_status": "pass",
            "mode": "exploratory",
            "ruleset_id": analysis.result["ruleset_id"],
            "ranking_row_count": len(analysis.result["rankings"]),
            "eligible_row_count": sum(row["eligible"] for row in analysis.result["rankings"]),
            "provisional_selection_count": sum(
                len(items) for items in analysis.result["provisional_portfolios"].values()
            ),
            "formal_selection_count": 0,
            "missing_requirement_count": len(analysis.result["requirements"]),
            "open_human_actions": len(open_actions),
        }
        input_audit = {
            "stage_id": RANKING_STAGE_ID,
            "status": "pass",
            "source_run_id": analysis.source_manifest["run_id"],
            "inputs": {
                "ranking_specification": {
                    "source_path": str(analysis.ranking_specification_path),
                    "snapshot_path": "inputs/ranking_specification.json",
                    "sha256": sha256_file(analysis.ranking_specification_path),
                }
            },
            "checks": [
                {"check_id": "stage6-parent-verified", "status": "pass"},
                {"check_id": "all-candidate-hashes-bound", "status": "pass"},
                {"check_id": "hard-gates-before-ranking", "status": "pass"},
                {"check_id": "missing-values-coverage-penalized", "status": "pass"},
                {"check_id": "formal-release-disabled", "status": "pass"},
            ],
        }
        process_record = {
            "stage_id": RANKING_STAGE_ID,
            "pipeline_version": __version__,
            "ruleset_id": analysis.result["ruleset_id"],
            "operations": [
                "join_stage3_to_stage6_evidence_by_candidate_id",
                "normalize_each_declared_feature_across_candidate_set",
                "apply_hard_gates_and_required_feature_checks",
                "compute_weighted_scores_with_coverage_penalty",
                "select_control_aware_sequence_diverse_provisional_portfolios",
                "perturb_positive_weights_and_record_rank_spans",
            ],
        }
        output_audit = {
            "stage_id": RANKING_STAGE_ID,
            "status": "pass",
            "summary": summary,
            "requirements": analysis.result["requirements"],
            "checks": [
                {"check_id": "component-contributions-retained", "status": "pass"},
                {"check_id": "excluded-candidates-retained", "status": "pass"},
                {"check_id": "sensitivity-recorded", "status": "pass"},
                {"check_id": "no-efficacy-or-release-claim", "status": "pass"},
            ],
        }
        human_actions = {
            "stage_id": RANKING_STAGE_ID,
            "open_count": len(open_actions),
            "actions": actions,
        }
        handoff = {
            "schema_version": 1,
            "run_id": run_id,
            "from_stage": RANKING_STAGE_ID,
            "to_stage": "experiment_release",
            "readiness": "needs_data" if analysis.result["requirements"] else "needs_human_input",
            "formal_readiness": "not_released",
            "blocking_action_ids": [action["action_id"] for action in open_actions],
            "carried_human_actions": open_actions,
            "carried_forward": {"ranking_result_sha256": None},
            "limitations": analysis.result["limitations"],
        }
        _atomic_write(node / "ranking_result.json", _json_text(analysis.result))
        _atomic_write(node / "summary.json", _json_text(summary))
        _atomic_write(node / "input_audit.json", _json_text(input_audit))
        _atomic_write(node / "process_record.json", _json_text(process_record))
        _atomic_write(node / "output_audit.json", _json_text(output_audit))
        _atomic_write(node / "human_actions.json", _json_text(human_actions))
        _atomic_write(
            node / "rankings.csv",
            _csv_text(
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
                    for row in analysis.result["rankings"]
                ],
            ),
        )
        _atomic_write(
            node / "provisional_portfolios.csv",
            _csv_text(
                ["modality", "candidate_id", "candidate_key", "rank", "score", "selection_reason"],
                [
                    {"modality": modality, **item}
                    for modality, items in analysis.result["provisional_portfolios"].items()
                    for item in items
                ],
            ),
        )
        _atomic_write(
            node / "sensitivity.csv",
            _csv_text(
                ["modality", "candidate_id", "minimum_rank", "maximum_rank", "rank_span", "scenario_count"],
                [
                    {"modality": modality, **item}
                    for modality, items in analysis.result["sensitivity"].items()
                    for item in items
                ],
            ),
        )
        _atomic_write(
            node / "report.html",
            render_ranking_report(analysis.result, actions, run_id, created_at),
        )
        handoff["carried_forward"]["ranking_result_sha256"] = sha256_file(
            node / "ranking_result.json"
        )
        _atomic_write(node / "handoff.json", _json_text(handoff))

        workflow = workflow_contract()
        workflow["contract_sha256"] = workflow_contract_sha256()
        workflow["run_id"] = run_id
        workflow["current_stage"] = RANKING_STAGE_ID
        parent_workflow = json.loads(
            (analysis.source_run_dir / "workflow.json").read_text(encoding="utf-8")
        )
        parent_status = {stage["stage_id"]: stage["status"] for stage in parent_workflow["stages"]}
        for stage in workflow["stages"]:
            stage["status"] = (
                summary["status"]
                if stage["stage_id"] == RANKING_STAGE_ID
                else parent_status.get(stage["stage_id"], "not_evaluated")
            )
        _atomic_write(run_dir / "workflow.json", _json_text(workflow))
        nodes = copy.deepcopy(analysis.source_manifest["nodes"])
        nodes[RANKING_STAGE_ID] = {
            "status": summary["status"],
            "summary": f"nodes/{RANKING_STAGE_ID}/summary.json",
            "report": f"nodes/{RANKING_STAGE_ID}/report.html",
        }
        parent_index_sha = sha256_file(analysis.source_run_dir / ARTIFACT_INDEX_FILENAME)
        manifest = {
            "schema_version": 1,
            "pipeline_version": __version__,
            "project_id": analysis.config.project_id,
            "run_id": run_id,
            "created_at_utc": created_at,
            "status": summary["status"],
            "runtime_root": str(analysis.config.runtime_root),
            "current_stage": RANKING_STAGE_ID,
            "executed_stages": [RANKING_STAGE_ID],
            "lineage": {
                "parent_run_id": analysis.source_manifest["run_id"],
                "parent_run_path": str(analysis.source_run_dir),
                "parent_artifact_index_sha256": parent_index_sha,
            },
            "context": analysis.source_manifest["context"],
            "counts": {
                **analysis.source_manifest["counts"],
                "ranking_rows": len(analysis.result["rankings"]),
                "ranking_eligible_rows": sum(row["eligible"] for row in analysis.result["rankings"]),
                "provisional_portfolio_items": sum(
                    len(items) for items in analysis.result["provisional_portfolios"].values()
                ),
                "ranking_missing_requirements": len(analysis.result["requirements"]),
            },
            "inputs": {
                "parent_run_id": analysis.source_manifest["run_id"],
                "ranking_specification_sha256": sha256_file(analysis.ranking_specification_path),
            },
            "nodes": nodes,
            "artifacts": {
                "workflow": "workflow.json",
                "ranking_handoff": f"nodes/{RANKING_STAGE_ID}/handoff.json",
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
                "Stage 7 run verification failed; latest was not updated: "
                + "; ".join(verification["errors"][:5])
            )
        _atomic_write(
            analysis.config.run_root / "latest.json",
            _json_text({
                "schema_version": 1,
                "project_id": analysis.config.project_id,
                "run_id": run_id,
                "run_path": str(run_dir),
                "current_stage": RANKING_STAGE_ID,
                "executed_stages": [RANKING_STAGE_ID],
                "status": summary["status"],
                "report_path": str(node / "report.html"),
                "artifact_index_path": str(run_dir / ARTIFACT_INDEX_FILENAME),
                "artifact_index_sha256": sha256_file(run_dir / ARTIFACT_INDEX_FILENAME),
                "verification_status": "pass",
            }),
        )
        return run_dir
    except Exception:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise
