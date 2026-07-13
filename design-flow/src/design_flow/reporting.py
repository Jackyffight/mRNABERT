"""Immutable run artifacts and human-readable sequence-audit reports."""

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


FUTURE_STAGES = (
    "candidate_generation",
    "structure_prediction",
    "developability_assessment",
    "nucleotide_design",
    "candidate_ranking",
    "experiment_design",
    "assay_ingestion",
    "learning",
)


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
        row.update(
            {
                metric_name: protein.metrics.get(metric_name)
                for metric_name in metric_names
            }
        )
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


def _markdown_report(analysis: ProjectAnalysis, run_id: str, created_at: str) -> str:
    counts = _issue_counts(analysis.all_issues)
    lines = [
        f"# Sequence Audit: {analysis.config.project_id}",
        "",
        "## Summary",
        "",
        f"- Run: `{run_id}`",
        f"- Created (UTC): `{created_at}`",
        f"- Status: **{analysis.status.upper()}**",
        f"- Paired proteins analyzed: `{len(analysis.proteins)}`",
        f"- QC errors: `{counts['errors']}`",
        f"- QC warnings: `{counts['warnings']}`",
        "",
        "> This milestone checks sequence integrity and descriptive sequence properties only. "
        "It does not establish antigenicity, safety, expression, folding, or vaccine efficacy.",
        "",
        "## Inputs",
        "",
        f"- Amino-acid FASTA: `{analysis.config.amino_acid_fasta}`",
        f"- CDS FASTA: `{analysis.config.nucleotide_fasta}`",
        f"- Protein expression host: `{analysis.config.protein_expression_host}`",
        f"- mRNA target species: `{analysis.config.mrna_target_species}`",
        "",
        "## Protein Metrics",
        "",
        "| Protein | Status | AA | CDS nt | Translation | GC | Molecular weight (Da) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for protein in analysis.proteins:
        metrics = protein.metrics
        translation = metrics.get("translation_matches")
        translation_text = "n/a" if translation is None else ("match" if translation else "mismatch")
        lines.append(
            "| {protein} | {status} | {aa} | {cds} | {translation} | {gc:.4f} | {mass:.1f} |".format(
                protein=protein.protein_id,
                status=protein.status,
                aa=metrics.get("aa_length", 0),
                cds=metrics.get("cds_length_nt", 0),
                translation=translation_text,
                gc=float(metrics.get("gc_fraction", 0.0)),
                mass=float(metrics.get("estimated_molecular_weight_da", 0.0)),
            )
        )

    lines.extend(["", "## QC Findings", ""])
    if analysis.all_issues:
        lines.extend(
            f"- **{issue.severity.upper()}** `{issue.code}`"
            f"{f' [{issue.protein_id}]' if issue.protein_id else ''}: {issue.message}"
            for issue in analysis.all_issues
        )
    else:
        lines.append("No QC findings.")

    lines.extend(
        [
            "",
            "## Stage Coverage",
            "",
            "- `sequence_audit`: complete",
            *[f"- `{stage}`: not evaluated" for stage in FUTURE_STAGES],
            "",
            "Detailed machine-readable records are in `proteins.json`, `proteins.csv`, "
            "`qc_issues.csv`, and `manifest.json`.",
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
    run_dir.mkdir(parents=True, exist_ok=False)

    proteins_document = {
        "schema_version": 1,
        "project_id": analysis.config.project_id,
        "run_id": run_id,
        "proteins": [protein.to_dict() for protein in analysis.proteins],
    }
    fieldnames, protein_rows = _protein_rows(analysis)
    issue_rows = _issue_rows(analysis)
    issue_counts = _issue_counts(analysis.all_issues)
    manifest = {
        "schema_version": 1,
        "pipeline_version": __version__,
        "project_id": analysis.config.project_id,
        "run_id": run_id,
        "created_at_utc": created_at,
        "status": analysis.status,
        "counts": {
            "expected_proteins": analysis.config.expected_protein_count,
            "paired_proteins": len(analysis.proteins),
            **issue_counts,
        },
        "context": {
            "protein_expression_host": analysis.config.protein_expression_host,
            "mrna_target_species": analysis.config.mrna_target_species,
        },
        "inputs": {
            "project_config": {
                "path": str(analysis.config.config_path),
                "sha256": analysis.input_digests["project_config"],
            },
            "amino_acid_fasta": {
                "path": str(analysis.config.amino_acid_fasta),
                "sha256": analysis.input_digests["amino_acid_fasta"],
            },
            "nucleotide_fasta": {
                "path": str(analysis.config.nucleotide_fasta),
                "sha256": analysis.input_digests["nucleotide_fasta"],
            },
        },
        "stages": {
            "sequence_audit": analysis.status,
            **{stage: "not_evaluated" for stage in FUTURE_STAGES},
        },
        "artifacts": {
            "proteins_json": "proteins.json",
            "proteins_csv": "proteins.csv",
            "qc_issues_csv": "qc_issues.csv",
            "report": "report.md",
        },
    }

    _atomic_write(run_dir / "proteins.json", _json_text(proteins_document))
    _atomic_write(run_dir / "proteins.csv", _csv_text(fieldnames, protein_rows))
    _atomic_write(
        run_dir / "qc_issues.csv",
        _csv_text(["scope", "protein_id", "severity", "code", "message"], issue_rows),
    )
    _atomic_write(run_dir / "report.md", _markdown_report(analysis, run_id, created_at))
    _atomic_write(run_dir / "manifest.json", _json_text(manifest))
    _atomic_write(
        analysis.config.run_root / "latest.json",
        _json_text(
            {
                "schema_version": 1,
                "project_id": analysis.config.project_id,
                "run_id": run_id,
                "run_path": str(run_dir),
                "status": analysis.status,
            }
        ),
    )
    return run_dir
