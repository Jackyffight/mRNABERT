#!/usr/bin/env python3
"""Evaluate explicit research-hypothesis coverage in a frozen candidate pool."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def source_counts(candidate: dict[str, Any]) -> Counter[str]:
    return Counter(
        str(component["source_protein_id"])
        for component in candidate.get("inferred_components", [])
        if isinstance(component, dict) and component.get("source_protein_id")
    )


def linker_ids(candidate: dict[str, Any]) -> set[str]:
    parameters = candidate.get("proposal", {}).get("generator", {}).get("parameters", {})
    values: set[str] = set()
    single = parameters.get("linker_id")
    if single:
        values.add(str(single))
    multiple = parameters.get("linker_ids", [])
    if isinstance(multiple, list):
        values.update(str(value) for value in multiple)
    if candidate.get("candidate_type") == "fusion" and not values:
        additions = [
            component
            for component in candidate.get("inferred_components", [])
            if isinstance(component, dict) and component.get("component_type") == "addition"
        ]
        if not additions:
            values.add("direct")
    return values


def matches(candidate: dict[str, Any], rule: dict[str, Any]) -> bool:
    candidate_types = set(map(str, rule.get("candidate_types", [])))
    if candidate_types and str(candidate.get("candidate_type")) not in candidate_types:
        return False

    generator_ids = set(map(str, rule.get("generator_ids", [])))
    generator_id = str(candidate.get("proposal", {}).get("generator", {}).get("id", ""))
    if generator_ids and generator_id not in generator_ids:
        return False

    keys = set(map(str, rule.get("candidate_keys", [])))
    if keys and str(candidate.get("candidate_key")) not in keys:
        return False

    counts = source_counts(candidate)
    required = set(map(str, rule.get("required_source_proteins", [])))
    if required and not required.issubset(counts):
        return False
    any_sources = set(map(str, rule.get("any_source_proteins", [])))
    if any_sources and not any(source in counts for source in any_sources):
        return False
    for source, minimum in rule.get("min_source_counts", {}).items():
        if counts[str(source)] < int(minimum):
            return False

    allowed_linkers = set(map(str, rule.get("linker_ids_any", [])))
    if allowed_linkers and not (allowed_linkers & linker_ids(candidate)):
        return False

    architectures = set(map(str, rule.get("required_product_architectures", [])))
    if architectures and str(candidate.get("product_architecture", "")) not in architectures:
        return False
    return True


def coverage_status(pool_count: int, ranked_count: int, portfolio_count: int) -> str:
    if pool_count == 0:
        return "absent"
    if ranked_count == 0:
        return "covered_not_evaluated"
    if portfolio_count == 0:
        return "evaluated_not_selected"
    return "represented_in_portfolio"


def composition(candidate: dict[str, Any]) -> str:
    counts = source_counts(candidate)
    if not counts:
        return "no_source_component"
    return "+".join(
        f"{source}x{count}" if count > 1 else source
        for source, count in sorted(counts.items())
    )


def evaluate(
    candidate_batch: dict[str, Any],
    ranking_result: dict[str, Any],
    hypotheses: dict[str, Any],
) -> dict[str, Any]:
    candidates = candidate_batch.get("candidates", [])
    if not isinstance(candidates, list):
        raise ValueError("Candidate batch has no candidates array")
    by_id = {str(candidate["candidate_id"]): candidate for candidate in candidates}

    ranking_rows = ranking_result.get("rankings", [])
    ranked_ids = {str(row["candidate_id"]) for row in ranking_rows}
    portfolios = ranking_result.get("provisional_portfolios", {})
    portfolio_rows = [
        row
        for rows in portfolios.values()
        if isinstance(rows, list)
        for row in rows
        if isinstance(row, dict)
    ]
    portfolio_ids = {str(row["candidate_id"]) for row in portfolio_rows}

    results = []
    for hypothesis in hypotheses.get("hypotheses", []):
        if not isinstance(hypothesis, dict):
            raise ValueError("Hypothesis records must be objects")
        coverage = hypothesis.get("coverage", {})
        mode = coverage.get("mode")
        if mode == "context_gate":
            results.append(
                {
                    "hypothesis_id": hypothesis["hypothesis_id"],
                    "arm_id": hypothesis["arm_id"],
                    "coverage_status": coverage["declared_status"],
                    "pool_match_count": None,
                    "ranked_match_count": None,
                    "portfolio_match_count": None,
                    "sample_candidate_ids": [],
                    "reason": coverage["reason"],
                }
            )
            continue
        if mode != "candidate_query":
            raise ValueError(f"Unsupported coverage mode for {hypothesis['hypothesis_id']}")
        rule = coverage.get("rule", {})
        matching = [candidate for candidate in candidates if matches(candidate, rule)]
        matching_ids = {str(candidate["candidate_id"]) for candidate in matching}
        ranked_matches = matching_ids & ranked_ids
        portfolio_matches = matching_ids & portfolio_ids
        results.append(
            {
                "hypothesis_id": hypothesis["hypothesis_id"],
                "arm_id": hypothesis["arm_id"],
                "coverage_status": coverage_status(
                    len(matching_ids), len(ranked_matches), len(portfolio_matches)
                ),
                "pool_match_count": len(matching_ids),
                "ranked_match_count": len(ranked_matches),
                "portfolio_match_count": len(portfolio_matches),
                "sample_candidate_ids": sorted(matching_ids)[:10],
                "reason": coverage.get("reason", ""),
            }
        )

    pool_composition = Counter(composition(candidate) for candidate in candidates)
    ranked_composition = Counter(
        composition(by_id[candidate_id]) for candidate_id in ranked_ids if candidate_id in by_id
    )
    portfolio_composition = Counter(
        composition(by_id[candidate_id])
        for candidate_id in portfolio_ids
        if candidate_id in by_id
    )
    status_counts = Counter(result["coverage_status"] for result in results)

    return {
        "schema_version": "vaxflow.research-candidate-impact.v1",
        "skill": "exploratory-research-loop/candidate-impact@v0.1",
        "status": "ready_for_review",
        "inputs": {
            "candidate_run_id": candidate_batch.get("run_id"),
            "candidate_count": len(candidates),
            "ranked_candidate_count": len(ranked_ids),
            "portfolio_candidate_count": len(portfolio_ids),
            "portfolio_row_count": len(portfolio_rows),
        },
        "coverage_status_counts": dict(sorted(status_counts.items())),
        "composition_counts": {
            "pool": dict(sorted(pool_composition.items())),
            "ranked": dict(sorted(ranked_composition.items())),
            "portfolio": dict(sorted(portfolio_composition.items())),
        },
        "results": results,
        "limitations": [
            "Coverage is deterministic syntactic matching against declared rules, not biological equivalence.",
            "A represented family may still lack the exact boundary, order, conformation, expression context, or experimental support described by a claim.",
            "Portfolio counts are unique candidate identities; portfolio_row_count includes modality-specific rows."
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate_batch", type=Path)
    parser.add_argument("ranking_result", type=Path)
    parser.add_argument("hypotheses", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    document = evaluate(
        load_json(args.candidate_batch),
        load_json(args.ranking_result),
        load_json(args.hypotheses),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
