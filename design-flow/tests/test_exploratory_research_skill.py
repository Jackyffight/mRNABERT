import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_SCRIPTS = (
    REPO_ROOT
    / "design-flow"
    / "skills"
    / "exploratory-research-loop"
    / "scripts"
)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


source_inventory = load_module(
    "research_source_inventory", SKILL_SCRIPTS / "build_source_inventory.py"
)
candidate_impact = load_module(
    "research_candidate_impact", SKILL_SCRIPTS / "evaluate_candidate_impact.py"
)


class SourceInventoryTests(unittest.TestCase):
    def make_run(self, root: Path, independent_doi: str = "10.1000/independent") -> Path:
        retrieval = root / "02-retrieval"
        raw = retrieval / "raw"
        raw.mkdir(parents=True)
        (retrieval / "independent-selected-pubmed.json").write_text(
            json.dumps(
                {
                    "records": [
                        {
                            "pmid": "111",
                            "pmc": "PMC111",
                            "doi": independent_doi,
                            "title": "Independent source",
                            "abstract": "Independent evidence.",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        (retrieval / "direct-gold-pubmed.json").write_text(
            json.dumps(
                {
                    "records": [
                        {
                            "pmid": "222",
                            "doi": "10.1038/s41590-023-01715-7",
                            "title": "Held-out direct source",
                            "abstract": "Direct evidence.",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        (retrieval / "PMC111.json").write_text(
            json.dumps({"identifiers": {"pmcid": "PMC111"}, "section_count": 3}),
            encoding="utf-8",
        )
        (raw / "PMC111.xml").write_text("<article/>", encoding="utf-8")
        return root

    def test_inventory_separates_direct_and_independent_arms(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = self.make_run(Path(directory))
            result = source_inventory.build_inventory(run_dir)

        self.assertEqual(result["source_count"], 2)
        self.assertEqual(
            result["counts"]["by_arm"],
            {"direct-prior": 1, "independent-prior": 1},
        )
        self.assertEqual(
            result["counts"]["by_access_level"],
            {"abstract_only": 1, "full_text": 1},
        )
        independent = next(
            source for source in result["sources"] if source["arm_id"] == "independent-prior"
        )
        self.assertEqual(len(independent["snapshots"]), 3)

    def test_inventory_rejects_direct_paper_leakage(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = self.make_run(
                Path(directory), independent_doi="10.1038/s41590-023-01715-7"
            )
            with self.assertRaisesRegex(ValueError, "leaked into independent arm"):
                source_inventory.build_inventory(run_dir)


class CandidateImpactTests(unittest.TestCase):
    def test_explicit_rules_drive_coverage_without_biological_inference(self):
        candidates = {
            "run_id": "candidate-run",
            "candidates": [
                {
                    "candidate_id": "fusion-1",
                    "candidate_key": "fusion-a-b",
                    "candidate_type": "fusion",
                    "product_architecture": "secreted-fusion",
                    "inferred_components": [
                        {"component_type": "source", "source_protein_id": "A"},
                        {"component_type": "source", "source_protein_id": "B"},
                    ],
                    "proposal": {
                        "generator": {
                            "id": "evidence-guided",
                            "parameters": {"linker_id": "G4S"},
                        }
                    },
                },
                {
                    "candidate_id": "truncation-1",
                    "candidate_key": "truncation-b",
                    "candidate_type": "truncation",
                    "product_architecture": "secreted-single",
                    "inferred_components": [
                        {"component_type": "source", "source_protein_id": "B"}
                    ],
                    "proposal": {"generator": {"id": "boundary-search", "parameters": {}}},
                },
            ],
        }
        ranking = {
            "rankings": [
                {"candidate_id": "fusion-1", "modality": "protein"},
                {"candidate_id": "fusion-1", "modality": "mrna"},
                {"candidate_id": "truncation-1", "modality": "protein"},
            ],
            "provisional_portfolios": {
                "protein": [{"candidate_id": "fusion-1"}],
                "mrna": [{"candidate_id": "fusion-1"}],
            },
        }
        hypotheses = {
            "hypotheses": [
                {
                    "hypothesis_id": "h-represented",
                    "arm_id": "independent-prior",
                    "coverage": {
                        "mode": "candidate_query",
                        "rule": {"required_source_proteins": ["A", "B"]},
                    },
                },
                {
                    "hypothesis_id": "h-evaluated",
                    "arm_id": "independent-prior",
                    "coverage": {
                        "mode": "candidate_query",
                        "rule": {
                            "candidate_types": ["truncation"],
                            "required_source_proteins": ["B"],
                        },
                    },
                },
                {
                    "hypothesis_id": "h-absent",
                    "arm_id": "independent-prior",
                    "coverage": {
                        "mode": "candidate_query",
                        "rule": {"required_source_proteins": ["C"]},
                    },
                },
                {
                    "hypothesis_id": "h-context",
                    "arm_id": "direct-prior",
                    "coverage": {
                        "mode": "context_gate",
                        "declared_status": "needs_human_input",
                        "reason": "Host-specific evidence is missing.",
                    },
                },
            ]
        }

        result = candidate_impact.evaluate(candidates, ranking, hypotheses)
        statuses = {
            row["hypothesis_id"]: row["coverage_status"] for row in result["results"]
        }

        self.assertEqual(result["inputs"]["ranked_candidate_count"], 2)
        self.assertEqual(result["inputs"]["portfolio_candidate_count"], 1)
        self.assertEqual(result["inputs"]["portfolio_row_count"], 2)
        self.assertEqual(statuses["h-represented"], "represented_in_portfolio")
        self.assertEqual(statuses["h-evaluated"], "evaluated_not_selected")
        self.assertEqual(statuses["h-absent"], "absent")
        self.assertEqual(statuses["h-context"], "needs_human_input")


if __name__ == "__main__":
    unittest.main()
