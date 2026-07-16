import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from design_flow.evo2_adapter import (
    build_evo2_evidence,
    build_evo2_job_documents,
    import_evo2_results,
    load_evo2_result_archive,
    validate_evo2_job_documents,
    write_evo2_result_archive,
)
from design_flow.verification import sha256_file


def digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def fixture_documents():
    routing_id = "2" * 64
    stage6_manifest = {
        "project_id": "test-evo2",
        "run_id": "stage6-run",
        "current_stage": "mrna_product_design",
        "executed_stages": ["protein_product_design", "mrna_product_design"],
        "lineage": {"parent_run_id": "stage5-run"},
    }
    raw = [
        ("mrna-one", "candidate-one", "source_cds_control", "priority", "ATGGCTTAA"),
        ("mrna-two", "candidate-one", "provided_cds_control", "priority", "ATGGCGTAA"),
    ]
    designs = []
    followup_records = []
    for design_id, candidate_id, design_type, lane, sequence in raw:
        sequence_sha256 = digest_text(sequence)
        designs.append(
            {
                "design_id": design_id,
                "candidate_id": candidate_id,
                "design_type": design_type,
                "routing_lane": lane,
                "coding_sequence_dna": sequence,
                "coding_sequence_sha256": sequence_sha256,
                "translation_verified": True,
                "expensive_followup_eligible": True,
            }
        )
        followup_records.append(
            {
                "design_id": design_id,
                "candidate_id": candidate_id,
                "routing_lane": lane,
                "coding_sequence_sha256": sequence_sha256,
            }
        )
    mrna_products = {
        "schema_version": 1,
        "stage_id": "mrna_product_design",
        "mrna_design_batch_sha256": "1" * 64,
        "routing": {"routing_id": routing_id},
        "designs": designs,
    }
    followup = {
        "schema_version": "vaxflow.stage6-model-followup.v1",
        "modality": "mrna",
        "routing_id": routing_id,
        "records": followup_records,
    }
    return stage6_manifest, mrna_products, followup


def build_job(**overrides):
    stage6_manifest, mrna_products, followup = fixture_documents()
    arguments = {
        "project_id": "test-evo2",
        "stage6_manifest": stage6_manifest,
        "stage6_artifact_index_sha256": "3" * 64,
        "mrna_products": mrna_products,
        "mrna_products_sha256": "4" * 64,
        "followup_manifest": followup,
        "followup_manifest_sha256": "5" * 64,
    }
    arguments.update(overrides)
    return build_evo2_job_documents(**arguments)


def fixture_scores(job):
    scores = []
    for index, record in enumerate(job["records"], start=1):
        count = record["sequence_length_nt"] - 1
        mean = -0.5 - index / 10
        scores.append(
            {
                "design_id": record["design_id"],
                "coding_sequence_sha256": record["coding_sequence_sha256"],
                "sequence_length_nt": record["sequence_length_nt"],
                "predicted_token_count": count,
                "total_log_likelihood": mean * count,
                "mean_log_likelihood": mean,
                "perplexity": math.exp(-mean),
            }
        )
    return scores


class Evo2AdapterTest(unittest.TestCase):
    def test_job_binds_exact_followup_sequences(self):
        job, fasta = build_job()

        validate_evo2_job_documents(job, fasta)
        self.assertEqual(len(job["records"]), 2)
        self.assertEqual(job["fasta"]["records"], 2)
        self.assertEqual(job["source"]["mrna_design_batch_sha256"], "1" * 64)
        self.assertEqual(job["records"][0]["design_id"], "mrna-one")

        tampered = fasta.replace(b"ATGGCTTAA", b"ATGGCGTAA", 1)
        with self.assertRaisesRegex(ValueError, "Invalid Evo 2 job record"):
            validate_evo2_job_documents(job, tampered)

    def test_evidence_is_context_only_and_complete(self):
        job, _ = build_job()
        evidence = build_evo2_evidence(job, fixture_scores(job), tool_version="0.6.0")

        self.assertEqual(evidence["adapter_id"], "evo2_sequence_score")
        self.assertEqual(len(evidence["observations"]), 2)
        self.assertTrue(
            all(item["status"] == "context" for item in evidence["observations"])
        )
        self.assertTrue(
            all(
                item["score_semantics"] == "higher_is_more_likely_under_pinned_evo2"
                for item in evidence["observations"]
            )
        )

        with self.assertRaisesRegex(ValueError, "score set differs"):
            build_evo2_evidence(job, fixture_scores(job)[:1], tool_version="0.6.0")

    def test_result_archive_round_trip_is_deterministic(self):
        job, fasta = build_job()
        scores = fixture_scores(job)
        execution = {
            "device": "cuda:0",
            "gpu_name": "fixture-a100",
            "checkpoint_sha256": job["model"]["checkpoint_sha256"],
            "evo2_package_version": "0.6.0",
            "torch_version": "2.7.1",
            "use_kernels": False,
            "scoring_dtype": "bfloat16",
            "elapsed_seconds": 1.0,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = write_evo2_result_archive(
                job,
                fasta,
                scores,
                tool_version="0.6.0",
                output_root=root,
                execution=execution,
            )
            first_sha = sha256_file(Path(first["archive"]))
            second = write_evo2_result_archive(
                job,
                fasta,
                scores,
                tool_version="0.6.0",
                output_root=root,
                execution=execution,
            )
            loaded_job, loaded_fasta, evidence, run_manifest, _ = (
                load_evo2_result_archive(second["archive"])
            )

        self.assertEqual(first_sha, second["archive_sha256"])
        self.assertEqual(loaded_job, job)
        self.assertEqual(loaded_fasta, fasta)
        self.assertEqual(len(evidence["observations"]), 2)
        self.assertEqual(run_manifest["status"], "complete")

    def test_import_updates_versioned_stage6_adapter_declaration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = root / "source"
            runtime_root = root / "runtime"
            source_root.mkdir()
            runtime_root.mkdir()
            project_config = source_root / "project.json"
            project_config.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "project_id": "test-evo2",
                        "expected_protein_count": 3,
                        "runtime_root": str(runtime_root),
                        "inputs": {
                            "amino_acid_fasta": "input/proteins_aa.fasta",
                            "nucleotide_fasta": "input/proteins_cds.fasta",
                        },
                        "outputs": {"run_root": "runs"},
                        "context": {"product_modalities": []},
                    }
                ),
                encoding="utf-8",
            )
            stage6_run = runtime_root / "runs" / "stage6-run"
            mrna_node = stage6_run / "nodes" / "mrna_product_design"
            mrna_node.mkdir(parents=True)
            stage6_manifest, mrna_products, followup = fixture_documents()
            (stage6_run / "manifest.json").write_text(
                json.dumps(stage6_manifest), encoding="utf-8"
            )
            (stage6_run / "artifact_index.json").write_text("{}\n", encoding="utf-8")
            products_path = mrna_node / "mrna_products.json"
            followup_path = mrna_node / "model_followup_manifest.json"
            products_path.write_text(json.dumps(mrna_products), encoding="utf-8")
            followup_path.write_text(json.dumps(followup), encoding="utf-8")
            job, fasta = build_job(
                stage6_artifact_index_sha256=sha256_file(
                    stage6_run / "artifact_index.json"
                ),
                mrna_products_sha256=sha256_file(products_path),
                followup_manifest_sha256=sha256_file(followup_path),
            )
            result = write_evo2_result_archive(
                job,
                fasta,
                fixture_scores(job),
                tool_version="0.6.0",
                output_root=root / "results",
                execution={
                    "device": "cuda:0",
                    "gpu_name": "fixture-a100",
                    "checkpoint_sha256": job["model"]["checkpoint_sha256"],
                    "evo2_package_version": "0.6.0",
                    "torch_version": "2.7.1",
                    "use_kernels": False,
                    "scoring_dtype": "bfloat16",
                    "elapsed_seconds": 1.0,
                },
            )
            specification_path = runtime_root / "input" / "stage6" / "mrna_product_specification.json"
            specification_path.parent.mkdir(parents=True)
            specification_path.write_text(
                json.dumps(
                    {
                        "routing": {"routing_id": "2" * 64},
                        "external_adapters": {
                            "evo2_sequence_score": {
                                "status": "not_configured",
                                "result_path": None,
                            },
                            "rna_structure": {
                                "status": "not_configured",
                                "result_path": None,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch(
                "design_flow.evo2_adapter.verify_run",
                return_value={"status": "pass", "errors": []},
            ):
                imported = import_evo2_results(
                    project_config,
                    result_archive=result["archive"],
                )
            updated = json.loads(specification_path.read_text(encoding="utf-8"))

        declaration = updated["external_adapters"]["evo2_sequence_score"]
        self.assertEqual(declaration["status"], "provided")
        self.assertEqual(declaration["result_path"], Path(imported["evidence_path"]).relative_to(runtime_root).as_posix())
        self.assertEqual(imported["observations"], 2)
        self.assertIsNotNone(imported["archived_specification"])


if __name__ == "__main__":
    unittest.main()
