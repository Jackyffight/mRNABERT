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
from .assessment_specs import (
    DEVELOPABILITY_STAGE_ID,
    IMMUNE_STAGE_ID,
    initialize_assessment_specifications,
)
from .domain import ProjectAnalysis
from .config import load_project_config
from .codon_usage import (
    SELECTION_METHODS,
    configure_mrna_codon_generation,
    write_codon_usage,
)
from .design_loop import default_design_documents
from .evo2_adapter import import_evo2_results, prepare_evo2_job
from .netmhc_adapter import prepare_stage4_mhc_evidence
from .pipeline import analyze_project
from .post_structure_assessment import analyze_post_structure_stages
from .post_structure_reporting import write_post_structure_run
from .proposal_generation import (
    verify_proposal_generation,
    write_proposal_generation,
)
from .product_design import analyze_product_designs
from .product_reporting import write_product_design_run
from .product_specs import (
    MRNA_PRODUCT_STAGE_ID,
    PROTEIN_PRODUCT_STAGE_ID,
    initialize_product_specifications,
)
from .ranking import analyze_integrated_ranking
from .ranking_reporting import write_ranking_run
from .ranking_specs import (
    RANKING_STAGE_ID,
    initialize_ranking_specification,
    prepare_evo2_sensitivity_specifications,
)
from .reporting import write_run_artifacts
from .structure_job import write_structure_job
from .structure_assessment import analyze_structure_results
from .structure_reporting import write_structure_run
from .stage5_model_adapter import prepare_stage5_sequence_evidence
from .stage2_external_proposals import (
    verify_stage2_model_import,
    write_stage2_model_import,
)
from .stage2_search import verify_stage2_search, write_stage2_search
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
    init_design_parser = subparsers.add_parser(
        "init-design-round",
        help="create explicit round-0 design brief, variable registry, and objective policy",
    )
    init_design_parser.add_argument("project_config", type=Path)
    init_design_parser.add_argument(
        "--mock-approved",
        action="store_true",
        help="mark templates approved only for non-scientific workflow validation",
    )
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
    generate_stage2_parser = subparsers.add_parser(
        "generate-stage2-proposals",
        help="expand a verified Stage 2 seed batch with an approved proposal grammar",
    )
    generate_stage2_parser.add_argument("project_config", type=Path)
    generate_stage2_parser.add_argument(
        "--from-run",
        type=Path,
        help="verified Stage 2 seed run; defaults to the project's latest run",
    )
    generate_stage2_parser.add_argument("--grammar", type=Path, required=True)
    generate_stage2_parser.add_argument("--output-root", type=Path)
    verify_stage2_proposals_parser = subparsers.add_parser(
        "verify-stage2-proposals",
        help="recompute and verify a materialized Stage 2 proposal directory",
    )
    verify_stage2_proposals_parser.add_argument("proposal_dir", type=Path)
    search_stage2_parser = subparsers.add_parser(
        "search-stage2",
        help="expand a Stage 2 baseline with evidence-guided multi-family search",
    )
    search_stage2_parser.add_argument("project_config", type=Path)
    search_stage2_parser.add_argument("--from-run", type=Path, required=True)
    search_stage2_parser.add_argument("--evidence-run", type=Path, required=True)
    search_stage2_parser.add_argument("--policy", type=Path, required=True)
    search_stage2_parser.add_argument("--output-root", type=Path)
    verify_stage2_search_parser = subparsers.add_parser(
        "verify-stage2-search",
        help="recompute and verify a materialized Stage 2 multi-family search",
    )
    verify_stage2_search_parser.add_argument("search_dir", type=Path)
    import_stage2_model_parser = subparsers.add_parser(
        "import-stage2-model-proposals",
        help="validate constrained external-model proposals and return them to Stage 2",
    )
    import_stage2_model_parser.add_argument("project_config", type=Path)
    import_stage2_model_parser.add_argument("--search-dir", type=Path, required=True)
    import_stage2_model_parser.add_argument("--results", type=Path, required=True)
    import_stage2_model_parser.add_argument("--job-id", required=True)
    import_stage2_model_parser.add_argument("--output-root", type=Path)
    verify_stage2_model_parser = subparsers.add_parser(
        "verify-stage2-model-import",
        help="recompute and verify an imported Stage 2 model-proposal directory",
    )
    verify_stage2_model_parser.add_argument("import_dir", type=Path)
    verify_parser = subparsers.add_parser(
        "verify-run",
        help="verify hashes and cross-file consistency for an immutable run",
    )
    verify_parser.add_argument("run_dir", type=Path)
    prepare_stage3_parser = subparsers.add_parser(
        "prepare-stage3",
        help="write a checksum-bound ESMFold2 transfer job from a verified Stage 2 run",
    )
    prepare_stage3_parser.add_argument("project_config", type=Path)
    prepare_stage3_parser.add_argument(
        "--from-run",
        type=Path,
        help="verified Stage 2 run; defaults to the project's latest run",
    )
    prepare_stage3_parser.add_argument(
        "--output-root",
        type=Path,
        help="external transfer directory; defaults under the project runtime root",
    )
    prepare_stage3_parser.add_argument(
        "--selection-manifest",
        type=Path,
        help="checksum-bound Stage 3 selection emitted by search-stage2",
    )
    import_stage3_parser = subparsers.add_parser(
        "import-stage3",
        help="verify an ESMFold2 result archive and write an immutable Stage 3 run",
    )
    import_stage3_parser.add_argument("project_config", type=Path)
    import_stage3_parser.add_argument("--results", type=Path, required=True)
    import_stage3_parser.add_argument(
        "--from-run",
        type=Path,
        help="verified Stage 2 run; defaults to the project's latest Stage 2 run",
    )
    import_stage3_parser.add_argument(
        "--job-dir",
        type=Path,
        help="unpacked job directory; inferred from the result job identity by default",
    )
    init_stage4_5_parser = subparsers.add_parser(
        "init-stage4-5",
        help="create versioned Stage 4 immune and Stage 5 developability specifications",
    )
    init_stage4_5_parser.add_argument("project_config", type=Path)
    init_stage4_5_parser.add_argument(
        "--from-run",
        type=Path,
        help="verified Stage 3 run; defaults to the project's latest run",
    )
    run_stage4_5_parser = subparsers.add_parser(
        "run-stage4-5",
        help="write deterministic Stage 4/5 evidence nodes from a verified Stage 3 run",
    )
    run_stage4_5_parser.add_argument("project_config", type=Path)
    run_stage4_5_parser.add_argument(
        "--from-run",
        type=Path,
        help="verified Stage 3 run; defaults to the project's latest Stage 3 run",
    )
    prepare_stage4_mhc_parser = subparsers.add_parser(
        "prepare-stage4-mhc",
        help="run checksum-bound NetMHCpan/NetMHCIIpan Stage 4 adapters",
    )
    prepare_stage4_mhc_parser.add_argument("project_config", type=Path)
    prepare_stage4_mhc_parser.add_argument("--from-run", type=Path, required=True)
    prepare_stage4_mhc_parser.add_argument("--netmhcpan-root", type=Path, required=True)
    prepare_stage4_mhc_parser.add_argument("--netmhciipan-root", type=Path, required=True)
    prepare_stage4_mhc_parser.add_argument(
        "--class-i-allele",
        action="append",
        required=True,
        help="NetMHCpan allele name; repeat for multiple alleles",
    )
    prepare_stage4_mhc_parser.add_argument(
        "--class-ii-allele",
        action="append",
        required=True,
        help="NetMHCIIpan allele name; repeat for multiple alleles",
    )
    prepare_stage5_models_parser = subparsers.add_parser(
        "prepare-stage5-sequence-models",
        help="run checksum-bound TMbed and metapredict Stage 5 adapters",
    )
    prepare_stage5_models_parser.add_argument("project_config", type=Path)
    prepare_stage5_models_parser.add_argument("--from-run", type=Path, required=True)
    prepare_stage5_models_parser.add_argument(
        "--toolchain-root",
        type=Path,
        required=True,
        help="installed Stage 5 model environment with toolchain.json",
    )
    prepare_stage5_models_parser.add_argument(
        "--device",
        default="cuda:0",
        help="cpu, cuda, or cuda:<index>; defaults to cuda:0",
    )
    prepare_stage5_models_parser.add_argument(
        "--tmbed-batch-size",
        type=int,
        default=4000,
        help="TMbed approximate residue batch size",
    )
    init_stage6_parser = subparsers.add_parser(
        "init-stage6",
        help="create versioned recombinant-protein and mRNA product specifications",
    )
    init_stage6_parser.add_argument("project_config", type=Path)
    init_stage6_parser.add_argument(
        "--from-run",
        type=Path,
        help="verified combined Stage 4/5 run; defaults to the project's latest run",
    )
    init_stage6_parser.add_argument(
        "--refresh-selection",
        action="store_true",
        help=(
            "archive and migrate stale Stage 6 specifications to the current "
            "checksum-bound routing manifest"
        ),
    )
    run_stage6_parser = subparsers.add_parser(
        "run-stage6",
        help="write deterministic Stage 6A protein and Stage 6B mRNA product nodes",
    )
    run_stage6_parser.add_argument("project_config", type=Path)
    run_stage6_parser.add_argument(
        "--from-run",
        type=Path,
        help="verified combined Stage 4/5 run; defaults to the project's latest run",
    )
    codon_usage_parser = subparsers.add_parser(
        "build-codon-usage",
        help="derive an audited Stage 6 codon table from a versioned RefSeq CDS FASTA",
    )
    codon_usage_parser.add_argument("source_fasta", type=Path)
    codon_usage_parser.add_argument("output_json", type=Path)
    codon_usage_parser.add_argument("--audit-output", type=Path, required=True)
    codon_usage_parser.add_argument("--species", required=True)
    codon_usage_parser.add_argument("--taxon-id", type=int, required=True)
    codon_usage_parser.add_argument("--assembly", required=True)
    codon_usage_parser.add_argument("--annotation-release", required=True)
    codon_usage_parser.add_argument("--source-url", required=True)
    codon_usage_parser.add_argument("--expected-md5", required=True)
    codon_usage_parser.add_argument(
        "--selection-method",
        choices=sorted(SELECTION_METHODS),
        default="longest-valid-cds-per-gene",
    )
    configure_codon_parser = subparsers.add_parser(
        "configure-stage6-mrna-codon-generation",
        help="bind a target-species codon table and enable exploratory Stage 6 CDS generation",
    )
    configure_codon_parser.add_argument("project_config", type=Path)
    configure_codon_parser.add_argument("--codon-table", type=Path, required=True)
    configure_codon_parser.add_argument("--designs-per-candidate", type=int, default=4)
    configure_codon_parser.add_argument("--search-multiplier", type=int, default=32)
    configure_codon_parser.add_argument("--seed", type=int, default=42)
    prepare_stage6_evo2_parser = subparsers.add_parser(
        "prepare-stage6-evo2",
        help="write a checksum-bound Evo 2 scoring job from a verified Stage 6 run",
    )
    prepare_stage6_evo2_parser.add_argument("project_config", type=Path)
    prepare_stage6_evo2_parser.add_argument("--from-run", type=Path, required=True)
    prepare_stage6_evo2_parser.add_argument(
        "--output-root",
        type=Path,
        help="external transfer directory; defaults under the project runtime root",
    )
    import_stage6_evo2_parser = subparsers.add_parser(
        "import-stage6-evo2",
        help="verify Evo 2 results and bind the evidence to the Stage 6 mRNA specification",
    )
    import_stage6_evo2_parser.add_argument("project_config", type=Path)
    import_stage6_evo2_parser.add_argument("--results", type=Path, required=True)
    init_stage7_parser = subparsers.add_parser(
        "init-stage7",
        help="create a versioned transparent integrated-ranking policy",
    )
    init_stage7_parser.add_argument("project_config", type=Path)
    init_stage7_parser.add_argument(
        "--from-run",
        type=Path,
        help="verified combined Stage 6 run; defaults to the project's latest run",
    )
    init_stage7_parser.add_argument(
        "--refresh-candidate-set",
        action="store_true",
        help="archive a stale Stage 7 specification and migrate its policy to the current candidate set",
    )
    init_stage7_evo2_parser = subparsers.add_parser(
        "init-stage7-evo2-sensitivity",
        help="write paired control and weighted ranking policies for the Evo 2 observed subset",
    )
    init_stage7_evo2_parser.add_argument("project_config", type=Path)
    init_stage7_evo2_parser.add_argument(
        "--from-run",
        type=Path,
        required=True,
        help="verified combined Stage 6 run containing imported Evo 2 evidence",
    )
    init_stage7_evo2_parser.add_argument(
        "--evo2-weight",
        type=float,
        default=0.25,
        help="exploratory Evo 2 feature weight; default: 0.25",
    )
    run_stage7_parser = subparsers.add_parser(
        "run-stage7",
        help="write deterministic integrated rankings and provisional portfolios",
    )
    run_stage7_parser.add_argument("project_config", type=Path)
    run_stage7_parser.add_argument(
        "--from-run",
        type=Path,
        help="verified combined Stage 6 run; defaults to the project's latest run",
    )
    run_stage7_parser.add_argument(
        "--specification",
        type=Path,
        help="explicit versioned ranking policy; defaults to input/stage7/ranking_specification.json",
    )
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
    design_root = runtime_root / "input" / "design"
    design_paths = {
        "design_brief": design_root / "design_brief.json",
        "design_variable_registry": design_root / "design_variable_registry.json",
        "objective_policy": design_root / "objective_policy.json",
    }
    existing = [
        path
        for path in (config_path, amino_acid_path, nucleotide_path, *design_paths.values())
        if path.exists()
    ]
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
            "design_brief": "input/design/design_brief.json",
            "design_variable_registry": "input/design/design_variable_registry.json",
            "objective_policy": "input/design/objective_policy.json",
        },
        "outputs": {"run_root": "runs"},
        "context": {
            "target_indication": "unspecified",
            "intended_host_species": "unspecified",
            "product_modalities": [],
            "protein_expression_host": "unspecified",
            "mrna_target_species": "unspecified",
            "project_mode": "unspecified",
            "scientific_release_allowed": False,
            "mrna_manufacturing_method": "unspecified",
        },
    }
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    amino_acid_path.write_text(_placeholder_records(expected_count, "aa"), encoding="utf-8")
    nucleotide_path.write_text(_placeholder_records(expected_count, "cds"), encoding="utf-8")
    design_root.mkdir(parents=True, exist_ok=True)
    design_documents = default_design_documents(
        project_id=project_id,
        target_indication="unspecified",
        intended_host_species="unspecified",
        product_modalities=[],
    )
    for name, document in design_documents.items():
        design_paths[name].write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(f"Created project: {config_path}")
    print(f"Runtime root: {runtime_root}")
    print(f"Replace placeholder sequences in: {amino_acid_path}")
    print(f"Replace placeholder sequences in: {nucleotide_path}")
    return 0


def _init_design_round(project_config: Path, *, mock_approved: bool) -> int:
    config = load_project_config(project_config)
    paths = {
        "design_brief": config.design_brief,
        "design_variable_registry": config.design_variable_registry,
        "objective_policy": config.objective_policy,
    }
    existing = [path for path in paths.values() if path.exists()]
    if existing:
        raise ValueError(f"Refusing to overwrite existing design-round input: {existing[0]}")
    documents = default_design_documents(
        project_id=config.project_id,
        target_indication=config.target_indication,
        intended_host_species=config.intended_host_species,
        product_modalities=list(config.product_modalities),
        mock_approved=mock_approved,
    )
    for name, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(documents[name], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"Created {name}: {path}")
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
        if args.command == "init-design-round":
            return _init_design_round(
                args.project_config,
                mock_approved=args.mock_approved,
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

        if args.command == "verify-stage2-proposals":
            result = verify_proposal_generation(args.proposal_dir)
            print(
                f"Stage 2 proposal generation {result['identity']}: "
                f"status={result['status']} errors={len(result['errors'])}"
            )
            for error in result["errors"]:
                print(f"  ERROR {error}")
            return 0 if result["status"] == "pass" else 2

        if args.command == "verify-stage2-search":
            result = verify_stage2_search(args.search_dir)
            print(
                f"Stage 2 search {result['identity']}: "
                f"status={result['status']} errors={len(result['errors'])}"
            )
            for error in result["errors"]:
                print(f"  ERROR {error}")
            return 0 if result["status"] == "pass" else 2

        if args.command == "verify-stage2-model-import":
            result = verify_stage2_model_import(args.import_dir)
            print(
                f"Stage 2 model import {result['identity']}: "
                f"status={result['status']} errors={len(result['errors'])}"
            )
            for error in result["errors"]:
                print(f"  ERROR {error}")
            return 0 if result["status"] == "pass" else 2

        if args.command == "import-stage2-model-proposals":
            imported = write_stage2_model_import(
                args.project_config,
                search_dir=args.search_dir,
                results_path=args.results,
                job_id=args.job_id,
                output_root=args.output_root,
            )
            print(
                "Stage 2 external-model import: "
                f"identity={imported['identity']} "
                f"accepted={imported['accepted_records']} "
                f"skipped={imported['skipped_records']}"
            )
            print(f"Import artifacts: {imported['output_dir']}")
            print(f"Candidate specification: {imported['candidate_specification']}")
            print(f"Bilingual report: {imported['report']}")
            return 0

        if args.command == "search-stage2":
            searched = write_stage2_search(
                args.project_config,
                policy_path=args.policy,
                seed_run_dir=args.from_run,
                evidence_run_dir=args.evidence_run,
                output_root=args.output_root,
            )
            print(
                "Stage 2 multi-family search: "
                f"identity={searched['identity']} "
                f"atomic={searched['generated_atomic_components']} "
                f"eligible_fusions={searched['eligible_unique_fusions']} "
                f"materialized_fusions={searched['materialized_fusions']} "
                f"total={searched['total_candidates']} "
                f"stage3={searched['stage3_selected_candidates']}"
            )
            print(f"Search artifacts: {searched['output_dir']}")
            print(f"Candidate specification: {searched['candidate_specification']}")
            print(f"Stage 3 selection: {searched['stage3_selection']}")
            print(f"External model jobs: {searched['external_model_jobs']}")
            print(f"Bilingual report: {searched['report']}")
            return 0

        if args.command == "generate-stage2-proposals":
            generated = write_proposal_generation(
                args.project_config,
                grammar_path=args.grammar,
                seed_run_dir=args.from_run,
                output_root=args.output_root,
            )
            print(
                "Stage 2 proposal generation: "
                f"identity={generated['identity']} "
                f"generated={generated['generated_candidates']} "
                f"skipped={generated['skipped_candidates']} "
                f"total={generated['total_candidates']}"
            )
            print(f"Proposal artifacts: {generated['output_dir']}")
            print(f"Candidate specification: {generated['candidate_specification']}")
            print(f"Stage 1 source run: {generated['stage1_run_path']}")
            print(f"Bilingual report: {generated['report']}")
            return 0

        if args.command == "prepare-stage3":
            prepared = write_structure_job(
                args.project_config,
                source_run_dir=args.from_run,
                output_root=args.output_root,
                selection_manifest=args.selection_manifest,
            )
            print(
                f"Stage 3 exploratory job: identity={prepared['job_identity']} "
                f"records={prepared['records']} lengths="
                f"{prepared['minimum_length']}-{prepared['maximum_length']}"
            )
            print(f"Job directory: {prepared['job_dir']}")
            print(f"Transfer archive: {prepared['archive']}")
            print(f"Transfer SHA256: {prepared['archive_sha256']}")
            return 0

        if args.command == "import-stage3":
            structure_analysis = analyze_structure_results(
                args.project_config,
                result_archive=args.results,
                source_run_dir=args.from_run,
                job_dir=args.job_dir,
            )
            run_dir = write_structure_run(structure_analysis)
            node_dir = run_dir / "nodes" / "protein_structure_assessment"
            print(
                f"Stage 3 structure assessment: status=pass "
                f"candidates={len(structure_analysis.assessments)} "
                f"review_flags={len(structure_analysis.findings)}"
            )
            print(f"Run artifacts: {run_dir}")
            print(f"Node summary: {node_dir / 'summary.json'}")
            print(f"Node report: {node_dir / 'report.html'}")
            return 0

        if args.command == "init-stage4-5":
            initialized = initialize_assessment_specifications(
                args.project_config,
                source_run_dir=args.from_run,
            )
            print(f"Stage 4/5 specifications: source_run={initialized['source_run']}")
            print(f"Immune specification: {initialized['immune_specification']}")
            print(
                "Developability specification: "
                f"{initialized['developability_specification']}"
            )
            print(f"Created files: {len(initialized['created'])}")
            return 0

        if args.command == "run-stage4-5":
            post_structure = analyze_post_structure_stages(
                args.project_config,
                source_run_dir=args.from_run,
            )
            run_dir = write_post_structure_run(post_structure)
            immune_node = run_dir / "nodes" / IMMUNE_STAGE_ID
            developability_node = run_dir / "nodes" / DEVELOPABILITY_STAGE_ID
            print(
                "Stage 4/5 assessment: "
                f"immune={post_structure.immune_result['status']} "
                f"developability={post_structure.developability_result['status']} "
                f"immune_missing={len(post_structure.immune_result['requirements'])} "
                "developability_missing="
                f"{len(post_structure.developability_result['requirements'])}"
            )
            print(f"Run artifacts: {run_dir}")
            print(f"Immune report: {immune_node / 'report.html'}")
            print(f"Developability report: {developability_node / 'report.html'}")
            return 0

        if args.command == "prepare-stage4-mhc":
            prepared = prepare_stage4_mhc_evidence(
                args.project_config,
                source_run_dir=args.from_run,
                netmhcpan_root=args.netmhcpan_root,
                netmhciipan_root=args.netmhciipan_root,
                class_i_alleles=args.class_i_allele,
                class_ii_alleles=args.class_ii_allele,
                progress=lambda message: print(message, flush=True),
            )
            print(
                "Stage 4 MHC adapter: "
                f"identity={prepared['identity']} "
                f"candidates={prepared['candidate_count']} "
                f"observations={prepared['observation_count']}"
            )
            print(
                "Supported observations: "
                f"class_I={prepared['supported_count_by_class']['I']} "
                f"class_II={prepared['supported_count_by_class']['II']}"
            )
            print(f"Adapter artifacts: {prepared['output_dir']}")
            print(f"Immune specification: {prepared['immune_specification']}")
            print("Population coverage remains unapproved; this is a technical smoke panel.")
            return 0

        if args.command == "prepare-stage5-sequence-models":
            prepared = prepare_stage5_sequence_evidence(
                args.project_config,
                source_run_dir=args.from_run,
                toolchain_root=args.toolchain_root,
                device=args.device,
                tmbed_batch_size=args.tmbed_batch_size,
                progress=lambda message: print(message, flush=True),
            )
            counts = prepared["observation_counts"]
            print(
                "Stage 5 sequence-model adapters: "
                f"identity={prepared['identity']} "
                f"candidates={prepared['candidate_count']}"
            )
            print(
                "Model observations: "
                f"signal_peptide={counts['signal_peptide']} "
                f"transmembrane_topology={counts['transmembrane_topology']} "
                f"disorder={counts['disorder']}"
            )
            print(f"Adapter artifacts: {prepared['output_dir']}")
            print(
                "Still not evaluated: "
                + ", ".join(prepared["not_evaluated_adapters"])
            )
            print(
                "Developability specification: "
                f"{prepared['developability_specification']}"
            )
            return 0

        if args.command == "init-stage6":
            initialized = initialize_product_specifications(
                args.project_config,
                source_run_dir=args.from_run,
                refresh_selection=args.refresh_selection,
            )
            print(f"Stage 6 specifications: source_run={initialized['source_run']}")
            print(f"Routing policy: {initialized['routing_policy']}")
            print(f"Routing manifest: {initialized['routing_manifest']}")
            counts = initialized["routing_counts"]
            print(
                "Routing counts: "
                f"active={counts['active']} priority={counts['priority']} "
                f"diversity_rescue={counts['diversity_rescue']} "
                f"archive={counts['archive']} "
                f"product_drafting={counts['product_drafting']} "
                f"expensive_followup={counts['expensive_followup']}"
            )
            print(f"Protein specification: {initialized['protein_specification']}")
            print(f"mRNA specification: {initialized['mrna_specification']}")
            print(f"Created files: {len(initialized['created'])}")
            print(f"Archived stale files: {len(initialized['archived'])}")
            return 0

        if args.command == "run-stage6":
            product_analysis = analyze_product_designs(
                args.project_config,
                source_run_dir=args.from_run,
            )
            run_dir = write_product_design_run(product_analysis)
            protein_node = run_dir / "nodes" / PROTEIN_PRODUCT_STAGE_ID
            mrna_node = run_dir / "nodes" / MRNA_PRODUCT_STAGE_ID
            print(
                "Stage 6 product design: "
                f"protein={product_analysis.protein_result['status']} "
                f"mrna={product_analysis.mrna_result['status']} "
                f"protein_products={len(product_analysis.protein_result['products'])} "
                f"mrna_designs={len(product_analysis.mrna_result['designs'])}"
            )
            print(f"Run artifacts: {run_dir}")
            print(f"Protein report: {protein_node / 'report.html'}")
            print(f"mRNA report: {mrna_node / 'report.html'}")
            return 0

        if args.command == "build-codon-usage":
            built = write_codon_usage(
                args.source_fasta,
                args.output_json,
                args.audit_output,
                species=args.species,
                taxon_id=args.taxon_id,
                assembly=args.assembly,
                annotation_release=args.annotation_release,
                source_url=args.source_url,
                expected_md5=args.expected_md5,
                selection_method=args.selection_method,
            )
            print(
                "Codon usage table: "
                f"records={built['selected_cds_records']} "
                f"sense_codons={built['selected_sense_codons']} "
                f"sha256={built['codon_table_sha256']}"
            )
            print(f"Table: {built['output_path']}")
            print(f"Audit: {built['audit_path']}")
            return 0

        if args.command == "configure-stage6-mrna-codon-generation":
            configured = configure_mrna_codon_generation(
                args.project_config,
                args.codon_table,
                designs_per_candidate=args.designs_per_candidate,
                search_multiplier=args.search_multiplier,
                seed=args.seed,
            )
            print(
                "Stage 6 mRNA CDS generation enabled: "
                f"designs_per_candidate={configured['designs_per_candidate']} "
                f"search_multiplier={configured['search_multiplier']} "
                f"seed={configured['seed']}"
            )
            print(f"Codon table: {configured['codon_usage_path']}")
            print(f"mRNA specification: {configured['specification_path']}")
            print(f"Previous specification: {configured['history_path']}")
            return 0

        if args.command == "prepare-stage6-evo2":
            prepared = prepare_evo2_job(
                args.project_config,
                source_run_dir=args.from_run,
                output_root=args.output_root,
            )
            print(
                "Stage 6 Evo 2 job: "
                f"identity={prepared['job_identity']} "
                f"records={prepared['records']}"
            )
            print(
                "mRNA design batch SHA256: "
                f"{prepared['mrna_design_batch_sha256']}"
            )
            print(f"Job directory: {prepared['job_dir']}")
            print(f"Transfer archive: {prepared['archive']}")
            print(f"Transfer SHA256: {prepared['archive_sha256']}")
            return 0

        if args.command == "import-stage6-evo2":
            imported = import_evo2_results(
                args.project_config,
                result_archive=args.results,
            )
            print(
                "Stage 6 Evo 2 evidence: "
                f"job={imported['job_identity']} "
                f"result={imported['result_identity']} "
                f"observations={imported['observations']}"
            )
            print(f"Evidence: {imported['evidence_path']}")
            print(f"Evidence SHA256: {imported['evidence_sha256']}")
            print(f"mRNA specification: {imported['specification_path']}")
            print(f"Stage 4/5 source run for Stage 6 rerun: {imported['stage5_run_path']}")
            return 0

        if args.command == "init-stage7":
            initialized = initialize_ranking_specification(
                args.project_config,
                source_run_dir=args.from_run,
                refresh_candidate_set=args.refresh_candidate_set,
            )
            print(f"Stage 7 specification: source_run={initialized['source_run']}")
            print(f"Ranking specification: {initialized['ranking_specification']}")
            print(f"Created files: {len(initialized['created'])}")
            print(f"Archived stale files: {len(initialized['archived'])}")
            return 0

        if args.command == "init-stage7-evo2-sensitivity":
            prepared = prepare_evo2_sensitivity_specifications(
                args.project_config,
                source_run_dir=args.from_run,
                evo2_weight=args.evo2_weight,
            )
            print(
                "Stage 7 Evo 2 sensitivity: "
                f"experiment={prepared['experiment_id']} "
                f"candidates={prepared['candidate_count']} "
                f"designs={prepared['design_count']} "
                f"weight={prepared['evo2_weight']}"
            )
            print(f"Control specification: {prepared['control_specification']}")
            print(f"Weighted specification: {prepared['weighted_specification']}")
            return 0

        if args.command == "run-stage7":
            ranking_analysis = analyze_integrated_ranking(
                args.project_config,
                source_run_dir=args.from_run,
                specification_path=args.specification,
            )
            run_dir = write_ranking_run(ranking_analysis)
            node = run_dir / "nodes" / RANKING_STAGE_ID
            print(
                "Stage 7 integrated ranking: "
                f"status={ranking_analysis.result['status']} "
                f"rows={len(ranking_analysis.result['rankings'])} "
                "provisional="
                f"{sum(len(items) for items in ranking_analysis.result['provisional_portfolios'].values())} "
                "formal=0"
            )
            print(f"Run artifacts: {run_dir}")
            print(f"Ranking report: {node / 'report.html'}")
            return 0

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
