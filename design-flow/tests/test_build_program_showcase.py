import hashlib
import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path


DESIGN_FLOW_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = DESIGN_FLOW_ROOT / "scripts" / "build_program_showcase.py"


def load_module():
    spec = importlib.util.spec_from_file_location("build_program_showcase", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def fixture(pdb_path: Path):
    stages = []
    for number in range(1, 8):
        stages.append(
            {
                "number": number,
                "node_id": f"stage_{number}",
                "label_en": f"Stage {number}",
                "label_zh": f"Stage {number}",
                "purpose_en": "Purpose",
                "purpose_zh": "Purpose",
                "status": "complete" if number == 1 else "needs_data",
                "audit_status": "pass",
                "run_id": f"run-{number}",
                "metrics": [["Metric", str(number)], ["Checks", "pass"]],
                "question_zh": "研究问题",
                "question_en": "Research question",
                "why_zh": "版本化原因",
                "why_en": "Versioned rationale",
                "decision_zh": "计算结论",
                "decision_en": "Computed decision",
                "operations": [
                    {"operation": f"operation_{number}", "behavior": "Deterministic step"}
                ],
                "report_href": f"/stage-{number}.html",
            }
        )
    return {
        "schema_version": "vaxflow.program-showcase.v1",
        "generated_at_utc": "2026-07-17T00:00:00+00:00",
        "project": {
            "id": "three-protein-vaccine",
            "mode": "mock_workflow_validation",
            "target": "Mock target",
            "host": "cattle",
            "modalities": ["recombinant_protein", "mrna"],
        },
        "run": {
            "run_id": "stage7-run",
            "path": "/runtime/stage7-run",
            "status": "needs_data",
            "current_stage": "integrated_ranking",
        },
        "stages": stages,
        "search": {
            "candidate_count": 10,
            "candidate_types": {"fusion": 7, "source_control": 3},
            "generators": {"search": 7, "source": 3},
            "structure_panel_generators": {"search": 3, "source": 1},
            "active_count": 4,
            "active_fraction": 0.4,
        },
        "structure": {
            "candidate_count": 4,
            "confidence_bands": {"higher": 1, "mixed": 1, "low": 2},
            "review_flag_count": 3,
        },
        "evidence": {
            "mhc_observation_count": 100,
            "developability_adapter_count": 3,
            "developability_liability_count": 12,
            "missing_immune_requirements": 2,
            "missing_developability_requirements": 1,
        },
        "products": {
            "protein_design_count": 4,
            "mrna_design_count": 16,
            "mrna_rejected_count": 2,
            "evo2_observation_count": 8,
            "routing": {
                "active": 4,
                "priority": 1,
                "diversity_rescue": 1,
                "expensive_followup": 2,
                "archive": 2,
                "product_drafting": 4,
            },
            "followup_fraction": 0.5,
        },
        "ranking": {
            "status": "needs_data",
            "mode": "exploratory",
            "ranking_rows": 4,
            "unique_ranked_candidates": 2,
            "provisional_slots": 2,
            "unique_portfolio_candidates": 1,
            "formal_portfolio_count": 0,
            "missing_requirements": ["Human approval is required."],
            "portfolio": [
                {
                    "modality": "protein",
                    "candidate_key": "source-B5",
                    "rank": "1",
                    "score": "0.8",
                    "selection_reason": "control",
                },
                {
                    "modality": "mrna",
                    "candidate_key": "source-B5",
                    "rank": "1",
                    "score": "0.7",
                    "selection_reason": "control",
                },
            ],
            "portfolio_compositions": {"B5": 1},
            "features": [
                {
                    "feature_id": "structure_mean_plddt",
                    "label_zh": "结构局部置信度",
                    "label_en": "Mean pLDDT",
                    "direction": "maximize",
                    "weight": 1.0,
                    "required": True,
                    "modalities": ["protein", "mrna"],
                },
                {
                    "feature_id": "immune_mhc_supported_fraction",
                    "label_zh": "MHC 支持比例",
                    "label_en": "MHC-supported fraction",
                    "direction": "maximize",
                    "weight": 0.0,
                    "required": False,
                    "modalities": ["protein", "mrna"],
                },
            ],
            "hard_gate_count": 0,
            "policy": {"allow_formal_release": False},
            "portfolio_policy": {"budget_per_modality": 1},
        },
        "top_candidates": [
            {
                "candidate_id": "candidate-1",
                "candidate_key": "source-B5",
                "display_name": "B5 source control",
                "candidate_type": "source_control",
                "sequence": "MA",
                "sequence_sha256": hashlib.sha256(b"MA").hexdigest(),
                "length": 2,
                "source_ranges": [
                    {"source_protein_id": "B5", "source_start": 1, "source_end": 2}
                ],
                "generator_id": "source_intake",
                "proposal_rationale": "Preserve a source control.",
                "structure": {
                    "mean_plddt": 85.0,
                    "ptm": 0.7,
                    "confidence_band": "higher_confidence",
                    "fraction_plddt_at_least_90": 0.5,
                    "fraction_plddt_below_70": 0.0,
                    "principal_axis_vectors": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                    "review_flags": [],
                    "pdb_sha256": hashlib.sha256(pdb_path.read_bytes()).hexdigest(),
                    "source_path": str(pdb_path),
                },
                "protein_product": {
                    "design_id": "protein-1",
                    "sequence": "MA",
                    "translation_verified": True,
                    "status": "draft_audited",
                },
                "mrna_designs": [
                    {
                        "candidate_id": "candidate-1",
                        "candidate_key": "source-B5",
                        "design_id": "mrna-1",
                        "design_type": "synonymous_pareto",
                        "coding_sequence_dna": "ATGGCT",
                        "metrics": {"cai_proxy": 0.9},
                    }
                ],
                "best_mrna_design": {
                    "candidate_id": "candidate-1",
                    "candidate_key": "source-B5",
                    "design_id": "mrna-1",
                    "design_type": "synonymous_pareto",
                    "coding_sequence_dna": "ATGGCT",
                    "metrics": {"cai_proxy": 0.9},
                },
                "rankings": {
                    "protein": {
                        "rank": 1,
                        "score": 0.9,
                        "components": [
                            {
                                "feature_id": "structure_mean_plddt",
                                "raw_value": 85.0,
                                "weight": 1.0,
                            },
                            {
                                "feature_id": "structure_ptm",
                                "raw_value": 0.7,
                                "weight": 0.5,
                            },
                            {
                                "feature_id": "developability_review_liability_count",
                                "raw_value": 1,
                                "weight": 0.5,
                            },
                            {
                                "feature_id": "protein_product_translation_verified",
                                "raw_value": 1,
                                "weight": 0.25,
                            },
                        ],
                    },
                    "mrna": {
                        "rank": 1,
                        "score": 0.8,
                        "components": [
                            {
                                "feature_id": "mrna_best_cai_proxy",
                                "raw_value": 0.9,
                                "weight": 0.25,
                            },
                            {
                                "feature_id": "mrna_evo2_mean_score",
                                "raw_value": -1.1,
                                "weight": 0.25,
                            },
                        ],
                    },
                },
                "selection_reasons": {
                    "protein": "required_source_control",
                    "mrna": "required_source_control",
                },
                "deliverables": {
                    "pdb": "deliverables/structures/candidate-1.pdb",
                    "projection": "deliverables/structures/candidate-1.svg",
                },
            }
        ],
        "evo2_sensitivity": {
            "candidate_count": 2,
            "design_count": 8,
            "spearman": 0.99,
            "mean_rank_change": 1.0,
            "max_rank_change": 2,
            "top_10_overlap": 10,
            "interpretation": "small",
        },
        "model_research": {
            "internal_best_spearman": 0.86,
            "public_best_spearman": 0.87,
            "retained_best_fraction": 0.99,
            "internal_worst_spearman": 0.83,
            "public_worst_spearman": 0.52,
            "parameter_reduction": 0.24,
            "throughput_gain": 0.09,
            "time_reduction": 0.08,
            "esmfold_native_agreement": {"ca_lddt_mean": 0.93},
            "proteinmpnn_refold_status": "not-qualified",
            "three_model_shared_probe_status": "pending",
        },
        "research": {
            "status": "complete",
            "source_count": 3,
            "independent_sources": 2,
            "direct_sources": 1,
            "full_text_sources": 1,
            "abstract_only_sources": 1,
            "database_records": 1,
            "claim_status": "raw_run_only",
            "hypothesis_status": "not_started",
            "impact_status": "not_started",
        },
        "capability_boundary": {
            "deterministic_core": "L2_replayable_workflow",
            "research_skill": "L1_audited_llm_workflow",
            "claim_authority": "proposal_only",
            "release_authority": "human_only",
        },
        "input_artifacts": [
            {"path": "/tmp/evidence.json", "sha256": "a" * 64, "bytes": 100}
        ],
        "limitations": ["Mock only.", "No formal release."],
    }


class ProgramShowcaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.pdb_path = Path(self.temporary_directory.name) / "candidate-1.pdb"
        self.pdb_path.write_text(
            "ATOM      1  CA  MET A   1       0.000   0.000   0.000  1.00  0.95           C  \n"
            "ATOM      2  CA  ALA A   2       3.800   1.000   0.500  1.00  0.75           C  \n",
            encoding="ascii",
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_fixture_validates_and_renders_bilingual_report(self):
        data = fixture(self.pdb_path)
        self.module.validate_snapshot(data)
        document = self.module.render_html(data)

        self.assertIn("不是七个页面，而是一条研究决策链", document)
        self.assertIn("最终 Rank 到底依据什么", document)
        self.assertIn("下载完整交付包", document)
        self.assertIn("No formal experiment release", document)
        self.assertEqual(document.count("class=\"decision-chapter\""), 7)
        self.assertEqual(document.count("class=\"candidate-card\""), 1)
        self.assertNotIn("model-comparison", document)

    def test_formal_portfolio_is_rejected_for_mock_showcase(self):
        data = fixture(self.pdb_path)
        data["ranking"]["formal_portfolio_count"] = 1
        with self.assertRaisesRegex(ValueError, "formal portfolio"):
            self.module.validate_snapshot(data)

    def test_routing_must_cover_active_candidates(self):
        data = fixture(self.pdb_path)
        data["products"]["routing"]["archive"] = 1
        with self.assertRaisesRegex(ValueError, "Routing lanes"):
            self.module.validate_snapshot(data)

    def test_research_counts_must_be_complete(self):
        data = fixture(self.pdb_path)
        data["research"]["abstract_only_sources"] = 0
        with self.assertRaisesRegex(ValueError, "access levels"):
            self.module.validate_snapshot(data)

    def test_structure_projection_and_delivery_package_are_real_artifacts(self):
        data = fixture(self.pdb_path)
        projection = self.module.structure_projection_svg(data["top_candidates"][0])
        self.assertIn("<line", projection)
        self.assertIn("checksum-bound", projection)

        output = Path(self.temporary_directory.name) / "showcase"
        report, evidence, package = self.module.write_showcase(data, output)
        self.assertTrue(report.is_file())
        self.assertTrue(evidence.is_file())
        self.assertTrue(package.is_file())
        with zipfile.ZipFile(package) as archive:
            names = set(archive.namelist())
        self.assertIn("top_candidates_antigen_aa.fasta", names)
        self.assertIn("top_mrna_coding_designs.fasta", names)
        self.assertIn("top_candidates_ranking.csv", names)
        self.assertIn("structures/candidate-1.pdb", names)
        self.assertIn("structures/candidate-1.svg", names)

    def test_top_candidate_pdb_hash_mismatch_is_rejected(self):
        data = fixture(self.pdb_path)
        data["top_candidates"][0]["structure"]["pdb_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "PDB hash mismatch"):
            self.module.validate_snapshot(data)

    def test_generated_at_defaults_to_frozen_run_timestamp(self):
        manifest = {"created_at_utc": "2026-07-16T19:28:35+00:00"}
        self.assertEqual(
            self.module.frozen_generated_at(manifest, None),
            "2026-07-16T19:28:35+00:00",
        )
        self.assertEqual(
            self.module.frozen_generated_at(manifest, "2026-07-17T00:00:00+00:00"),
            "2026-07-17T00:00:00+00:00",
        )

    def test_generated_at_requires_a_frozen_source(self):
        with self.assertRaisesRegex(ValueError, "created_at_utc"):
            self.module.frozen_generated_at({}, None)


if __name__ == "__main__":
    unittest.main()
