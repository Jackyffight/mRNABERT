"""Command-line entry point for the design-flow sequence audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

from . import __version__
from .candidate_reporting import write_candidate_run
from .candidate_specification import (
    CANDIDATE_STAGE_ID,
    CandidateBatchAnalysis,
    analyze_candidate_specification,
)
from .domain import ProjectAnalysis
from .pipeline import analyze_project
from .reporting import write_run_artifacts
from .verification import verify_run
from .workflow import CURRENT_STAGE_ID


def _project_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
        raise argparse.ArgumentTypeError("use only letters, numbers, '.', '_' and '-'")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vaxflow",
        description="Traceable vaccine construct design workflow",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init",
        help="create a source project with an external runtime",
    )
    init_parser.add_argument("project_dir", type=Path)
    init_parser.add_argument("--runtime-root", type=Path, required=True)
    init_parser.add_argument("--project-id", type=_project_id, default="three-protein-vaccine")
    init_parser.add_argument("--expected-count", type=int, default=3)

    validate_parser = subparsers.add_parser("validate", help="audit inputs without writing a run")
    validate_parser.add_argument("project_config", type=Path)

    run_parser = subparsers.add_parser("run", help="audit inputs and write immutable run artifacts")
    run_parser.add_argument("project_config", type=Path)
    for command, help_text in (
        ("validate-stage2", "validate candidate specification without writing a continuation run"),
        ("run-stage2", "write an immutable candidate-specification continuation run"),
    ):
        stage2_parser = subparsers.add_parser(command, help=help_text)
        stage2_parser.add_argument("project_config", type=Path)
        stage2_parser.add_argument(
            "--from-run",
            type=Path,
            help="verified stage-1 run; defaults to the project's latest run",
        )
        stage2_parser.add_argument(
            "--specification",
            type=Path,
            help="candidate specification JSON; defaults to the project input",
        )
    verify_parser = subparsers.add_parser(
        "verify-run",
        help="verify hashes and cross-file consistency for an immutable run",
    )
    verify_parser.add_argument("run_dir", type=Path)
    return parser


def _placeholder_records(count: int, sequence_type: str) -> str:
    if sequence_type == "aa":
        placeholder = "REPLACE_WITH_AMINO_ACID_SEQUENCE"
    else:
        placeholder = "REPLACE_WITH_CODING_DNA_SEQUENCE"
    return "".join(f">protein_{index}\n{placeholder}\n" for index in range(1, count + 1))


def _init_project(
    project_dir: Path,
    runtime_root: Path,
    project_id: str,
    expected_count: int,
) -> int:
    if expected_count < 1:
        raise ValueError("--expected-count must be a positive integer")
    project_dir = project_dir.resolve()
    if not runtime_root.is_absolute():
        raise ValueError("--runtime-root must be an absolute path")
    runtime_root = runtime_root.resolve()
    if runtime_root == project_dir or runtime_root.is_relative_to(project_dir):
        raise ValueError("--runtime-root must be outside project_dir")
    config_path = project_dir / "project.json"
    amino_acid_path = runtime_root / "input" / "proteins_aa.fasta"
    nucleotide_path = runtime_root / "input" / "proteins_cds.fasta"
    existing = [path for path in (config_path, amino_acid_path, nucleotide_path) if path.exists()]
    if existing:
        raise ValueError(f"Refusing to overwrite existing project file: {existing[0]}")

    amino_acid_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config = {
        "schema_version": 1,
        "project_id": project_id,
        "expected_protein_count": expected_count,
        "runtime_root": str(runtime_root),
        "inputs": {
            "amino_acid_fasta": "input/proteins_aa.fasta",
            "nucleotide_fasta": "input/proteins_cds.fasta",
        },
        "outputs": {"run_root": "runs"},
        "context": {
            "target_indication": "unspecified",
            "intended_host_species": "unspecified",
            "product_modalities": [],
            "protein_expression_host": "unspecified",
            "mrna_target_species": "unspecified",
        },
    }
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    amino_acid_path.write_text(_placeholder_records(expected_count, "aa"), encoding="utf-8")
    nucleotide_path.write_text(_placeholder_records(expected_count, "cds"), encoding="utf-8")
    print(f"Created project: {config_path}")
    print(f"Runtime root: {runtime_root}")
    print(f"Replace placeholder sequences in: {amino_acid_path}")
    print(f"Replace placeholder sequences in: {nucleotide_path}")
    return 0


def _print_analysis(analysis: ProjectAnalysis) -> None:
    errors = sum(issue.severity == "error" for issue in analysis.all_issues)
    warnings = sum(issue.severity == "warning" for issue in analysis.all_issues)
    print(
        f"Project {analysis.config.project_id}: status={analysis.status} "
        f"proteins={len(analysis.proteins)} errors={errors} warnings={warnings}"
    )
    for protein in analysis.proteins:
        metrics = protein.metrics
        print(
            f"  {protein.protein_id}: status={protein.status} "
            f"aa={metrics['aa_length']} cds_nt={metrics['cds_length_nt']} "
            f"translation_matches={metrics['translation_matches']}"
        )
    for issue in analysis.all_issues:
        scope = f"[{issue.protein_id}] " if issue.protein_id else ""
        print(f"  {issue.severity.upper()} {issue.code}: {scope}{issue.message}")


def _print_candidate_analysis(analysis: CandidateBatchAnalysis) -> None:
    errors = sum(issue.severity == "error" for issue in analysis.all_issues)
    warnings = sum(issue.severity == "warning" for issue in analysis.all_issues)
    ready = sum(
        candidate.exploratory_structure_ready and candidate.duplicate_of is None
        for candidate in analysis.candidates
    )
    print(
        f"Project {analysis.config.project_id}: stage={CANDIDATE_STAGE_ID} "
        f"status={analysis.computational_status} candidates={len(analysis.candidates)} "
        f"structure_ready={ready} errors={errors} warnings={warnings}"
    )
    print(
        f"  source_run={analysis.source_run_id} "
        f"source_handoff={analysis.source_handoff.get('readiness')} "
        f"specification={analysis.specification.specification_id}"
    )
    for candidate in analysis.candidates:
        components = ",".join(
            (
                f"{component['source_protein_id']}:{component['source_start']}-{component['source_end']}"
                if component["component_type"] == "source_segment"
                else f"addition:{component['sequence']}"
            )
            for component in candidate.inferred_components
        )
        print(
            f"  {candidate.candidate_key}: compute={candidate.computational_status} "
            f"release={candidate.release_status} aa={len(candidate.amino_acid_sequence)} "
            f"translation={candidate.translation_relation['relation']} components={components}"
        )
    for issue in analysis.all_issues:
        scope = f"[{issue.protein_id}] " if issue.protein_id else ""
        print(f"  {issue.severity.upper()} {issue.code}: {scope}{issue.message}")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            return _init_project(
                args.project_dir,
                args.runtime_root,
                args.project_id,
                args.expected_count,
            )

        if args.command == "verify-run":
            result = verify_run(args.run_dir)
            print(
                f"Run {result['run_id']}: status={result['status']} "
                f"checks={len(result['checks'])} errors={len(result['errors'])} "
                f"warnings={len(result['warnings'])}"
            )
            for error in result["errors"]:
                print(f"  ERROR {error}")
            for warning in result["warnings"]:
                print(f"  WARNING {warning}")
            return 0 if result["status"] == "pass" else 2

        if args.command in {"validate-stage2", "run-stage2"}:
            candidate_analysis = analyze_candidate_specification(
                args.project_config,
                source_run_dir=args.from_run,
                specification_path=args.specification,
            )
            _print_candidate_analysis(candidate_analysis)
            if args.command == "run-stage2":
                run_dir = write_candidate_run(candidate_analysis)
                node_dir = run_dir / "nodes" / CANDIDATE_STAGE_ID
                print(f"Run artifacts: {run_dir}")
                print(f"Node summary: {node_dir / 'summary.json'}")
                print(f"Node report: {node_dir / 'report.html'}")
                print(f"ESMFold2 input: {node_dir / 'structure_candidates.fasta'}")
            return 0 if candidate_analysis.computational_status == "pass" else 2

        analysis = analyze_project(args.project_config)
        _print_analysis(analysis)
        if args.command == "run":
            run_dir = write_run_artifacts(analysis)
            print(f"Run artifacts: {run_dir}")
            print(f"Node summary: {run_dir / 'nodes' / CURRENT_STAGE_ID / 'summary.json'}")
            print(f"Node report: {run_dir / 'nodes' / CURRENT_STAGE_ID / 'report.html'}")
        return 0 if analysis.status == "pass" else 2
    except (OSError, ValueError) as error:
        print(f"vaxflow: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
