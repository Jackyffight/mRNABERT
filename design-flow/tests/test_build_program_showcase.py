import copy
import importlib.util
import unittest
from pathlib import Path


DESIGN_FLOW_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = DESIGN_FLOW_ROOT / "scripts" / "build_program_showcase.py"


def load_module():
    spec = importlib.util.spec_from_file_location("build_program_showcase", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def fixture():
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
        },
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

    def test_fixture_validates_and_renders_bilingual_report(self):
        data = fixture()
        self.module.validate_snapshot(data)
        document = self.module.render_html(data)

        self.assertIn("Stage 1-7 已完成计算前环", document)
        self.assertIn("Formal releases", document)
        self.assertIn("LLM proposal plane", document)
        self.assertIn("No formal experiment release", document)
        self.assertEqual(document.count("class=\"stage-card\""), 7)

    def test_formal_portfolio_is_rejected_for_mock_showcase(self):
        data = fixture()
        data["ranking"]["formal_portfolio_count"] = 1
        with self.assertRaisesRegex(ValueError, "formal portfolio"):
            self.module.validate_snapshot(data)

    def test_routing_must_cover_active_candidates(self):
        data = fixture()
        data["products"]["routing"]["archive"] = 1
        with self.assertRaisesRegex(ValueError, "Routing lanes"):
            self.module.validate_snapshot(data)

    def test_research_counts_must_be_complete(self):
        data = fixture()
        data["research"]["abstract_only_sources"] = 0
        with self.assertRaisesRegex(ValueError, "access levels"):
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
