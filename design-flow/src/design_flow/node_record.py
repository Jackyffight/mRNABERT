"""Structured audit, intervention, and handoff records for the current node."""

from __future__ import annotations

from typing import Any

from . import __version__
from .domain import HumanAction, ProjectAnalysis
from .workflow import (
    CURRENT_STAGE_ID,
    STAGE_BY_ID,
    action_due_for_handoff,
    workflow_contract,
    workflow_contract_sha256,
)


NEXT_STAGE_ID = "candidate_specification"
UNSPECIFIED_VALUES = frozenset({"", "none", "tbd", "unknown", "unspecified"})


def _is_unspecified(value: str) -> bool:
    return value.strip().lower() in UNSPECIFIED_VALUES


def _automatic_human_actions(analysis: ProjectAnalysis) -> list[HumanAction]:
    config = analysis.config
    actions: list[HumanAction] = []
    if _is_unspecified(config.target_indication):
        actions.append(
            HumanAction(
                action_id="define-target-indication",
                question="Define the target indication, use scenario, and measurable program objective.",
                required_before_stage=NEXT_STAGE_ID,
                question_zh="明确目标适应症、使用场景和可量化的项目目标。",
            )
        )
    if _is_unspecified(config.intended_host_species):
        actions.append(
            HumanAction(
                action_id="confirm-intended-host-species",
                question="Confirm the intended vaccinated host species and population assumptions.",
                required_before_stage=NEXT_STAGE_ID,
                question_zh="确认预期接种的宿主物种和目标群体假设。",
            )
        )
    if not config.product_modalities:
        actions.append(
            HumanAction(
                action_id="select-product-modalities",
                question="Select the product modalities that must be designed and compared.",
                required_before_stage=NEXT_STAGE_ID,
                question_zh="选择需要设计和比较的产品路线。",
            )
        )
    if "recombinant_protein" in config.product_modalities and _is_unspecified(
        config.protein_expression_host
    ):
        actions.append(
            HumanAction(
                action_id="select-protein-expression-host",
                question="Select the initial recombinant protein expression host and compartment assumptions.",
                required_before_stage="developability_assessment",
                question_zh="选择重组蛋白的初始表达宿主和表达区室假设。",
            )
        )
    if "mrna" in config.product_modalities and _is_unspecified(config.mrna_target_species):
        actions.append(
            HumanAction(
                action_id="confirm-mrna-target-species",
                question="Confirm the species and cell context used for mRNA sequence design constraints.",
                required_before_stage="mrna_product_design",
                question_zh="确认 mRNA 序列设计约束使用的物种和细胞环境。",
            )
        )
    return actions


def effective_human_actions(analysis: ProjectAnalysis) -> list[HumanAction]:
    actions = list(analysis.config.human_actions)
    known_ids = {action.action_id for action in actions}
    actions.extend(
        action
        for action in _automatic_human_actions(analysis)
        if action.action_id not in known_ids
    )
    return actions


def _has_issue(analysis: ProjectAnalysis, codes: set[str]) -> bool:
    return any(issue.code in codes and issue.severity == "error" for issue in analysis.all_issues)


def build_input_audit(analysis: ProjectAnalysis) -> dict[str, Any]:
    expected = analysis.config.expected_protein_count
    paired = len(analysis.proteins)
    record_count_failed = _has_issue(analysis, {"aa_record_count", "cds_record_count"})
    pairing_failed = _has_issue(analysis, {"missing_aa", "missing_cds"})
    syntax_failed = _has_issue(
        analysis,
        {
            "aa_empty",
            "aa_internal_stop",
            "aa_invalid_symbols",
            "cds_empty",
            "cds_frame",
            "cds_internal_stop",
            "cds_invalid_symbols",
        },
    )
    translation_failed = _has_issue(analysis, {"translation_mismatch"})
    return {
        "stage_id": CURRENT_STAGE_ID,
        "status": analysis.status,
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
        "checks": [
            {
                "check_id": "source-files-hashed",
                "status": "pass",
                "evidence": "Project configuration and both FASTA files have SHA-256 identities.",
            },
            {
                "check_id": "expected-record-count",
                "status": "fail" if record_count_failed else "pass",
                "evidence": f"expected={expected}, paired={paired}",
            },
            {
                "check_id": "one-to-one-id-pairing",
                "status": "fail" if pairing_failed else "pass",
                "evidence": f"paired_ids={[protein.protein_id for protein in analysis.proteins]}",
            },
            {
                "check_id": "sequence-alphabet-frame-stop",
                "status": "fail" if syntax_failed else "pass",
                "evidence": "AA alphabet, CDS alphabet, reading frame, and stop behavior checked.",
            },
            {
                "check_id": "translation-equivalence",
                "status": "fail" if translation_failed else "pass",
                "evidence": (
                    f"exact_matches={sum(protein.metrics.get('translation_matches') is True for protein in analysis.proteins)}"
                    f"/{paired}"
                ),
            },
        ],
        "findings": [issue.to_dict() for issue in analysis.all_issues],
    }


def build_process_record(analysis: ProjectAnalysis) -> dict[str, Any]:
    return {
        "stage_id": CURRENT_STAGE_ID,
        "pipeline_version": __version__,
        "operations": [
            {
                "operation": "parse_fasta",
                "behavior": "Parse multiline records, preserve record IDs, reject duplicate or empty records.",
            },
            {
                "operation": "normalize_sequences",
                "behavior": "Uppercase sequences and convert RNA U to DNA T with an explicit warning.",
            },
            {
                "operation": "translate_cds",
                "behavior": "Translate with the standard genetic code and retain start/stop/frame findings.",
            },
            {
                "operation": "compare_translation",
                "behavior": "Require residue-exact AA/CDS agreement unless a future construct transformation is declared.",
            },
            {
                "operation": "calculate_descriptors",
                "behavior": (
                    "Calculate length, molecular-weight estimate, composition, entropy, homopolymers, "
                    "hydrophobic/charged fractions, and GC metrics."
                ),
            },
            {
                "operation": "assign_candidate_identity",
                "behavior": "Hash normalized protein ID, AA, and CDS into an immutable original-candidate ID.",
            },
        ],
        "parameters": {
            "genetic_code": "standard",
            "expected_protein_count": analysis.config.expected_protein_count,
            "product_modalities": list(analysis.config.product_modalities),
        },
    }


def build_output_audit(analysis: ProjectAnalysis) -> dict[str, Any]:
    errors = sum(issue.severity == "error" for issue in analysis.all_issues)
    warnings = sum(issue.severity == "warning" for issue in analysis.all_issues)
    return {
        "stage_id": CURRENT_STAGE_ID,
        "status": analysis.status,
        "summary": {
            "accepted_candidates": sum(protein.status == "pass" for protein in analysis.proteins),
            "rejected_candidates": sum(protein.status == "fail" for protein in analysis.proteins),
            "errors": errors,
            "warnings": warnings,
        },
        "candidates": [
            {
                "protein_id": protein.protein_id,
                "candidate_id": protein.candidate_id,
                "status": protein.status,
                "aa_length": protein.metrics["aa_length"],
                "cds_length_nt": protein.metrics["cds_length_nt"],
                "translation_matches": protein.metrics["translation_matches"],
            }
            for protein in analysis.proteins
        ],
        "checks": [
            {
                "check_id": "candidate-identities-present",
                "status": "pass" if all(protein.candidate_id for protein in analysis.proteins) else "fail",
            },
            {
                "check_id": "accepted-candidates-have-exact-translation",
                "status": (
                    "pass"
                    if all(
                        protein.status == "fail" or protein.metrics["translation_matches"] is True
                        for protein in analysis.proteins
                    )
                    else "fail"
                ),
            },
            {
                "check_id": "findings-exported",
                "status": "pass",
            },
        ],
    }


def build_node_bundle(analysis: ProjectAnalysis, run_id: str) -> dict[str, Any]:
    actions = effective_human_actions(analysis)
    open_actions = [action for action in actions if action.status == "open"]
    due_actions = [
        action
        for action in open_actions
        if action_due_for_handoff(
            action.required_before_stage,
            current_stage=CURRENT_STAGE_ID,
            to_stages=(NEXT_STAGE_ID,),
        )
    ]
    if analysis.status == "fail":
        readiness = "blocked"
        node_status = "blocked"
    elif due_actions:
        readiness = "needs_human_input"
        node_status = "needs_human_input"
    else:
        readiness = "ready"
        node_status = "complete"

    input_audit = build_input_audit(analysis)
    process_record = build_process_record(analysis)
    output_audit = build_output_audit(analysis)
    action_document = {
        "stage_id": CURRENT_STAGE_ID,
        "open_count": len(open_actions),
        "due_before_next_stage_count": len(due_actions),
        "actions": [action.to_dict() for action in actions],
    }
    handoff = {
        "schema_version": 1,
        "run_id": run_id,
        "from_stage": CURRENT_STAGE_ID,
        "to_stage": NEXT_STAGE_ID,
        "readiness": readiness,
        "blocking_action_ids": [action.action_id for action in due_actions],
        "carried_human_actions": [action.to_dict() for action in open_actions],
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
            "input_digests": analysis.input_digests,
            "candidates": [
                {
                    "protein_id": protein.protein_id,
                    "candidate_id": protein.candidate_id,
                    "status": protein.status,
                }
                for protein in analysis.proteins
            ],
            "qc_findings": [issue.to_dict() for issue in analysis.all_issues],
        },
    }
    summary = {
        "schema_version": 1,
        "run_id": run_id,
        "stage_id": CURRENT_STAGE_ID,
        "stage_name": STAGE_BY_ID[CURRENT_STAGE_ID].name,
        "status": node_status,
        "computational_audit_status": analysis.status,
        "next_stage": NEXT_STAGE_ID,
        "handoff_readiness": readiness,
        "accepted_candidates": output_audit["summary"]["accepted_candidates"],
        "errors": output_audit["summary"]["errors"],
        "warnings": output_audit["summary"]["warnings"],
        "open_human_actions": len(open_actions),
        "due_human_actions": len(due_actions),
    }
    return {
        "status": node_status,
        "summary": summary,
        "input_audit": input_audit,
        "process_record": process_record,
        "output_audit": output_audit,
        "human_actions": action_document,
        "handoff": handoff,
    }


def build_workflow_snapshot(current_node_status: str) -> dict[str, Any]:
    contract = workflow_contract()
    contract["contract_sha256"] = workflow_contract_sha256()
    contract["current_stage"] = CURRENT_STAGE_ID
    for stage in contract["stages"]:
        stage["status"] = (
            current_node_status
            if stage["stage_id"] == CURRENT_STAGE_ID
            else "not_evaluated"
        )
    return contract
