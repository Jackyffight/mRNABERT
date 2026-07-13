"""Immutable run, workflow, and per-node artifacts."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from . import __version__
from .domain import ProjectAnalysis, QCIssue
from .node_record import build_node_bundle, build_workflow_snapshot
from .workflow import CURRENT_STAGE_ID, STAGE_BY_ID


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


def _issue_counts(issues: list[QCIssue]) -> dict[str, int]:
    return {
        "errors": sum(issue.severity == "error" for issue in issues),
        "warnings": sum(issue.severity == "warning" for issue in issues),
    }


def _protein_rows(analysis: ProjectAnalysis) -> tuple[list[str], list[dict[str, Any]]]:
    metric_names = sorted(
        {
            metric_name
            for protein in analysis.proteins
            for metric_name, value in protein.metrics.items()
            if not isinstance(value, (dict, list))
        }
    )
    fieldnames = [
        "protein_id",
        "candidate_id",
        "status",
        "error_count",
        "warning_count",
        *metric_names,
        "amino_acid_composition",
    ]
    rows: list[dict[str, Any]] = []
    for protein in analysis.proteins:
        counts = _issue_counts(protein.issues)
        row: dict[str, Any] = {
            "protein_id": protein.protein_id,
            "candidate_id": protein.candidate_id,
            "status": protein.status,
            "error_count": counts["errors"],
            "warning_count": counts["warnings"],
            "amino_acid_composition": json.dumps(
                protein.metrics.get("amino_acid_composition", {}),
                sort_keys=True,
                separators=(",", ":"),
            ),
        }
        row.update({metric_name: protein.metrics.get(metric_name) for metric_name in metric_names})
        rows.append(row)
    return fieldnames, rows


def _issue_rows(analysis: ProjectAnalysis) -> list[dict[str, str]]:
    return [
        {
            "scope": "protein" if issue.protein_id else "project",
            "protein_id": issue.protein_id or "",
            "severity": issue.severity,
            "code": issue.code,
            "message": issue.message,
        }
        for issue in analysis.all_issues
    ]


def _markdown_report(
    analysis: ProjectAnalysis,
    bundle: dict[str, Any],
    run_id: str,
    created_at: str,
) -> str:
    stage = STAGE_BY_ID[CURRENT_STAGE_ID]
    summary = bundle["summary"]
    input_audit = bundle["input_audit"]
    process_record = bundle["process_record"]
    output_audit = bundle["output_audit"]
    action_document = bundle["human_actions"]
    handoff = bundle["handoff"]
    lines = [
        f"# Node Report: {stage.name}",
        "",
        "## Summary",
        "",
        f"- Project: `{analysis.config.project_id}`",
        f"- Run: `{run_id}`",
        f"- Created (UTC): `{created_at}`",
        f"- Node status: **{summary['status'].upper()}**",
        f"- Computational audit: **{summary['computational_audit_status'].upper()}**",
        f"- Handoff readiness: **{summary['handoff_readiness'].upper()}**",
        f"- Accepted source candidates: `{summary['accepted_candidates']}`",
        f"- Open human actions: `{summary['open_human_actions']}`",
        "",
        "> This node verifies source identity and sequence integrity. It does not establish "
        "antigenicity, safety, expression, folding, or vaccine efficacy.",
        "",
        "## Node Scope",
        "",
        stage.purpose,
        "",
        "Capabilities exercised or reserved by this node:",
        "",
        *[f"- {capability}" for capability in stage.capabilities],
        "",
        "## Input Audit",
        "",
        "| Check | Status | Evidence |",
        "|---|---:|---|",
    ]
    for check in input_audit["checks"]:
        lines.append(
            f"| `{check['check_id']}` | {check['status']} | {check['evidence']} |"
        )
    lines.extend(
        [
            "",
            "Input identities:",
            "",
            *[
                f"- `{name}`: `{record['path']}` (`{record['sha256']}`)"
                for name, record in input_audit["inputs"].items()
            ],
            "",
            "## Process Record",
            "",
            "| Operation | Recorded behavior |",
            "|---|---|",
        ]
    )
    for operation in process_record["operations"]:
        lines.append(f"| `{operation['operation']}` | {operation['behavior']} |")

    lines.extend(
        [
            "",
            "## Output Audit",
            "",
            "| Protein | Candidate ID | Status | AA | CDS nt | Translation | GC | Molecular weight (Da) |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for protein in analysis.proteins:
        metrics = protein.metrics
        translation = metrics.get("translation_matches")
        translation_text = "n/a" if translation is None else ("match" if translation else "mismatch")
        lines.append(
            "| {protein} | `{candidate}` | {status} | {aa} | {cds} | {translation} | {gc:.4f} | {mass:.1f} |".format(
                protein=protein.protein_id,
                candidate=protein.candidate_id,
                status=protein.status,
                aa=metrics.get("aa_length", 0),
                cds=metrics.get("cds_length_nt", 0),
                translation=translation_text,
                gc=float(metrics.get("gc_fraction", 0.0)),
                mass=float(metrics.get("estimated_molecular_weight_da", 0.0)),
            )
        )
    lines.extend(["", "Output checks:", ""])
    lines.extend(
        f"- `{check['check_id']}`: **{check['status'].upper()}**"
        for check in output_audit["checks"]
    )
    lines.extend(["", "QC findings:", ""])
    if analysis.all_issues:
        lines.extend(
            f"- **{issue.severity.upper()}** `{issue.code}`"
            f"{f' [{issue.protein_id}]' if issue.protein_id else ''}: {issue.message}"
            for issue in analysis.all_issues
        )
    else:
        lines.append("- No sequence QC findings.")

    lines.extend(
        [
            "",
            "## Human Intervention",
            "",
            "| Action | Status | Owner | Required before | Question or decision |",
            "|---|---:|---|---|---|",
        ]
    )
    for action in action_document["actions"]:
        lines.append(
            f"| `{action['action_id']}` | {action['status']} | {action['owner']} | "
            f"`{action['required_before_stage']}` | {action['question']} |"
        )
        if action["resolution"]:
            lines.append(f"|  | resolution |  |  | {action['resolution']} |")

    blocking = ", ".join(f"`{action_id}`" for action_id in handoff["blocking_action_ids"])
    lines.extend(
        [
            "",
            "## Handoff",
            "",
            f"- Next node: `{handoff['to_stage']}`",
            f"- Readiness: **{handoff['readiness'].upper()}**",
            f"- Blocking human actions: {blocking or 'none'}",
            f"- Carried source candidates: `{len(handoff['carried_forward']['candidates'])}`",
            "",
            "The machine-readable handoff carries candidate IDs, input hashes, QC findings, and every "
            "unresolved human action into the next node.",
            "",
            "## Artifacts",
            "",
            "- Node card: `summary.json`",
            "- Input audit: `input_audit.json`",
            "- Process record: `process_record.json`",
            "- Output audit: `output_audit.json`",
            "- Human actions: `human_actions.json`",
            "- Next-node payload: `handoff.json`",
            "- Sequence details: `proteins.json`, `proteins.csv`, `qc_issues.csv`",
            "",
        ]
    )
    return "\n".join(lines)


def write_run_artifacts(
    analysis: ProjectAnalysis,
    *,
    now: datetime | None = None,
) -> Path:
    created = now or datetime.now(timezone.utc)
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    created = created.astimezone(timezone.utc)
    created_at = created.isoformat()
    digest_prefix = analysis.input_digests["amino_acid_fasta"][:8]
    run_id = f"{created.strftime('%Y%m%dT%H%M%S%fZ')}-{digest_prefix}"
    run_dir = analysis.config.run_root / run_id
    node_dir = run_dir / "nodes" / CURRENT_STAGE_ID
    node_dir.mkdir(parents=True, exist_ok=False)

    bundle = build_node_bundle(analysis, run_id)
    bundle["summary"]["created_at_utc"] = created_at
    workflow = build_workflow_snapshot(bundle["status"])
    workflow["run_id"] = run_id
    proteins_document = {
        "schema_version": 1,
        "project_id": analysis.config.project_id,
        "run_id": run_id,
        "stage_id": CURRENT_STAGE_ID,
        "proteins": [protein.to_dict() for protein in analysis.proteins],
    }
    fieldnames, protein_rows = _protein_rows(analysis)
    issue_rows = _issue_rows(analysis)
    issue_counts = _issue_counts(analysis.all_issues)
    node_relative = f"nodes/{CURRENT_STAGE_ID}"
    manifest = {
        "schema_version": 1,
        "pipeline_version": __version__,
        "project_id": analysis.config.project_id,
        "run_id": run_id,
        "created_at_utc": created_at,
        "status": bundle["status"],
        "runtime_root": str(analysis.config.runtime_root),
        "current_stage": CURRENT_STAGE_ID,
        "counts": {
            "expected_proteins": analysis.config.expected_protein_count,
            "paired_proteins": len(analysis.proteins),
            **issue_counts,
        },
        "context": {
            "target_indication": analysis.config.target_indication,
            "intended_host_species": analysis.config.intended_host_species,
            "product_modalities": list(analysis.config.product_modalities),
            "protein_expression_host": analysis.config.protein_expression_host,
            "mrna_target_species": analysis.config.mrna_target_species,
        },
        "inputs": bundle["input_audit"]["inputs"],
        "nodes": {
            CURRENT_STAGE_ID: {
                "status": bundle["status"],
                "summary": f"{node_relative}/summary.json",
                "report": f"{node_relative}/report.md",
            }
        },
        "artifacts": {
            "workflow": "workflow.json",
            "node_root": node_relative,
            "handoff": f"{node_relative}/handoff.json",
        },
    }

    _atomic_write(run_dir / "workflow.json", _json_text(workflow))
    _atomic_write(node_dir / "summary.json", _json_text(bundle["summary"]))
    _atomic_write(node_dir / "input_audit.json", _json_text(bundle["input_audit"]))
    _atomic_write(node_dir / "process_record.json", _json_text(bundle["process_record"]))
    _atomic_write(node_dir / "output_audit.json", _json_text(bundle["output_audit"]))
    _atomic_write(node_dir / "human_actions.json", _json_text(bundle["human_actions"]))
    _atomic_write(node_dir / "handoff.json", _json_text(bundle["handoff"]))
    _atomic_write(node_dir / "proteins.json", _json_text(proteins_document))
    _atomic_write(node_dir / "proteins.csv", _csv_text(fieldnames, protein_rows))
    _atomic_write(
        node_dir / "qc_issues.csv",
        _csv_text(["scope", "protein_id", "severity", "code", "message"], issue_rows),
    )
    _atomic_write(
        node_dir / "report.md",
        _markdown_report(analysis, bundle, run_id, created_at),
    )
    _atomic_write(run_dir / "manifest.json", _json_text(manifest))
    _atomic_write(
        analysis.config.run_root / "latest.json",
        _json_text(
            {
                "schema_version": 1,
                "project_id": analysis.config.project_id,
                "run_id": run_id,
                "run_path": str(run_dir),
                "current_stage": CURRENT_STAGE_ID,
                "status": bundle["status"],
                "summary_path": str(node_dir / "summary.json"),
                "report_path": str(node_dir / "report.md"),
            }
        ),
    )
    return run_dir
