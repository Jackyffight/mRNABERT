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
from design_flow.candidate_reporting import write_candidate_run
from design_flow.candidate_specification import analyze_candidate_specification
from design_flow.domain import FastaRecord
from design_flow.fasta import parse_fasta
from design_flow.pipeline import analyze_project
from design_flow.qc import analyze_sequence_pairs, normalize_nucleotide, translate_cds
from design_flow.reporting import write_run_artifacts
from design_flow.structure_job import build_structure_job, write_structure_job
from design_flow.structure_assessment import analyze_structure_results
from design_flow.structure_reporting import write_structure_run
from design_flow.structure_job import _document_sha256, _identity
from design_flow.structure_metrics import ResidueGeometry, geometry_metrics
from design_flow.verification import build_artifact_index, verify_run
from design_flow.workflow import (
    CURRENT_STAGE_ID,
    FULL_WORKFLOW,
    SYSTEM_ARCHITECTURE_VERSION,
    WORKFLOW_ID,
    WORKFLOW_VERSION,
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

        self.assertIn("frozen architecture baseline v1", architecture)
        self.assertIn("docs/audit-automation-and-llm-governance.md", architecture)
        self.assertIn("docs/adr/0001-hybrid-audited-workflow.md", architecture)


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
        config = {
            "schema_version": 1,
            "project_id": "test-three-protein",
            "expected_protein_count": 3,
            "runtime_root": str(runtime_dir),
            "inputs": {
                "amino_acid_fasta": "input/proteins_aa.fasta",
                "nucleotide_fasta": "input/proteins_cds.fasta",
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
                {"project.json", "proteins_aa.fasta", "proteins_cds.fasta"},
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
        config = {
            "schema_version": 1,
            "project_id": "test-stage2",
            "expected_protein_count": 3,
            "runtime_root": str(runtime_dir),
            "inputs": {
                "amino_acid_fasta": "input/proteins_aa.fasta",
                "nucleotide_fasta": "input/proteins_cds.fasta",
                "candidate_specification": "input/candidate_specification.json",
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


if __name__ == "__main__":
    unittest.main()
