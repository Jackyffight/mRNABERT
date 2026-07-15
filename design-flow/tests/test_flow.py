from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from design_flow.cli import main as cli_main
from design_flow.assessment_specs import (
    ADAPTER_IDS,
    DEVELOPABILITY_ADAPTER_IDS,
    initialize_assessment_specifications,
)
from design_flow.candidate_reporting import write_candidate_run
from design_flow.candidate_specification import analyze_candidate_specification
from design_flow.domain import FastaRecord
from design_flow.design_loop import default_design_documents
from design_flow.fasta import parse_fasta
from design_flow.pipeline import analyze_project
from design_flow.post_structure_assessment import analyze_post_structure_stages
from design_flow.post_structure_reporting import write_post_structure_run
from design_flow.product_design import analyze_product_designs
from design_flow.product_reporting import write_product_design_run
from design_flow.product_specs import initialize_product_specifications
from design_flow.proposal_generation import (
    verify_proposal_generation,
    write_proposal_generation,
)
from design_flow.ranking import analyze_integrated_ranking
from design_flow.ranking_reporting import write_ranking_run
from design_flow.ranking_specs import initialize_ranking_specification
from design_flow.qc import CODON_TABLE, analyze_sequence_pairs, normalize_nucleotide, translate_cds
from design_flow.reporting import write_run_artifacts
from design_flow.structure_job import build_structure_job, write_structure_job
from design_flow.stage2_external_proposals import (
    _validate_results,
    verify_stage2_model_import,
    write_stage2_model_import,
)
from design_flow.stage2_search import _selection_records
from design_flow.structure_assessment import analyze_structure_results
from design_flow.structure_reporting import write_structure_run
from design_flow.structure_job import _document_sha256, _identity
from design_flow.structure_metrics import ResidueGeometry, geometry_metrics
from design_flow.verification import (
    _workflow_blueprint_matches,
    build_artifact_index,
    verify_run,
)
from design_flow.workflow import (
    CURRENT_STAGE_ID,
    FULL_WORKFLOW,
    SYSTEM_ARCHITECTURE_VERSION,
    WORKFLOW_ID,
    WORKFLOW_VERSION,
    action_due_for_handoff,
    approved_workflow_hash,
    validate_workflow,
    workflow_contract,
    workflow_contract_sha256,
)


VALID_AA = [
    FastaRecord("protein_1", "", "MAA"),
    FastaRecord("protein_2", "", "MKF"),
    FastaRecord("protein_3", "", "MGP"),
]
VALID_CDS = [
    FastaRecord("protein_1", "", "ATGGCTGCTTAA"),
    FastaRecord("protein_2", "", "ATGAAATTTTGA"),
    FastaRecord("protein_3", "", "ATGGGTCCTTAG"),
]


def _write_design_inputs(
    runtime_dir: Path,
    *,
    project_id: str,
    target_indication: str = "test indication",
    intended_host_species: str = "test host",
) -> None:
    design_dir = runtime_dir / "input" / "design"
    design_dir.mkdir(parents=True, exist_ok=True)
    documents = default_design_documents(
        project_id=project_id,
        target_indication=target_indication,
        intended_host_species=intended_host_species,
        product_modalities=["recombinant_protein", "mrna"],
        mock_approved=True,
    )
    for name, document in documents.items():
        (design_dir / f"{name}.json").write_text(
            json.dumps(document, sort_keys=True),
            encoding="utf-8",
        )


DESIGN_INPUT_PATHS = {
    "design_brief": "input/design/design_brief.json",
    "design_variable_registry": "input/design/design_variable_registry.json",
    "objective_policy": "input/design/objective_policy.json",
}


class StructureMetricTests(unittest.TestCase):
    def test_principal_axes_are_deterministic_for_linear_coordinates(self) -> None:
        residues = [
            ResidueGeometry("A", index + 1, "", "A", (float(index), 0.0, 0.0), 85.0, 85.0)
            for index in range(3)
        ]

        metrics = geometry_metrics(residues)

        self.assertEqual(metrics["principal_axis_extents_angstrom"], [2.0, 0.0, 0.0])
        self.assertEqual(metrics["principal_axis_vectors"][0], [1.0, 0.0, 0.0])
        self.assertAlmostEqual(metrics["radius_of_gyration_angstrom"], (2 / 3) ** 0.5, places=6)
        self.assertEqual(metrics["shape_anisotropy"], 1.0)


class FastaTests(unittest.TestCase):
    def test_parses_multiline_records_and_comments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            path = Path(temporary_dir) / "records.fasta"
            path.write_text(
                "; comment\n>protein_1 first protein\nMA\nA\n\n>protein_2\nMKF\n",
                encoding="utf-8",
            )
            records = parse_fasta(path)

        self.assertEqual([record.record_id for record in records], ["protein_1", "protein_2"])
        self.assertEqual(records[0].description, "first protein")
        self.assertEqual(records[0].sequence, "MAA")

    def test_rejects_duplicate_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            path = Path(temporary_dir) / "records.fasta"
            path.write_text(">same\nMAA\n>same\nMKF\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate FASTA ID"):
                parse_fasta(path)


class SequenceAuditTests(unittest.TestCase):
    def test_matching_three_proteins_pass(self) -> None:
        proteins, issues = analyze_sequence_pairs(VALID_AA, VALID_CDS, expected_count=3)

        self.assertEqual(issues, [])
        self.assertEqual([protein.status for protein in proteins], ["pass", "pass", "pass"])
        self.assertTrue(all(protein.metrics["translation_matches"] for protein in proteins))
        self.assertEqual(proteins[0].metrics["aa_length"], 3)
        self.assertEqual(proteins[0].metrics["cds_length_nt"], 12)

    def test_translation_mismatch_reports_first_position(self) -> None:
        proteins, issues = analyze_sequence_pairs(
            [FastaRecord("protein_1", "", "MGA")],
            [FastaRecord("protein_1", "", "ATGGCTGCTTAA")],
            expected_count=1,
        )

        self.assertEqual(issues, [])
        mismatch = next(issue for issue in proteins[0].issues if issue.code == "translation_mismatch")
        self.assertIn("position 2", mismatch.message)
        self.assertEqual(proteins[0].status, "fail")

    def test_missing_pair_and_count_fail_at_project_level(self) -> None:
        proteins, issues = analyze_sequence_pairs(
            VALID_AA,
            VALID_CDS[:2],
            expected_count=3,
        )

        self.assertEqual(len(proteins), 2)
        self.assertIn("cds_record_count", {issue.code for issue in issues})
        self.assertIn("missing_cds", {issue.code for issue in issues})

    def test_rna_is_normalized_before_translation(self) -> None:
        normalized, normalization_issues = normalize_nucleotide("AUGGCUUAA", "protein_1")
        translated, translation_issues, terminal_stop = translate_cds(normalized, "protein_1")

        self.assertEqual(normalized, "ATGGCTTAA")
        self.assertEqual(translated, "MA")
        self.assertTrue(terminal_stop)
        self.assertEqual([issue.code for issue in normalization_issues], ["rna_normalized"])
        self.assertEqual(translation_issues, [])


class WorkflowContractTests(unittest.TestCase):
    def test_full_workflow_is_a_valid_dag(self) -> None:
        validate_workflow(FULL_WORKFLOW)

    def test_duplicate_stage_id_is_rejected(self) -> None:
        duplicate = replace(FULL_WORKFLOW[1], stage_id=CURRENT_STAGE_ID)
        with self.assertRaisesRegex(ValueError, "Duplicate workflow stage IDs"):
            validate_workflow((FULL_WORKFLOW[0], duplicate, *FULL_WORKFLOW[2:]))

    def test_unknown_dependency_is_rejected(self) -> None:
        invalid = replace(FULL_WORKFLOW[1], depends_on=("missing-stage",))
        with self.assertRaisesRegex(ValueError, "Unknown workflow dependencies"):
            validate_workflow((FULL_WORKFLOW[0], invalid, *FULL_WORKFLOW[2:]))

    def test_dependency_cycle_is_rejected(self) -> None:
        cyclic_first = replace(FULL_WORKFLOW[0], depends_on=(FULL_WORKFLOW[1].stage_id,))
        with self.assertRaisesRegex(ValueError, "dependency cycle"):
            validate_workflow((cyclic_first, *FULL_WORKFLOW[1:]))

    def test_second_entry_stage_is_rejected(self) -> None:
        second_root = replace(FULL_WORKFLOW[1], depends_on=())
        with self.assertRaisesRegex(ValueError, "exactly one entry stage"):
            validate_workflow((FULL_WORKFLOW[0], second_root, *FULL_WORKFLOW[2:]))

    def test_empty_stage_contract_is_rejected(self) -> None:
        invalid = replace(FULL_WORKFLOW[1], output_audit=())
        with self.assertRaisesRegex(ValueError, "empty audit contract"):
            validate_workflow((FULL_WORKFLOW[0], invalid, *FULL_WORKFLOW[2:]))

    def test_frozen_machine_contract_matches_executable_workflow(self) -> None:
        design_flow_root = Path(__file__).resolve().parents[1]
        frozen_path = design_flow_root / "docs" / f"workflow-v{WORKFLOW_VERSION}.json"
        frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
        expected = workflow_contract()
        expected["contract_sha256"] = workflow_contract_sha256()

        self.assertEqual(frozen, expected)
        self.assertEqual(approved_workflow_hash(), workflow_contract_sha256())

    def test_historical_v1_contract_remains_verifiable(self) -> None:
        design_flow_root = Path(__file__).resolve().parents[1]
        historical = json.loads(
            (design_flow_root / "docs/workflow-v1.json").read_text(encoding="utf-8")
        )

        self.assertTrue(_workflow_blueprint_matches(historical))

    def test_human_workflow_document_records_version_hash_and_all_stages(self) -> None:
        design_flow_root = Path(__file__).resolve().parents[1]
        document = (
            design_flow_root / "docs" / f"workflow-v{WORKFLOW_VERSION}.md"
        ).read_text(encoding="utf-8")

        self.assertIn(f"System architecture version: `{SYSTEM_ARCHITECTURE_VERSION}`", document)
        self.assertIn(f"Workflow ID: `{WORKFLOW_ID}`", document)
        self.assertIn(f"Workflow version: `{WORKFLOW_VERSION}`", document)
        self.assertIn(f"Contract SHA-256: `{workflow_contract_sha256()}`", document)
        for stage in FULL_WORKFLOW:
            self.assertIn(f"`{stage.stage_id}`", document)

    def test_architecture_baseline_links_governance_and_adr(self) -> None:
        design_flow_root = Path(__file__).resolve().parents[1]
        architecture = (design_flow_root / "ARCHITECTURE.md").read_text(encoding="utf-8")

        self.assertIn("frozen architecture baseline v2", architecture)
        self.assertIn("docs/audit-automation-and-llm-governance.md", architecture)
        self.assertIn("docs/adr/0002-round-based-design-optimization.md", architecture)

    def test_human_actions_block_only_when_due_for_handoff(self) -> None:
        self.assertFalse(
            action_due_for_handoff(
                "experiment_release",
                current_stage="protein_structure_assessment",
                to_stages=(
                    "immune_evidence_assessment",
                    "developability_assessment",
                ),
            )
        )
        self.assertTrue(
            action_due_for_handoff(
                "candidate_specification",
                current_stage="protein_structure_assessment",
                to_stages=("immune_evidence_assessment",),
            )
        )
        self.assertTrue(
            action_due_for_handoff(
                "integrated_ranking",
                current_stage="mrna_product_design",
                to_stages=("integrated_ranking",),
            )
        )
        self.assertTrue(
            action_due_for_handoff(
                "experiment_release",
                current_stage="integrated_ranking",
                to_stages=("experiment_release",),
            )
        )


class EndToEndTests(unittest.TestCase):
    def _write_project(self, root: Path) -> Path:
        source_dir = root / "source-project"
        runtime_dir = root / "runtime-project"
        input_dir = runtime_dir / "input"
        input_dir.mkdir(parents=True)
        (input_dir / "proteins_aa.fasta").write_text(
            ">protein_1\nMAA\n>protein_2\nMKF\n>protein_3\nMGP\n",
            encoding="utf-8",
        )
        (input_dir / "proteins_cds.fasta").write_text(
            ">protein_1\nATGGCTGCTTAA\n"
            ">protein_2\nATGAAATTTTGA\n"
            ">protein_3\nATGGGTCCTTAG\n",
            encoding="utf-8",
        )
        _write_design_inputs(runtime_dir, project_id="test-three-protein")
        config = {
            "schema_version": 1,
            "project_id": "test-three-protein",
            "expected_protein_count": 3,
            "runtime_root": str(runtime_dir),
            "inputs": {
                "amino_acid_fasta": "input/proteins_aa.fasta",
                "nucleotide_fasta": "input/proteins_cds.fasta",
                **DESIGN_INPUT_PATHS,
            },
            "outputs": {"run_root": "runs"},
            "context": {
                "target_indication": "test indication",
                "intended_host_species": "test host",
                "product_modalities": ["recombinant_protein", "mrna"],
                "protein_expression_host": "test expression host",
                "mrna_target_species": "test host",
                "project_mode": "mock_workflow_validation",
                "scientific_release_allowed": False,
                "mrna_manufacturing_method": "in_vitro_transcription",
            },
        }
        source_dir.mkdir(parents=True)
        config_path = source_dir / "project.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        return config_path

    def test_run_writes_reproducible_artifact_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            analysis = analyze_project(self._write_project(root))
            run_dir = write_run_artifacts(
                analysis,
                now=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(analysis.status, "pass")
            self.assertEqual(
                {path.name for path in run_dir.iterdir()},
                {"manifest.json", "workflow.json", "artifact_index.json", "inputs", "nodes"},
            )
            self.assertEqual(
                {path.name for path in (run_dir / "inputs").iterdir()},
                {
                    "project.json", "proteins_aa.fasta", "proteins_cds.fasta",
                    "design_brief.json", "design_variable_registry.json",
                    "objective_policy.json",
                },
            )
            node_dir = run_dir / "nodes" / CURRENT_STAGE_ID
            self.assertEqual(
                {path.name for path in node_dir.iterdir()},
                {
                    "summary.json",
                    "report.html",
                    "input_audit.json",
                    "process_record.json",
                    "output_audit.json",
                    "human_actions.json",
                    "handoff.json",
                    "proteins.json",
                    "design_round.json",
                    "proteins.csv",
                    "qc_issues.csv",
                },
            )
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            workflow = json.loads((run_dir / "workflow.json").read_text(encoding="utf-8"))
            summary = json.loads((node_dir / "summary.json").read_text(encoding="utf-8"))
            latest = json.loads(
                (root / "runtime-project" / "runs" / "latest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["status"], "complete")
            self.assertEqual(
                manifest["context"]["project_mode"], "mock_workflow_validation"
            )
            self.assertFalse(manifest["context"]["scientific_release_allowed"])
            self.assertEqual(
                manifest["context"]["mrna_manufacturing_method"],
                "in_vitro_transcription",
            )
            self.assertEqual(manifest["runtime_root"], str(root / "runtime-project"))
            self.assertEqual(manifest["nodes"][CURRENT_STAGE_ID]["status"], "complete")
            self.assertEqual(summary["computational_audit_status"], "pass")
            self.assertEqual(summary["handoff_readiness"], "ready")
            self.assertEqual(workflow["stages"][0]["status"], "complete")
            self.assertEqual(workflow["stages"][1]["status"], "not_evaluated")
            self.assertEqual(latest["run_id"], manifest["run_id"])
            self.assertEqual(latest["verification_status"], "pass")
            self.assertEqual(verify_run(run_dir)["status"], "pass")
            report_html = (node_dir / "report.html").read_text(encoding="utf-8")
            self.assertIn("<!doctype html>", report_html)
            self.assertIn("当前结论 / Conclusions", report_html)
            self.assertIn("do not establish", report_html)

    def test_cli_validate_returns_zero_for_valid_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            config_path = self._write_project(Path(temporary_dir))
            self.assertEqual(cli_main(["validate", str(config_path)]), 0)

    def test_cli_init_refuses_to_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            project_dir = root / "source-project"
            runtime_dir = root / "runtime-project"
            arguments = [
                "init",
                str(project_dir),
                "--runtime-root",
                str(runtime_dir),
            ]
            self.assertEqual(cli_main(arguments), 0)
            self.assertEqual(cli_main(arguments), 1)

    def test_runtime_inside_source_project_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path = self._write_project(root)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["runtime_root"] = str(config_path.parent / "runtime")
            config_path.write_text(json.dumps(config), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "outside the source project"):
                analyze_project(config_path)

    def test_open_human_questions_block_next_node_without_failing_sequence_qc(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path = self._write_project(root)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["context"]["target_indication"] = "unspecified"
            config["human_actions"] = [
                {
                    "action_id": "approve-controls",
                    "question": "Approve immutable source controls.",
                    "required_before_stage": "candidate_specification",
                    "status": "open",
                }
            ]
            config_path.write_text(json.dumps(config), encoding="utf-8")
            analysis = analyze_project(config_path)
            run_dir = write_run_artifacts(
                analysis,
                now=datetime(2026, 7, 13, 13, 0, tzinfo=timezone.utc),
            )
            node_dir = run_dir / "nodes" / CURRENT_STAGE_ID
            summary = json.loads((node_dir / "summary.json").read_text(encoding="utf-8"))
            handoff = json.loads((node_dir / "handoff.json").read_text(encoding="utf-8"))

            self.assertEqual(analysis.status, "pass")
            self.assertEqual(summary["status"], "needs_human_input")
            self.assertEqual(handoff["readiness"], "needs_human_input")
            self.assertEqual(handoff["source_node_artifacts"]["input_audit"], "input_audit.json")
            self.assertEqual(handoff["source_node_artifacts"]["output_audit"], "output_audit.json")
            self.assertEqual(
                set(handoff["blocking_action_ids"]),
                {"approve-controls", "define-target-indication"},
            )

    def test_draft_design_contract_blocks_candidate_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path = self._write_project(root)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            brief_path = Path(config["runtime_root"]) / config["inputs"]["design_brief"]
            brief = json.loads(brief_path.read_text(encoding="utf-8"))
            brief["status"] = "draft"
            brief_path.write_text(json.dumps(brief), encoding="utf-8")

            run_dir = write_run_artifacts(
                analyze_project(config_path),
                now=datetime(2026, 7, 13, 13, 30, tzinfo=timezone.utc),
            )
            node_dir = run_dir / "nodes" / CURRENT_STAGE_ID
            summary = json.loads((node_dir / "summary.json").read_text(encoding="utf-8"))
            input_audit = json.loads(
                (node_dir / "input_audit.json").read_text(encoding="utf-8")
            )
            handoff = json.loads((node_dir / "handoff.json").read_text(encoding="utf-8"))

            self.assertEqual(summary["status"], "needs_human_input")
            self.assertIn("approve-design-round-contract", handoff["blocking_action_ids"])
            contract_check = next(
                check
                for check in input_audit["checks"]
                if check["check_id"] == "design-round-contract-approved"
            )
            self.assertEqual(contract_check["status"], "warning")

    def test_artifact_hash_detects_modified_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            analysis = analyze_project(self._write_project(root))
            run_dir = write_run_artifacts(
                analysis,
                now=datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc),
            )
            output_path = run_dir / "nodes" / CURRENT_STAGE_ID / "output_audit.json"
            output_path.write_text(output_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

            result = verify_run(run_dir)

            self.assertEqual(result["status"], "fail")
            self.assertTrue(
                any("artifact-integrity:" in error for error in result["errors"]),
                result["errors"],
            )

    def test_design_round_tampering_fails_after_reindex(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            run_dir = write_run_artifacts(
                analyze_project(self._write_project(root)),
                now=datetime(2026, 7, 13, 14, 15, tzinfo=timezone.utc),
            )
            path = run_dir / "nodes/program_and_source_intake/design_round.json"
            document = json.loads(path.read_text(encoding="utf-8"))
            document["summary"]["searchable_variable_count"] = 999
            path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            rebuilt = build_artifact_index(
                run_dir,
                manifest["project_id"],
                manifest["run_id"],
            )
            (run_dir / "artifact_index.json").write_text(
                json.dumps(rebuilt, indent=2) + "\n",
                encoding="utf-8",
            )

            verification = verify_run(run_dir)

            self.assertEqual(verification["status"], "fail")
            self.assertTrue(
                any("design-round-contract" in error for error in verification["errors"])
            )

    def test_failed_sequence_audit_produces_a_valid_blocked_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path = self._write_project(root)
            cds_path = root / "runtime-project" / "input" / "proteins_cds.fasta"
            cds_path.write_text(
                ">protein_1\nATGGGTGCTTAA\n"
                ">protein_2\nATGAAATTTTGA\n"
                ">protein_3\nATGGGTCCTTAG\n",
                encoding="utf-8",
            )
            analysis = analyze_project(config_path)
            run_dir = write_run_artifacts(
                analysis,
                now=datetime(2026, 7, 13, 14, 30, tzinfo=timezone.utc),
            )
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))

            self.assertEqual(analysis.status, "fail")
            self.assertEqual(manifest["status"], "blocked")
            self.assertEqual(verify_run(run_dir)["status"], "pass")

    def test_semantic_verifier_detects_candidate_mismatch_even_after_reindex(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            analysis = analyze_project(self._write_project(root))
            run_dir = write_run_artifacts(
                analysis,
                now=datetime(2026, 7, 13, 15, 0, tzinfo=timezone.utc),
            )
            output_path = run_dir / "nodes" / CURRENT_STAGE_ID / "output_audit.json"
            output = json.loads(output_path.read_text(encoding="utf-8"))
            output["candidates"][0]["candidate_id"] = "candidate-wrong"
            output_path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            index = build_artifact_index(run_dir, manifest["project_id"], manifest["run_id"])
            (run_dir / "artifact_index.json").write_text(
                json.dumps(index, indent=2) + "\n",
                encoding="utf-8",
            )

            result = verify_run(run_dir)

            self.assertEqual(result["status"], "fail")
            self.assertTrue(
                any("candidate-cross-reference" in error for error in result["errors"]),
                result["errors"],
            )

    def test_latest_is_not_published_when_verification_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            analysis = analyze_project(self._write_project(root))
            with patch(
                "design_flow.reporting.verify_run",
                return_value={"status": "fail", "errors": ["forced verification failure"]},
            ):
                with self.assertRaisesRegex(ValueError, "latest was not updated"):
                    write_run_artifacts(
                        analysis,
                        now=datetime(2026, 7, 13, 16, 0, tzinfo=timezone.utc),
                    )

            self.assertFalse((analysis.config.run_root / "latest.json").exists())

    def test_verify_run_cli_returns_success_for_valid_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            analysis = analyze_project(self._write_project(root))
            run_dir = write_run_artifacts(
                analysis,
                now=datetime(2026, 7, 13, 17, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(cli_main(["verify-run", str(run_dir)]), 0)


class CandidateStageEndToEndTests(unittest.TestCase):
    @staticmethod
    def _fake_pdb(sequence: str) -> str:
        names = {
            "A": "ALA", "R": "ARG", "N": "ASN", "D": "ASP", "C": "CYS",
            "Q": "GLN", "E": "GLU", "G": "GLY", "H": "HIS", "I": "ILE",
            "L": "LEU", "K": "LYS", "M": "MET", "F": "PHE", "P": "PRO",
            "S": "SER", "T": "THR", "W": "TRP", "Y": "TYR", "V": "VAL",
        }
        lines = []
        for index, amino_acid in enumerate(sequence, 1):
            x = (index - 1) * 3.8
            y = math.sin(index / 3.0) * 2.0
            z = math.cos(index / 4.0) * 1.5
            lines.append(
                f"ATOM  {index:5d}  CA  {names[amino_acid]:>3s} A{index:4d}    "
                f"{x:8.3f}{y:8.3f}{z:8.3f}{1.0:6.2f}{85.0:6.2f}           C\n"
            )
        return "".join(lines) + "END\n"

    def _write_stage3_result_archive(self, job_dir: Path, archive_path: Path) -> Path:
        job = json.loads((job_dir / "job-manifest.json").read_text(encoding="utf-8"))
        result_dir = archive_path.parent / "fake-results"
        result_dir.mkdir()
        run_manifest = {
            "schema_version": "vaxflow.esmfold2-run.v1",
            "run_identity": "pending",
            "created_at_utc": "2026-07-14T15:00:00+00:00",
            "job_identity": job["job_identity"],
            "job_manifest_sha256": _document_sha256(job),
            "source": job["source"],
            "runtime_identity": "fixture-runtime",
            "model": job["model"],
            "weight_files": [],
            "execution": {**job["execution"], "device": "cuda:0", "sequential": True},
            "candidate_ids": [record["candidate_id"] for record in job["records"]],
            "limitations": ["fixture exploratory result"],
        }
        run_manifest["run_identity"] = _identity(run_manifest, "run_identity")
        (result_dir / "run-manifest.json").write_text(
            json.dumps(run_manifest, sort_keys=True), encoding="utf-8"
        )
        result_paths = []
        for record in job["records"]:
            candidate_id = record["candidate_id"]
            record_dir = result_dir / "records" / candidate_id
            record_dir.mkdir(parents=True)
            pdb_path = record_dir / "prediction.pdb"
            pdb_path.write_text(self._fake_pdb(record["sequence"]), encoding="ascii")
            result = {
                "schema_version": "vaxflow.esmfold2-result.v1",
                "run_identity": run_manifest["run_identity"],
                "candidate_id": candidate_id,
                "candidate_key": record["candidate_key"],
                "sequence_sha256": record["sequence_sha256"],
                "length": record["length"],
                "status": "succeeded",
                "seed": 42,
                "started_at_utc": "2026-07-14T15:00:00+00:00",
                "finished_at_utc": "2026-07-14T15:00:01+00:00",
                "runtime_seconds": 1.0,
                "parameters": job["execution"]["parameters"],
                "metrics": {"mean_plddt": 0.85, "ptm": 0.75},
                "peak_gpu_memory_allocated_bytes": 1024,
                "peak_gpu_memory_reserved_bytes": 2048,
                "artifact": {
                    "path": f"records/{candidate_id}/prediction.pdb",
                    "media_type": "chemical/x-pdb",
                    "bytes": pdb_path.stat().st_size,
                    "sha256": hashlib.sha256(pdb_path.read_bytes()).hexdigest(),
                },
            }
            (record_dir / "result.json").write_text(
                json.dumps(result, sort_keys=True), encoding="utf-8"
            )
            result_paths.append(f"records/{candidate_id}/result.json")
        summary = {
            "schema_version": "vaxflow.esmfold2-summary.v1",
            "run_identity": run_manifest["run_identity"],
            "updated_at_utc": "2026-07-14T15:01:00+00:00",
            "status": "passed",
            "records": {
                "selected": len(job["records"]),
                "succeeded": len(job["records"]),
                "failed": 0,
                "pending": 0,
            },
            "timing": {
                "model_load_seconds_this_process": 1.0,
                "model_load_seconds_max_observed": 1.0,
                "record_runtime_seconds": float(len(job["records"])),
                "mean_seconds_per_success": 1.0,
            },
            "peak_gpu_memory_allocated_bytes": 1024,
            "peak_gpu_memory_reserved_bytes": 2048,
            "result_paths": result_paths,
        }
        (result_dir / "summary.json").write_text(
            json.dumps(summary, sort_keys=True), encoding="utf-8"
        )
        with tarfile.open(archive_path, "w:gz") as archive:
            for path in sorted(result_dir.rglob("*")):
                if path.is_file():
                    archive.add(path, arcname=path.relative_to(result_dir).as_posix())
        return archive_path

    def _write_stage2_project(self, root: Path) -> tuple[Path, Path]:
        source_dir = root / "source-project"
        runtime_dir = root / "runtime-project"
        input_dir = runtime_dir / "input"
        manual_dir = input_dir / "manual"
        manual_dir.mkdir(parents=True)
        source_aa = {
            "A": "MAAAAAAAA",
            "B": "MCCCCCCCC",
            "C": "MGGGGGGGG",
        }
        source_cds = {
            "A": "ATG" + "GCT" * 8 + "TAA",
            "B": "ATG" + "TGT" * 8 + "TAA",
            "C": "ATG" + "GGT" * 8 + "TAA",
        }
        (input_dir / "proteins_aa.fasta").write_text(
            "".join(f">{key}\n{value}\n" for key, value in source_aa.items()),
            encoding="utf-8",
        )
        (input_dir / "proteins_cds.fasta").write_text(
            "".join(f">{key}\n{value}\n" for key, value in source_cds.items()),
            encoding="utf-8",
        )
        manual_records = {
            "trunc-a.aa.fasta": ">trunc-a\nAAAAAAAA\n",
            "trunc-a.cds.fasta": ">trunc-a\n" + "GCT" * 8 + "TAA\n",
            "trunc-b.aa.fasta": ">trunc-b\nCCCCCCCC\n",
            "trunc-b.cds.fasta": ">trunc-b\n" + "TGT" * 8 + "TAA\n",
            "fusion.aa.fasta": ">fusion\nCCCCCCCCAAAAAAAA\n",
            "fusion.cds.fasta": ">fusion\nATG" + "TGT" * 8 + "GCT" * 8 + "TAA\n",
        }
        for name, content in manual_records.items():
            (manual_dir / name).write_text(content, encoding="utf-8")
        specification = {
            "schema_version": 1,
            "specification_id": "test-stage2-v1",
            "batch_label": "test candidates",
            "release_mode": "provisional",
            "include_source_controls": ["A", "B", "C"],
            "manual_candidates": [
                {
                    "candidate_key": "trunc-a",
                    "candidate_type": "truncation",
                    "amino_acid_fasta": "input/manual/trunc-a.aa.fasta",
                    "nucleotide_fasta": "input/manual/trunc-a.cds.fasta",
                    "claimed_source_id": "A",
                    "claimed_source_start": 2,
                    "claimed_source_end": 9,
                    "annotation_status": "unreviewed",
                },
                {
                    "candidate_key": "trunc-b",
                    "candidate_type": "truncation",
                    "amino_acid_fasta": "input/manual/trunc-b.aa.fasta",
                    "nucleotide_fasta": "input/manual/trunc-b.cds.fasta",
                    "claimed_source_id": "B",
                    "claimed_source_start": 2,
                    "claimed_source_end": 9,
                    "annotation_status": "unreviewed",
                },
                {
                    "candidate_key": "fusion-ba",
                    "candidate_type": "fusion",
                    "amino_acid_fasta": "input/manual/fusion.aa.fasta",
                    "nucleotide_fasta": "input/manual/fusion.cds.fasta",
                    "claimed_component_keys": ["trunc-a", "trunc-b"],
                    "annotation_status": "unreviewed",
                },
            ],
            "generation_grammar": {
                "status": "draft",
                "generate_new_candidates": False,
                "structure_max_length": 1024,
            },
        }
        specification_path = input_dir / "candidate_specification.json"
        specification_path.write_text(json.dumps(specification), encoding="utf-8")
        _write_design_inputs(runtime_dir, project_id="test-stage2")
        config = {
            "schema_version": 1,
            "project_id": "test-stage2",
            "expected_protein_count": 3,
            "runtime_root": str(runtime_dir),
            "inputs": {
                "amino_acid_fasta": "input/proteins_aa.fasta",
                "nucleotide_fasta": "input/proteins_cds.fasta",
                "candidate_specification": "input/candidate_specification.json",
                **DESIGN_INPUT_PATHS,
            },
            "outputs": {"run_root": "runs"},
            "context": {
                "target_indication": "test indication",
                "intended_host_species": "test host",
                "product_modalities": ["recombinant_protein", "mrna"],
                "protein_expression_host": "test expression host",
                "mrna_target_species": "test host",
            },
        }
        source_dir.mkdir(parents=True)
        config_path = source_dir / "project.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        source_run = write_run_artifacts(
            analyze_project(config_path),
            now=datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc),
        )
        return config_path, source_run

    def _write_verified_stage3_run(
        self,
        root: Path,
        config_path: Path,
        source_run: Path,
        *,
        hour: int = 8,
    ) -> Path:
        candidate_run = write_candidate_run(
            analyze_candidate_specification(config_path, source_run_dir=source_run),
            now=datetime(2026, 7, 15, hour, 0, tzinfo=timezone.utc),
        )
        prepared = write_structure_job(
            config_path,
            source_run_dir=candidate_run,
            output_root=root / f"transfer-{hour}",
            created_at=datetime(2026, 7, 15, hour, 10, tzinfo=timezone.utc),
        )
        archive = self._write_stage3_result_archive(
            Path(prepared["job_dir"]), root / f"stage3-results-{hour}.tar.gz"
        )
        return write_structure_run(
            analyze_structure_results(
                config_path,
                result_archive=archive,
                source_run_dir=candidate_run,
                job_dir=Path(prepared["job_dir"]),
            ),
            now=datetime(2026, 7, 15, hour, 20, tzinfo=timezone.utc),
        )

    @staticmethod
    def _write_empty_residue_evidence(
        path: Path,
        *,
        adapter_id: str,
        candidate_batch_sha256: str,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema_version": "vaxflow.residue-evidence.v1",
                    "adapter_id": adapter_id,
                    "candidate_batch_sha256": candidate_batch_sha256,
                    "tool": {
                        "name": f"fixture-{adapter_id}",
                        "version": "1.0.0",
                        "revision": "fixture-revision",
                    },
                    "observations": [],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _write_codon_usage(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema_version": "vaxflow.codon-usage.v1",
                    "species": "fixture-host",
                    "provenance": {
                        "source": "fixture",
                        "version": "1",
                        "revision": "fixture-revision",
                    },
                    "codon_frequencies": {
                        codon: float(index + 1)
                        for index, (codon, amino_acid) in enumerate(sorted(CODON_TABLE.items()))
                        if amino_acid != "*"
                    },
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _write_empty_product_evidence(
        path: Path,
        *,
        schema_version: str,
        adapter_id: str,
        binding_field: str,
        binding_sha256: str,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema_version": schema_version,
                    "adapter_id": adapter_id,
                    binding_field: binding_sha256,
                    "tool": {
                        "name": f"fixture-{adapter_id}",
                        "version": "1.0.0",
                        "revision": "fixture-revision",
                    },
                    "observations": [],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def test_stage2_infers_actual_component_order_and_writes_verified_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            config_path, source_run = self._write_stage2_project(Path(temporary_dir))
            analysis = analyze_candidate_specification(
                config_path,
                source_run_dir=source_run,
            )
            fusion = next(
                candidate for candidate in analysis.candidates if candidate.candidate_key == "fusion-ba"
            )

            self.assertEqual(fusion.observed_component_keys, ["trunc-b", "trunc-a"])
            self.assertTrue(
                any(issue.code == "claimed_component_order_mismatch" for issue in fusion.issues)
            )
            self.assertEqual(fusion.release_status, "quarantined")
            candidate_run = write_candidate_run(
                analysis,
                now=datetime(2026, 7, 14, 9, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(verify_run(candidate_run)["status"], "pass")
            self.assertEqual(verify_run(source_run)["status"], "pass")
            summary = json.loads(
                (candidate_run / "nodes" / "candidate_specification" / "summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(summary["candidate_count"], 6)
            self.assertEqual(summary["exploratory_structure_ready_count"], 6)
            self.assertEqual(summary["formal_structure_ready_count"], 3)

    def test_stage2_rejects_draft_design_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path, _ = self._write_stage2_project(root)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            brief_path = Path(config["runtime_root"]) / config["inputs"]["design_brief"]
            brief = json.loads(brief_path.read_text(encoding="utf-8"))
            brief["status"] = "draft"
            brief_path.write_text(json.dumps(brief), encoding="utf-8")
            source_run = write_run_artifacts(
                analyze_project(config_path),
                now=datetime(2026, 7, 14, 8, 1, tzinfo=timezone.utc),
            )

            with self.assertRaisesRegex(ValueError, "design-round contract is approved"):
                analyze_candidate_specification(
                    config_path,
                    source_run_dir=source_run,
                )

    def test_stage2_rejects_unknown_proposal_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path, source_run = self._write_stage2_project(root)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            specification_path = (
                Path(config["runtime_root"])
                / config["inputs"]["candidate_specification"]
            )
            specification = json.loads(specification_path.read_text(encoding="utf-8"))
            specification["manual_candidates"][0]["proposal"] = {
                "parent_candidate_keys": ["missing-parent"]
            }
            specification_path.write_text(json.dumps(specification), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "unknown proposal parents"):
                analyze_candidate_specification(
                    config_path,
                    source_run_dir=source_run,
                )

    def test_stage2_rejects_cyclic_proposal_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path, source_run = self._write_stage2_project(root)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            specification_path = (
                Path(config["runtime_root"])
                / config["inputs"]["candidate_specification"]
            )
            specification = json.loads(specification_path.read_text(encoding="utf-8"))
            specification["manual_candidates"][0]["proposal"] = {
                "parent_candidate_keys": ["trunc-b"]
            }
            specification["manual_candidates"][1]["proposal"] = {
                "parent_candidate_keys": ["trunc-a"]
            }
            specification_path.write_text(json.dumps(specification), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "proposal lineage contains a cycle"):
                analyze_candidate_specification(
                    config_path,
                    source_run_dir=source_run,
                )

    def test_stage2_proposal_generator_materializes_and_verifies_inline_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path, source_run = self._write_stage2_project(root)
            seed_run = write_candidate_run(
                analyze_candidate_specification(
                    config_path,
                    source_run_dir=source_run,
                ),
                now=datetime(2026, 7, 14, 8, 2, tzinfo=timezone.utc),
            )
            grammar_path = root / "proposal-grammar.json"
            grammar_path.write_text(
                json.dumps(
                    {
                        "schema_version": "vaxflow.stage2-proposal-grammar.v1",
                        "grammar_id": "fixture-pairwise-v1",
                        "project_id": "test-stage2",
                        "design_round_id": "round-000",
                        "status": "approved_for_mock_execution",
                        "consumed_feedback_request_ids": [],
                        "linkers": [
                            {
                                "linker_id": "direct",
                                "sequence": "",
                                "class": "control",
                                "rationale": "Direct concatenation control.",
                            },
                            {
                                "linker_id": "flex5",
                                "sequence": "GGGGS",
                                "class": "flexible",
                                "rationale": "Fixture flexible linker.",
                            },
                        ],
                        "composition_templates": [
                            {
                                "template_id": "pair-ab",
                                "component_slots": [
                                    {
                                        "slot_id": "a",
                                        "candidate_keys": ["trunc-a"],
                                    },
                                    {
                                        "slot_id": "b",
                                        "candidate_keys": ["trunc-b"],
                                    },
                                ],
                                "order_policy": "all_permutations",
                                "linker_ids": ["direct", "flex5"],
                                "rationale": "Exercise pairwise fixture generation.",
                            },
                            {
                                "template_id": "source-pair",
                                "component_slots": [
                                    {
                                        "slot_id": "source-a",
                                        "candidate_keys": ["source-A"],
                                    },
                                    {
                                        "slot_id": "source-b",
                                        "candidate_keys": ["source-B"],
                                    },
                                ],
                                "order_policy": "fixed",
                                "linker_ids": ["direct"],
                                "rationale": "Exercise immutable source parents.",
                            },
                        ],
                        "constraints": {
                            "maximum_aa_length": 1024,
                            "maximum_generated_candidates": 10,
                        },
                        "model_roles": [],
                    }
                ),
                encoding="utf-8",
            )

            generated = write_proposal_generation(
                config_path,
                grammar_path=grammar_path,
                seed_run_dir=seed_run,
            )
            proposal_dir = Path(generated["output_dir"])
            proposal_batch = json.loads(
                (proposal_dir / "proposal_batch.json").read_text(encoding="utf-8")
            )
            expanded = analyze_candidate_specification(
                config_path,
                source_run_dir=source_run,
                specification_path=Path(generated["candidate_specification"]),
            )

            self.assertEqual(generated["generated_candidates"], 4)
            self.assertEqual(generated["skipped_candidates"], 1)
            self.assertEqual(generated["total_candidates"], 10)
            self.assertEqual(
                proposal_batch["skipped_candidates"][0]["duplicate_of"],
                "fusion-ba",
            )
            self.assertEqual(verify_proposal_generation(proposal_dir)["status"], "pass")
            self.assertIn(
                "系统从 6 条冻结 seed 出发",
                (proposal_dir / "report.html").read_text(encoding="utf-8"),
            )
            self.assertEqual(expanded.computational_status, "pass")
            self.assertEqual(len(expanded.candidates), 10)
            linked = next(
                candidate
                for candidate in expanded.candidates
                if candidate.proposal["generator"]["id"]
                == "deterministic-combinatorial-enumerator"
                and candidate.proposal["generator"]["parameters"]["linker_id"]
                == "flex5"
            )
            self.assertEqual(linked.observed_component_keys, ["trunc-a", "trunc-b"])
            self.assertEqual(
                [
                    component.get("declared_role")
                    for component in linked.inferred_components
                ],
                [None, "linker", None],
            )
            source_pair = next(
                candidate
                for candidate in expanded.candidates
                if candidate.proposal["generator"]["id"]
                == "deterministic-combinatorial-enumerator"
                and candidate.proposal["generator"]["parameters"]["template_id"]
                == "source-pair"
            )
            self.assertEqual(
                source_pair.observed_component_keys,
                ["source-A", "source-B"],
            )
            self.assertEqual(
                [
                    component["source_protein_id"]
                    for component in source_pair.inferred_components
                ],
                ["A", "B"],
            )

            (proposal_dir / "proposals.csv").write_text("tampered\n", encoding="utf-8")
            verification = verify_proposal_generation(proposal_dir)
            self.assertEqual(verification["status"], "fail")
            self.assertTrue(
                any("Proposal CSV" in error for error in verification["errors"]),
                verification["errors"],
            )

    def test_stage2_supports_independent_linkers_and_constrained_substitution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path, source_run = self._write_stage2_project(root)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            specification_path = (
                Path(config["runtime_root"])
                / config["inputs"]["candidate_specification"]
            )
            specification = json.loads(specification_path.read_text(encoding="utf-8"))
            parent_sequence = "AAAAAAAA" + "GS" + "CCCCCCCC" + "EAAAK" + "MGGGGGGGG"
            child_sequence = parent_sequence[:8] + "AS" + parent_sequence[10:]
            specification["manual_candidates"].extend(
                [
                    {
                        "candidate_key": "fusion-independent-linkers",
                        "candidate_type": "fusion",
                        "amino_acid_sequence": parent_sequence,
                        "claimed_component_keys": ["trunc-a", "trunc-b", "source-C"],
                        "annotation_status": "unreviewed",
                        "proposal": {
                            "generator": {
                                "id": "fixture-multilinker",
                                "version": "1",
                                "parameters": {
                                    "linker_ids": ["short", "rigid"],
                                    "linker_sequences": ["GS", "EAAAK"],
                                },
                            },
                            "parent_candidate_keys": ["trunc-a", "trunc-b", "source-C"],
                            "transformation": "ordered_component_concatenation",
                            "rationale": "Exercise independent junction linkers.",
                            "feedback_request_ids": [],
                        },
                    },
                    {
                        "candidate_key": "fusion-model-child",
                        "candidate_type": "fusion",
                        "amino_acid_sequence": child_sequence,
                        "claimed_component_keys": ["fusion-independent-linkers"],
                        "annotation_status": "unreviewed",
                        "proposal": {
                            "generator": {
                                "id": "fixture-model",
                                "version": "model-revision",
                                "parameters": {
                                    "mutations": [
                                        {"position": 9, "from": "G", "to": "A"}
                                    ]
                                },
                            },
                            "parent_candidate_keys": ["fusion-independent-linkers"],
                            "transformation": "constrained_substitution",
                            "rationale": "Exercise validated model substitution.",
                            "feedback_request_ids": [],
                        },
                    },
                ]
            )
            specification_path.write_text(json.dumps(specification), encoding="utf-8")

            analysis = analyze_candidate_specification(
                config_path,
                source_run_dir=source_run,
            )

            parent = next(
                candidate
                for candidate in analysis.candidates
                if candidate.candidate_key == "fusion-independent-linkers"
            )
            child = next(
                candidate
                for candidate in analysis.candidates
                if candidate.candidate_key == "fusion-model-child"
            )
            self.assertEqual(parent.observed_component_keys, ["trunc-a", "trunc-b", "source-C"])
            self.assertEqual(
                [
                    component.get("linker_id")
                    for component in parent.inferred_components
                    if component.get("declared_role") == "linker"
                ],
                ["short", "rigid"],
            )
            redesigned = [
                component
                for component in child.inferred_components
                if component.get("sequence_relation") == "constrained_substitution"
            ]
            self.assertEqual(len(redesigned), 1)
            self.assertEqual(redesigned[0]["mutations"], [{"position": 9, "from": "G", "to": "A"}])
            self.assertEqual(child.observed_component_keys, ["fusion-independent-linkers"])
            child_run = write_candidate_run(
                analysis,
                now=datetime(2026, 7, 14, 8, 5, tzinfo=timezone.utc),
            )
            self.assertEqual(verify_run(child_run)["status"], "pass")
            batch_path = child_run / "nodes/candidate_specification/candidate_batch.json"
            batch = json.loads(batch_path.read_text(encoding="utf-8"))
            child_document = next(
                candidate
                for candidate in batch["candidates"]
                if candidate["candidate_key"] == "fusion-model-child"
            )
            redesigned_component = next(
                component
                for component in child_document["inferred_components"]
                if component.get("sequence_relation") == "constrained_substitution"
            )
            redesigned_component["mutations"][0]["to"] = "C"
            batch_path.write_text(json.dumps(batch, indent=2), encoding="utf-8")
            manifest = json.loads((child_run / "manifest.json").read_text(encoding="utf-8"))
            index = build_artifact_index(child_run, manifest["project_id"], manifest["run_id"])
            (child_run / "artifact_index.json").write_text(
                json.dumps(index, indent=2), encoding="utf-8"
            )
            tampered = verify_run(child_run)
            self.assertEqual(tampered["status"], "fail")
            self.assertTrue(
                any("component-maps-cover-sequences" in error for error in tampered["errors"])
            )

    def test_external_model_proposals_enforce_residue_masks(self) -> None:
        job = {
            "search_identity": "search-fixture",
            "job_id": "esm3-fixture",
            "job_identity": "job-fixture",
            "adapter_id": "esm3-fixture",
            "model": {"name": "fixture", "revision": "revision"},
            "variants_per_parent": 2,
            "records": [
                {
                    "parent_candidate_key": "parent",
                    "sequence": "AAAA",
                    "sequence_sha256": hashlib.sha256(b"AAAA").hexdigest(),
                    "mutable_positions": [2],
                    "protected_positions": [3],
                    "maximum_substitutions": 1,
                }
            ],
        }
        results = {
            "schema_version": "vaxflow.stage2-external-proposals.v1",
            "search_identity": "search-fixture",
            "job_id": "esm3-fixture",
            "job_identity": "job-fixture",
            "adapter_id": "esm3-fixture",
            "model": {"name": "fixture", "revision": "revision"},
            "records": [
                {
                    "parent_candidate_key": "parent",
                    "amino_acid_sequence": "ACAA",
                    "model_score": 0.5,
                }
            ],
        }

        accepted, skipped = _validate_results(
            results,
            job,
            {"parent": "AAAA"},
            {hashlib.sha256(b"AAAA").hexdigest(): "parent"},
        )

        self.assertEqual(len(accepted), 1)
        self.assertEqual(skipped, [])
        self.assertEqual(accepted[0]["mutations"], [{"position": 2, "from": "A", "to": "C"}])
        invalid = json.loads(json.dumps(results))
        invalid["records"][0]["amino_acid_sequence"] = "AACA"
        with self.assertRaisesRegex(ValueError, "outside the declared mask"):
            _validate_results(
                invalid,
                job,
                {"parent": "AAAA"},
                {hashlib.sha256(b"AAAA").hexdigest(): "parent"},
            )

    def test_stage2_search_preserves_baseline_without_exhausting_stage3_budget(self) -> None:
        seed_candidates = []
        for index in range(9):
            sequence = "A" * (10 + index)
            seed_candidates.append(
                {
                    "candidate_key": f"seed-{index}",
                    "candidate_type": "source_control" if index < 3 else "fusion",
                    "amino_acid_sequence": sequence,
                    "amino_acid_sha256": hashlib.sha256(sequence.encode("ascii")).hexdigest(),
                    "duplicate_of": None,
                    "proposal": {
                        "generator": {
                            "id": "source_intake" if index < 3 else "manual_import",
                            "parameters": {},
                        }
                    },
                }
            )
        for index in range(12):
            sequence = "C" * (20 + index)
            seed_candidates.append(
                {
                    "candidate_key": f"baseline-{index}",
                    "candidate_type": "fusion",
                    "amino_acid_sequence": sequence,
                    "amino_acid_sha256": hashlib.sha256(sequence.encode("ascii")).hexdigest(),
                    "duplicate_of": None,
                    "proposal": {
                        "generator": {
                            "id": "deterministic-combinatorial-enumerator",
                            "parameters": {
                                "template_id": f"template-{index % 3}",
                                "linker_id": f"linker-{index % 2}",
                            },
                        }
                    },
                }
            )
        atomic = [
            {
                "candidate_key": f"atomic-{index}",
                "amino_acid_sha256": hashlib.sha256(f"atomic-{index}".encode()).hexdigest(),
                "aa_length": 50,
                "features": {"atomic_priority_proxy": 0.5},
            }
            for index in range(3)
        ]
        fusions = [
            {
                "candidate_key": f"fusion-{index}",
                "amino_acid_sha256": hashlib.sha256(f"fusion-{index}".encode()).hexdigest(),
                "aa_length": 100,
                "template_id": f"template-{index % 2}",
                "ordered_source_ids": ["A", "B"],
                "linker_classes": [f"class-{index % 2}"],
                "features": {"fusion_priority_proxy": 1.0 - index / 100.0},
            }
            for index in range(20)
        ]
        selection = _selection_records(
            {"candidates": seed_candidates},
            atomic,
            fusions,
            {
                "project_id": "fixture",
                "design_round_id": "round-000",
                "budgets": {
                    "maximum_stage3_candidates": 20,
                    "maximum_baseline_generated_stage3_candidates": 4,
                },
            },
            "search-fixture",
        )

        tiers = [record["selection_tier"] for record in selection["records"]]
        self.assertEqual(len(tiers), 20)
        self.assertEqual(tiers.count("baseline_source_or_manual"), 9)
        self.assertEqual(tiers.count("baseline_generated_panel"), 4)
        self.assertEqual(tiers.count("atomic_boundary_panel"), 3)
        self.assertEqual(tiers.count("multifamily_fusion_panel"), 4)

    def test_external_model_import_materializes_self_verifying_specification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path, _ = self._write_stage2_project(root)
            search = root / "fixture-search"
            (search / "inputs").mkdir(parents=True)
            parent_sequence = "AAAAAAAA"
            parent_sha = hashlib.sha256(parent_sequence.encode("ascii")).hexdigest()
            base_specification = {
                "schema_version": 1,
                "specification_id": "fixture-search-spec",
                "batch_label": "fixture",
                "design_round_id": "round-000",
                "release_mode": "provisional",
                "include_source_controls": [],
                "manual_candidates": [
                    {
                        "candidate_key": "parent",
                        "candidate_type": "fusion",
                        "amino_acid_sequence": parent_sequence,
                        "claimed_component_keys": [],
                        "annotation_status": "unreviewed",
                        "proposal": {
                            "generator": {"id": "fixture", "version": "1", "parameters": {}},
                            "parent_candidate_keys": [],
                            "transformation": "fixture",
                            "rationale": "fixture",
                            "feedback_request_ids": [],
                        },
                    }
                ],
                "generation_grammar": {
                    "status": "approved",
                    "generate_new_candidates": False,
                    "structure_max_length": 1024,
                },
            }
            seed = {
                "design_round_id": "round-000",
                "candidates": [
                    {
                        "candidate_key": "parent",
                        "amino_acid_sequence": parent_sequence,
                        "amino_acid_sha256": parent_sha,
                    }
                ],
            }
            pool = {
                "schema_version": "vaxflow.stage2-search-pool.v1",
                "search_identity": "fixture-search",
                "records": [],
                "statistics": {},
            }
            job = {
                "job_id": "esm3-fixture",
                "adapter_id": "esm3-fixture",
                "model": {"name": "fixture", "revision": "revision"},
                "status": "ready_for_external_execution",
                "search_identity": "fixture-search",
                "transformation": "constrained_substitution",
                "variants_per_parent": 2,
                "records": [
                    {
                        "parent_candidate_key": "parent",
                        "sequence": parent_sequence,
                        "sequence_sha256": parent_sha,
                        "mutable_positions": [2],
                        "protected_positions": [],
                        "maximum_substitutions": 1,
                    }
                ],
                "result_schema": "vaxflow.stage2-external-proposals.v1",
            }
            job["job_identity"] = _document_sha256(job)
            jobs = {
                "schema_version": "vaxflow.stage2-model-job-requests.v1",
                "project_id": "test-stage2",
                "design_round_id": "round-000",
                "search_identity": "fixture-search",
                "jobs": [job],
                "limitations": [],
            }
            results = {
                "schema_version": "vaxflow.stage2-external-proposals.v1",
                "search_identity": "fixture-search",
                "job_id": job["job_id"],
                "job_identity": job["job_identity"],
                "adapter_id": job["adapter_id"],
                "model": job["model"],
                "records": [
                    {
                        "parent_candidate_key": "parent",
                        "amino_acid_sequence": "ACAAAAAA",
                        "model_score": 0.75,
                    }
                ],
            }
            (search / "search_summary.json").write_text(
                json.dumps(
                    {
                        "search_identity": "fixture-search",
                        "project_id": "test-stage2",
                        "design_round_id": "round-000",
                    }
                ),
                encoding="utf-8",
            )
            (search / "candidate_specification.generated.json").write_text(
                json.dumps(base_specification), encoding="utf-8"
            )
            (search / "inputs/seed_candidate_batch.json").write_text(
                json.dumps(seed), encoding="utf-8"
            )
            (search / "candidate_pool.json").write_text(json.dumps(pool), encoding="utf-8")
            (search / "external_model_jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
            (search / "artifact_index.json").write_text("{}\n", encoding="utf-8")
            results_path = root / "results.json"
            results_path.write_text(json.dumps(results), encoding="utf-8")

            with patch(
                "design_flow.stage2_external_proposals.verify_stage2_search",
                return_value={"status": "pass", "errors": []},
            ):
                imported = write_stage2_model_import(
                    config_path,
                    search_dir=search,
                    results_path=results_path,
                    job_id=job["job_id"],
                    output_root=root / "model-imports",
                )

            self.assertEqual(imported["accepted_records"], 1)
            self.assertEqual(imported["skipped_records"], 0)
            self.assertEqual(
                verify_stage2_model_import(imported["output_dir"])["status"],
                "pass",
            )
            expanded = json.loads(
                Path(imported["candidate_specification"]).read_text(encoding="utf-8")
            )
            self.assertEqual(
                expanded["manual_candidates"][-1]["proposal"]["transformation"],
                "constrained_substitution",
            )

    def test_stage2_verifier_detects_component_tampering_after_reindex(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            config_path, source_run = self._write_stage2_project(Path(temporary_dir))
            candidate_run = write_candidate_run(
                analyze_candidate_specification(config_path, source_run_dir=source_run),
                now=datetime(2026, 7, 14, 10, 0, tzinfo=timezone.utc),
            )
            batch_path = candidate_run / "nodes" / "candidate_specification" / "candidate_batch.json"
            batch = json.loads(batch_path.read_text(encoding="utf-8"))
            batch["candidates"][-1]["inferred_components"][0]["source_start"] = 1
            batch_path.write_text(json.dumps(batch, indent=2) + "\n", encoding="utf-8")
            manifest = json.loads((candidate_run / "manifest.json").read_text(encoding="utf-8"))
            index = build_artifact_index(candidate_run, manifest["project_id"], manifest["run_id"])
            (candidate_run / "artifact_index.json").write_text(
                json.dumps(index, indent=2) + "\n",
                encoding="utf-8",
            )

            result = verify_run(candidate_run)

            self.assertEqual(result["status"], "fail")
            self.assertTrue(
                any("component-maps-cover-sequences" in error for error in result["errors"]),
                result["errors"],
            )

    def test_stage2_verifier_detects_proposal_lineage_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            config_path, source_run = self._write_stage2_project(Path(temporary_dir))
            candidate_run = write_candidate_run(
                analyze_candidate_specification(config_path, source_run_dir=source_run),
                now=datetime(2026, 7, 14, 10, 30, tzinfo=timezone.utc),
            )
            path = candidate_run / "nodes/candidate_specification/proposal_lineage.json"
            document = json.loads(path.read_text(encoding="utf-8"))
            document["records"][-1]["generator"]["id"] = "unrecorded-generator"
            path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
            manifest = json.loads((candidate_run / "manifest.json").read_text(encoding="utf-8"))
            rebuilt = build_artifact_index(
                candidate_run,
                manifest["project_id"],
                manifest["run_id"],
            )
            (candidate_run / "artifact_index.json").write_text(
                json.dumps(rebuilt, indent=2) + "\n",
                encoding="utf-8",
            )

            verification = verify_run(candidate_run)

            self.assertEqual(verification["status"], "fail")
            self.assertTrue(
                any("proposal-lineage" in error for error in verification["errors"])
            )

    def test_stage3_job_contains_only_verified_exploratory_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path, source_run = self._write_stage2_project(root)
            candidate_run = write_candidate_run(
                analyze_candidate_specification(config_path, source_run_dir=source_run),
                now=datetime(2026, 7, 14, 11, 0, tzinfo=timezone.utc),
            )
            prepared = write_structure_job(
                config_path,
                source_run_dir=candidate_run,
                output_root=root / "transfer",
                created_at=datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc),
            )
            job = json.loads(Path(prepared["job_manifest"]).read_text(encoding="utf-8"))
            expected_model_inputs = json.loads(
                (
                    candidate_run
                    / "nodes"
                    / "candidate_specification"
                    / "model_inputs.json"
                ).read_text(encoding="utf-8")
            )["models"]["ESMFold2"]["candidate_ids"]

            self.assertEqual(prepared["records"], 6)
            self.assertEqual(
                [record["candidate_id"] for record in job["records"]],
                expected_model_inputs,
            )
            with tarfile.open(prepared["archive"], "r:gz") as archive:
                self.assertEqual(
                    sorted(archive.getnames()),
                    ["job-manifest.json", "sequences.fasta"],
                )
            rebuilt = write_structure_job(
                config_path,
                source_run_dir=candidate_run,
                output_root=root / "transfer",
                created_at=datetime(2026, 7, 14, 13, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(rebuilt["job_identity"], prepared["job_identity"])
            self.assertEqual(rebuilt["archive_sha256"], prepared["archive_sha256"])

    def test_stage3_job_binds_search_selection_and_imports_results(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path, source_run = self._write_stage2_project(root)
            candidate_run = write_candidate_run(
                analyze_candidate_specification(config_path, source_run_dir=source_run),
                now=datetime(2026, 7, 14, 13, 30, tzinfo=timezone.utc),
            )
            batch = json.loads(
                (
                    candidate_run
                    / "nodes/candidate_specification/candidate_batch.json"
                ).read_text(encoding="utf-8")
            )
            selected = batch["candidates"][:2]
            records = [
                {
                    "candidate_key": candidate["candidate_key"],
                    "amino_acid_sha256": candidate["amino_acid_sha256"],
                    "aa_length": len(candidate["amino_acid_sequence"]),
                    "selection_tier": "fixture",
                    "priority_proxy": None,
                }
                for candidate in selected
            ]
            selection = {
                "schema_version": "vaxflow.stage3-selection.v1",
                "selection_id": _document_sha256(
                    {
                        "search_identity": "fixture-search",
                        "records": records,
                        "budget": 2,
                    }
                ),
                "project_id": "test-stage2",
                "design_round_id": batch["design_round_id"],
                "search_identity": "fixture-search",
                "strategy": "fixture",
                "budget": 2,
                "records": records,
                "limitations": [],
            }
            selection_path = root / "stage3-selection.json"
            selection_path.write_text(json.dumps(selection), encoding="utf-8")
            prepared = write_structure_job(
                config_path,
                source_run_dir=candidate_run,
                output_root=root / "selected-transfer",
                created_at=datetime(2026, 7, 14, 13, 45, tzinfo=timezone.utc),
                selection_manifest=selection_path,
            )

            self.assertEqual(prepared["records"], 2)
            with tarfile.open(prepared["archive"], "r:gz") as archive:
                self.assertEqual(
                    sorted(archive.getnames()),
                    ["job-manifest.json", "selection.json", "sequences.fasta"],
                )
            archive = self._write_stage3_result_archive(
                Path(prepared["job_dir"]), root / "selected-stage3-results.tar.gz"
            )
            analysis = analyze_structure_results(
                config_path,
                result_archive=archive,
                source_run_dir=candidate_run,
                job_dir=Path(prepared["job_dir"]),
            )
            self.assertEqual(len(analysis.assessments), 2)

    def test_stage3_job_rejects_candidates_over_backend_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path, source_run = self._write_stage2_project(root)
            candidate_run = write_candidate_run(
                analyze_candidate_specification(config_path, source_run_dir=source_run),
                now=datetime(2026, 7, 14, 14, 0, tzinfo=timezone.utc),
            )
            with self.assertRaisesRegex(ValueError, "exceeds ESMFold2 limit"):
                build_structure_job(
                    config_path,
                    source_run_dir=candidate_run,
                    maximum_sequence_length=8,
                )

    def test_stage3_result_import_writes_semantically_verified_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path, source_run = self._write_stage2_project(root)
            candidate_run = write_candidate_run(
                analyze_candidate_specification(config_path, source_run_dir=source_run),
                now=datetime(2026, 7, 14, 16, 0, tzinfo=timezone.utc),
            )
            prepared = write_structure_job(
                config_path,
                source_run_dir=candidate_run,
                output_root=root / "transfer",
                created_at=datetime(2026, 7, 14, 17, 0, tzinfo=timezone.utc),
            )
            archive = self._write_stage3_result_archive(
                Path(prepared["job_dir"]), root / "stage3-results.tar.gz"
            )
            analysis = analyze_structure_results(
                config_path,
                result_archive=archive,
                source_run_dir=candidate_run,
                job_dir=Path(prepared["job_dir"]),
            )
            structure_run = write_structure_run(
                analysis,
                now=datetime(2026, 7, 14, 18, 0, tzinfo=timezone.utc),
            )

            verification = verify_run(structure_run)
            self.assertEqual(verification["status"], "pass", verification["errors"])
            self.assertEqual(len(analysis.assessments), 6)
            self.assertTrue(
                all(item["confidence_band"] == "higher_confidence" for item in analysis.assessments)
            )
            self.assertTrue(
                (
                    structure_run
                    / "nodes/protein_structure_assessment/report.html"
                ).is_file()
            )
            report_html = (
                structure_run
                / "nodes/protein_structure_assessment/report.html"
            ).read_text(encoding="utf-8")
            self.assertIn("6/6 checksum-bound candidates", report_html)
            self.assertIn("not a calibrated probability or release gate", report_html)
            self.assertIn("Combined band /", report_html)
            self.assertIn("Release state /", report_html)
            self.assertIn("Due now /", report_html)
            self.assertIn("fixture exploratory result", report_html)

    def test_stage3_verifier_recomputes_metrics_after_reindex(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path, source_run = self._write_stage2_project(root)
            candidate_run = write_candidate_run(
                analyze_candidate_specification(config_path, source_run_dir=source_run),
                now=datetime(2026, 7, 14, 19, 0, tzinfo=timezone.utc),
            )
            prepared = write_structure_job(
                config_path,
                source_run_dir=candidate_run,
                output_root=root / "transfer",
                created_at=datetime(2026, 7, 14, 20, 0, tzinfo=timezone.utc),
            )
            archive = self._write_stage3_result_archive(
                Path(prepared["job_dir"]), root / "stage3-results.tar.gz"
            )
            structure_run = write_structure_run(
                analyze_structure_results(
                    config_path,
                    result_archive=archive,
                    source_run_dir=candidate_run,
                    job_dir=Path(prepared["job_dir"]),
                ),
                now=datetime(2026, 7, 14, 21, 0, tzinfo=timezone.utc),
            )
            assessment_path = (
                structure_run
                / "nodes/protein_structure_assessment/structure_assessments.json"
            )
            document = json.loads(assessment_path.read_text(encoding="utf-8"))
            document["assessments"][0]["mean_plddt"] = 1.0
            assessment_path.write_text(
                json.dumps(document, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            manifest = json.loads(
                (structure_run / "manifest.json").read_text(encoding="utf-8")
            )
            rebuilt = build_artifact_index(
                structure_run, manifest["project_id"], manifest["run_id"]
            )
            (structure_run / "artifact_index.json").write_text(
                json.dumps(rebuilt, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            verification = verify_run(structure_run)
            self.assertEqual(verification["status"], "fail")
            self.assertTrue(
                any(
                    "stage3-assessment-reproducibility" in error
                    for error in verification["errors"]
                ),
                verification["errors"],
            )

    def test_stage4_5_missing_inputs_are_explicit_and_run_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path, source_run = self._write_stage2_project(root)
            structure_run = self._write_verified_stage3_run(
                root, config_path, source_run, hour=8
            )

            initialized = initialize_assessment_specifications(
                config_path, source_run_dir=structure_run
            )
            analysis = analyze_post_structure_stages(
                config_path, source_run_dir=structure_run
            )
            continuation = write_post_structure_run(
                analysis,
                now=datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(len(initialized["created"]), 2)
            self.assertEqual(analysis.immune_result["status"], "needs_data")
            self.assertEqual(analysis.developability_result["status"], "needs_data")
            self.assertTrue(analysis.immune_result["requirements"])
            self.assertTrue(analysis.developability_result["requirements"])
            self.assertTrue(
                all(
                    candidate["categories"]["surface_accessibility_proxy"]["status"]
                    == "evaluated"
                    for candidate in analysis.immune_result["candidates"]
                )
            )
            verification = verify_run(continuation)
            self.assertEqual(verification["status"], "pass", verification["errors"])

    def test_stage4_5_reconciles_current_project_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path, _ = self._write_stage2_project(root)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["context"]["protein_expression_host"] = "unspecified"
            config["human_actions"] = [
                {
                    "action_id": "project-review",
                    "question": "Review the project declaration.",
                    "required_before_stage": "integrated_ranking",
                    "status": "open",
                }
            ]
            config_path.write_text(
                json.dumps(config, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            source_run = write_run_artifacts(
                analyze_project(config_path),
                now=datetime(2026, 7, 14, 7, 0, tzinfo=timezone.utc),
            )
            structure_run = self._write_verified_stage3_run(
                root, config_path, source_run, hour=7
            )

            config["context"]["protein_expression_host"] = "CHO cells"
            config["human_actions"][0].update(
                {
                    "status": "resolved",
                    "owner": "project_owner",
                    "resolution": "The current versioned project uses CHO cells.",
                }
            )
            config_path.write_text(
                json.dumps(config, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            initialize_assessment_specifications(
                config_path, source_run_dir=structure_run
            )
            continuation = write_post_structure_run(
                analyze_post_structure_stages(
                    config_path, source_run_dir=structure_run
                ),
                now=datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc),
            )

            manifest = json.loads(
                (continuation / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                manifest["context"]["protein_expression_host"], "CHO cells"
            )
            self.assertEqual(
                (
                    continuation / "inputs/continuation/project.json"
                ).read_bytes(),
                config_path.read_bytes(),
            )
            self.assertEqual(
                (
                    continuation
                    / "inputs/lineage/stage3_parent_project.json"
                ).read_bytes(),
                (structure_run / "inputs/project.json").read_bytes(),
            )
            actions = json.loads(
                (
                    continuation
                    / "nodes/immune_evidence_assessment/human_actions.json"
                ).read_text(encoding="utf-8")
            )["actions"]
            action_by_id = {action["action_id"]: action for action in actions}
            self.assertEqual(action_by_id["project-review"]["status"], "resolved")
            self.assertEqual(
                action_by_id["select-protein-expression-host"]["status"],
                "resolved",
            )
            blocking = json.loads(
                (
                    continuation
                    / "nodes/immune_evidence_assessment/handoff.json"
                ).read_text(encoding="utf-8")
            )["blocking_action_ids"]
            self.assertNotIn("project-review", blocking)
            self.assertNotIn("select-protein-expression-host", blocking)
            verification = verify_run(continuation)
            self.assertEqual(verification["status"], "pass", verification["errors"])

    def test_stage4_5_complete_versioned_inputs_recompute_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path, source_run = self._write_stage2_project(root)
            structure_run = self._write_verified_stage3_run(
                root, config_path, source_run, hour=10
            )
            initialized = initialize_assessment_specifications(
                config_path, source_run_dir=structure_run
            )
            runtime_root = Path(
                json.loads(config_path.read_text(encoding="utf-8"))["runtime_root"]
            )
            batch_path = (
                structure_run
                / "nodes/candidate_specification/candidate_batch.json"
            )
            batch = json.loads(batch_path.read_text(encoding="utf-8"))
            batch_sha = hashlib.sha256(batch_path.read_bytes()).hexdigest()
            source_controls = {
                candidate["inferred_components"][0]["source_protein_id"]: candidate
                for candidate in batch["candidates"]
                if candidate["candidate_type"] == "source_control"
            }

            immune_path = Path(initialized["immune_specification"])
            immune = json.loads(immune_path.read_text(encoding="utf-8"))
            alignment_root = runtime_root / "input/stage4/alignments"
            for source_id, candidate in source_controls.items():
                alignment = alignment_root / f"{source_id}.fasta"
                alignment.parent.mkdir(parents=True, exist_ok=True)
                sequence = candidate["amino_acid_sequence"]
                alignment.write_text(
                    f">reference\n{sequence}\n>panel-2\n{sequence}\n>panel-3\n{sequence}\n",
                    encoding="utf-8",
                )
                immune["pathogen_panel"]["source_alignments"][source_id] = {
                    "alignment_path": str(alignment.relative_to(runtime_root)),
                    "reference_record_id": "reference",
                }
            panel_path = runtime_root / "input/stage4/bola-panel.json"
            panel_path.write_text('{"schema_version":1,"alleles":[]}\n', encoding="utf-8")
            immune["host"].update(
                {
                    "population_status": "approved",
                    "population_description": "fixture population",
                    "mhc_panel_path": str(panel_path.relative_to(runtime_root)),
                }
            )
            immune["pathogen_panel"]["status"] = "approved"
            immune["policy"]["status"] = "approved"
            for adapter_id in ADAPTER_IDS:
                evidence_path = runtime_root / f"input/stage4/evidence/{adapter_id}.json"
                self._write_empty_residue_evidence(
                    evidence_path,
                    adapter_id=adapter_id,
                    candidate_batch_sha256=batch_sha,
                )
                immune["adapters"][adapter_id] = {
                    "status": "provided",
                    "result_path": str(evidence_path.relative_to(runtime_root)),
                }
            immune_path.write_text(
                json.dumps(immune, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )

            developability_path = Path(initialized["developability_specification"])
            developability = json.loads(
                developability_path.read_text(encoding="utf-8")
            )
            developability["expression_context"].update(
                {
                    "status": "approved",
                    "host": "fixture-host",
                    "compartment": "fixture-cytosol",
                    "purification_strategy": "fixture-affinity",
                    "formulation_context": "fixture-buffer",
                }
            )
            developability["policy"]["status"] = "approved"
            for adapter_id in DEVELOPABILITY_ADAPTER_IDS:
                evidence_path = runtime_root / f"input/stage5/evidence/{adapter_id}.json"
                self._write_empty_residue_evidence(
                    evidence_path,
                    adapter_id=adapter_id,
                    candidate_batch_sha256=batch_sha,
                )
                developability["external_adapters"][adapter_id] = {
                    "status": "provided",
                    "result_path": str(evidence_path.relative_to(runtime_root)),
                }
            developability_path.write_text(
                json.dumps(developability, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            analysis = analyze_post_structure_stages(
                config_path, source_run_dir=structure_run
            )
            continuation = write_post_structure_run(
                analysis,
                now=datetime(2026, 7, 15, 11, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(analysis.immune_result["status"], "evaluated")
            self.assertEqual(analysis.developability_result["status"], "evaluated")
            self.assertEqual(analysis.immune_result["requirements"], [])
            self.assertEqual(analysis.developability_result["requirements"], [])
            verification = verify_run(continuation)
            self.assertEqual(verification["status"], "pass", verification["errors"])

    def test_stage5_verifier_detects_semantic_tampering_after_reindex(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path, source_run = self._write_stage2_project(root)
            structure_run = self._write_verified_stage3_run(
                root, config_path, source_run, hour=12
            )
            initialize_assessment_specifications(
                config_path, source_run_dir=structure_run
            )
            continuation = write_post_structure_run(
                analyze_post_structure_stages(
                    config_path, source_run_dir=structure_run
                ),
                now=datetime(2026, 7, 15, 13, 0, tzinfo=timezone.utc),
            )
            result_path = (
                continuation
                / "nodes/developability_assessment/developability_assessments.json"
            )
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["candidates"][0]["descriptors"]["gravy"] = 99.0
            result_path.write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            manifest = json.loads(
                (continuation / "manifest.json").read_text(encoding="utf-8")
            )
            rebuilt = build_artifact_index(
                continuation, manifest["project_id"], manifest["run_id"]
            )
            (continuation / "artifact_index.json").write_text(
                json.dumps(rebuilt, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            verification = verify_run(continuation)
            self.assertEqual(verification["status"], "fail")
            self.assertTrue(
                any(
                    "stage5-developability-reproducibility" in error
                    for error in verification["errors"]
                ),
                verification["errors"],
            )

    def test_stage1_to_stage7_missing_data_control_flow_is_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path, source_run = self._write_stage2_project(root)
            stage3_run = self._write_verified_stage3_run(
                root, config_path, source_run, hour=14
            )
            initialize_assessment_specifications(
                config_path, source_run_dir=stage3_run
            )
            stage5_run = write_post_structure_run(
                analyze_post_structure_stages(
                    config_path, source_run_dir=stage3_run
                ),
                now=datetime(2026, 7, 15, 15, 0, tzinfo=timezone.utc),
            )
            initialize_product_specifications(
                config_path, source_run_dir=stage5_run
            )
            product_analysis = analyze_product_designs(
                config_path, source_run_dir=stage5_run
            )
            stage6_run = write_product_design_run(
                product_analysis,
                now=datetime(2026, 7, 15, 16, 0, tzinfo=timezone.utc),
            )
            initialize_ranking_specification(
                config_path, source_run_dir=stage6_run
            )
            ranking_analysis = analyze_integrated_ranking(
                config_path, source_run_dir=stage6_run
            )
            stage7_run = write_ranking_run(
                ranking_analysis,
                now=datetime(2026, 7, 15, 17, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(product_analysis.protein_result["status"], "needs_data")
            self.assertEqual(product_analysis.mrna_result["status"], "needs_data")
            self.assertEqual(ranking_analysis.result["status"], "needs_data")
            self.assertEqual(ranking_analysis.result["formal_portfolio"], [])
            self.assertTrue(
                all(
                    product["translation_verified"]
                    for product in product_analysis.protein_result["products"]
                    if product["coding_sequence_dna"] is not None
                )
            )
            self.assertTrue(
                any(
                    product["coding_source"]
                    == "candidate_control_rejected_translation_mismatch"
                    for product in product_analysis.protein_result["products"]
                )
            )
            self.assertTrue(
                all(
                    design["translation_verified"]
                    for design in product_analysis.mrna_result["designs"]
                )
            )
            stage6_verification = verify_run(stage6_run)
            stage7_verification = verify_run(stage7_run)
            self.assertEqual(
                stage6_verification["status"], "pass", stage6_verification["errors"]
            )
            self.assertEqual(
                stage7_verification["status"], "pass", stage7_verification["errors"]
            )
            round_feedback = json.loads(
                (
                    stage7_run
                    / "nodes/integrated_ranking/round_feedback.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(round_feedback["round_id"], "round-000")
            self.assertEqual(
                round_feedback["request_count"], len(round_feedback["requests"])
            )
            self.assertIn(
                "developability_assessment",
                round_feedback["source_stages"],
            )

    def test_stage7_verifier_detects_score_tampering_after_reindex(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path, source_run = self._write_stage2_project(root)
            stage3_run = self._write_verified_stage3_run(
                root, config_path, source_run, hour=18
            )
            initialize_assessment_specifications(config_path, source_run_dir=stage3_run)
            stage5_run = write_post_structure_run(
                analyze_post_structure_stages(config_path, source_run_dir=stage3_run),
                now=datetime(2026, 7, 15, 19, 0, tzinfo=timezone.utc),
            )
            initialize_product_specifications(config_path, source_run_dir=stage5_run)
            stage6_run = write_product_design_run(
                analyze_product_designs(config_path, source_run_dir=stage5_run),
                now=datetime(2026, 7, 15, 20, 0, tzinfo=timezone.utc),
            )
            initialize_ranking_specification(config_path, source_run_dir=stage6_run)
            stage7_run = write_ranking_run(
                analyze_integrated_ranking(config_path, source_run_dir=stage6_run),
                now=datetime(2026, 7, 15, 21, 0, tzinfo=timezone.utc),
            )
            result_path = stage7_run / "nodes/integrated_ranking/ranking_result.json"
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["rankings"][0]["score"] = 1.0
            result_path.write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            manifest = json.loads((stage7_run / "manifest.json").read_text(encoding="utf-8"))
            rebuilt = build_artifact_index(
                stage7_run, manifest["project_id"], manifest["run_id"]
            )
            (stage7_run / "artifact_index.json").write_text(
                json.dumps(rebuilt, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            verification = verify_run(stage7_run)
            self.assertEqual(verification["status"], "fail")
            self.assertTrue(
                any(
                    "stage7-ranking-reproducibility" in error
                    for error in verification["errors"]
                ),
                verification["errors"],
            )

    def test_stage6_generates_translation_safe_pareto_designs_with_versioned_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path, source_run = self._write_stage2_project(root)
            stage3_run = self._write_verified_stage3_run(
                root, config_path, source_run, hour=22
            )
            initialize_assessment_specifications(config_path, source_run_dir=stage3_run)
            stage5_run = write_post_structure_run(
                analyze_post_structure_stages(config_path, source_run_dir=stage3_run),
                now=datetime(2026, 7, 15, 23, 0, tzinfo=timezone.utc),
            )
            initialized = initialize_product_specifications(
                config_path, source_run_dir=stage5_run
            )
            runtime_root = Path(
                json.loads(config_path.read_text(encoding="utf-8"))["runtime_root"]
            )
            codon_path = runtime_root / "input/stage6/fixture-codon-usage.json"
            self._write_codon_usage(codon_path)

            protein_path = Path(initialized["protein_specification"])
            protein = json.loads(protein_path.read_text(encoding="utf-8"))
            protein["selection"]["status"] = "approved"
            protein["expression_context"].update(
                {
                    "status": "approved",
                    "host": "fixture-host",
                    "compartment": "fixture-cytosol",
                    "vector_family": "fixture-vector",
                    "purification_strategy": "fixture-affinity",
                    "final_product_form": "fixture-soluble-protein",
                }
            )
            protein["policy"]["status"] = "approved"
            protein["codon_usage_table_path"] = str(codon_path.relative_to(runtime_root))
            protein_path.write_text(
                json.dumps(protein, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )

            mrna_path = Path(initialized["mrna_specification"])
            mrna = json.loads(mrna_path.read_text(encoding="utf-8"))
            mrna["selection"]["status"] = "approved"
            mrna["target_context"].update(
                {
                    "status": "approved",
                    "species": "fixture-host",
                    "cell_context": "fixture-cell",
                    "delivery_platform": "fixture-lnp",
                }
            )
            mrna["manufacturing_context"].update(
                {"status": "approved", "method": "in_vitro_transcription"}
            )
            candidate_batch = json.loads(
                (
                    stage5_run
                    / "nodes/candidate_specification/candidate_batch.json"
                ).read_text(encoding="utf-8")
            )
            provided_binding = mrna["selection"]["candidates"][0]
            provided_candidate = next(
                candidate
                for candidate in candidate_batch["candidates"]
                if candidate["candidate_id"] == provided_binding["candidate_id"]
            )
            provided_path = runtime_root / "input/stage6/mock-provided-control.fasta"
            provided_path.write_text(
                f">mock-control\n{provided_candidate['nucleotide_sequence']}\n",
                encoding="utf-8",
            )
            mrna["provided_coding_sequences"] = [
                {
                    "control_id": "fixture-mock-control-v1",
                    "candidate_id": provided_candidate["candidate_id"],
                    "sequence_path": str(provided_path.relative_to(runtime_root)),
                    "evidence_class": "mock",
                    "provenance_status": "user_declared",
                    "intended_use": "mock_reference_control",
                    "source_description": "Synthetic test fixture",
                }
            ]
            mrna["generation"].update({"status": "enabled", "designs_per_candidate": 2})
            mrna["constraints"].update(
                {
                    "maximum_gc_fraction": 0.95,
                    "target_gc_fraction": 0.60,
                    "maximum_homopolymer_length": 30,
                }
            )
            mrna["noncoding_elements"].update(
                {
                    "status": "approved",
                    "five_prime_utr": "ACGU",
                    "three_prime_utr": "UGCA",
                    "poly_a_length": 12,
                    "cap_assumption": "fixture-cap",
                    "modified_nucleoside_assumption": "fixture-unmodified",
                }
            )
            mrna["policy"]["status"] = "approved"
            mrna["codon_usage_table_path"] = str(codon_path.relative_to(runtime_root))
            mrna_path.write_text(
                json.dumps(mrna, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )

            provided_content = provided_path.read_text(encoding="utf-8")
            provided_path.write_text(">wrong-control\nATGTAA\n", encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError, "does not translate to"
            ):
                analyze_product_designs(config_path, source_run_dir=stage5_run)
            provided_path.write_text(provided_content, encoding="utf-8")

            provisional = analyze_product_designs(config_path, source_run_dir=stage5_run)
            for adapter_id in ("structure_recheck", "expression_support"):
                path = runtime_root / f"input/stage6/protein-evidence/{adapter_id}.json"
                self._write_empty_product_evidence(
                    path,
                    schema_version="vaxflow.product-evidence.v1",
                    adapter_id=adapter_id,
                    binding_field="product_batch_sha256",
                    binding_sha256=provisional.protein_result["product_batch_sha256"],
                )
                protein["external_adapters"][adapter_id] = {
                    "status": "provided",
                    "result_path": str(path.relative_to(runtime_root)),
                }
            protein_path.write_text(
                json.dumps(protein, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            for adapter_id in ("rna_structure", "evo2_sequence_score"):
                path = runtime_root / f"input/stage6/mrna-evidence/{adapter_id}.json"
                if adapter_id == "evo2_sequence_score":
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(
                        json.dumps(
                            {
                                "schema_version": "vaxflow.mrna-evidence.v1",
                                "adapter_id": adapter_id,
                                "mrna_design_batch_sha256": provisional.mrna_result[
                                    "mrna_design_batch_sha256"
                                ],
                                "tool": {
                                    "name": "fixture-evo2",
                                    "version": "7b",
                                    "revision": "fixture-revision",
                                },
                                "observations": [
                                    {
                                        "evidence_id": f"evo2-{index}",
                                        "design_id": design["design_id"],
                                        "status": "context",
                                        "score": float(index),
                                    }
                                    for index, design in enumerate(
                                        provisional.mrna_result["designs"], 1
                                    )
                                ],
                            },
                            sort_keys=True,
                        ),
                        encoding="utf-8",
                    )
                else:
                    self._write_empty_product_evidence(
                        path,
                        schema_version="vaxflow.mrna-evidence.v1",
                        adapter_id=adapter_id,
                        binding_field="mrna_design_batch_sha256",
                        binding_sha256=provisional.mrna_result["mrna_design_batch_sha256"],
                    )
                mrna["external_adapters"][adapter_id] = {
                    "status": "provided",
                    "result_path": str(path.relative_to(runtime_root)),
                }
            mrna_path.write_text(
                json.dumps(mrna, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )

            analysis = analyze_product_designs(config_path, source_run_dir=stage5_run)
            optimized = [
                design
                for design in analysis.mrna_result["designs"]
                if design["design_type"].startswith("synonymous_")
            ]
            provided = [
                design
                for design in analysis.mrna_result["designs"]
                if design["design_type"] == "provided_cds_control"
            ]
            self.assertEqual(len(optimized), 12)
            self.assertEqual(len(provided), 1)
            self.assertEqual(provided[0]["provenance"]["evidence_class"], "mock")
            self.assertEqual(
                provided[0]["provenance"]["provenance_status"], "user_declared"
            )
            self.assertTrue(all(design["translation_verified"] for design in optimized))
            self.assertTrue(all(design["full_mrna_sequence"] for design in optimized))
            self.assertTrue(
                all(
                    state["status"] == "evaluated"
                    for state in analysis.protein_result["adapter_states"].values()
                )
            )
            self.assertTrue(
                all(
                    state["status"] == "evaluated"
                    for state in analysis.mrna_result["adapter_states"].values()
                )
            )
            stage6_run = write_product_design_run(
                analysis,
                now=datetime(2026, 7, 16, 0, 0, tzinfo=timezone.utc),
            )
            verification = verify_run(stage6_run)
            self.assertEqual(verification["status"], "pass", verification["errors"])
            initialize_ranking_specification(config_path, source_run_dir=stage6_run)
            ranking = analyze_integrated_ranking(
                config_path, source_run_dir=stage6_run
            )
            evo2_components = [
                component
                for row in ranking.result["rankings"]
                if row["modality"] == "mrna"
                for component in row["components"]
                if component["feature_id"] == "mrna_evo2_mean_score"
            ]
            self.assertTrue(evo2_components)
            self.assertTrue(
                all(component["raw_value"] is not None for component in evo2_components)
            )
            self.assertTrue(all(component["weight"] == 0.0 for component in evo2_components))


if __name__ == "__main__":
    unittest.main()
