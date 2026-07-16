"""Versioned requirement classes used to separate evidence gaps from hard gates."""

from __future__ import annotations

from typing import Any

from .workflow import STAGE_BY_ID


REQUIREMENT_CLASSES = (
    "blocking_now",
    "design_variable",
    "required_before_ranking",
    "required_before_release",
)

REQUIREMENT_CLASS_LABELS = {
    "blocking_now": ("Blocking now", "当前阻塞"),
    "design_variable": ("Design variable", "设计变量"),
    "required_before_ranking": ("Required before ranking", "排名前补齐"),
    "required_before_release": ("Required before release", "放行前补齐"),
}

RESOLUTION_STRATEGIES = (
    "automated_enrichment",
    "automated_enrichment_and_human_approval",
    "computational_or_experimental_evidence",
    "enumerate_or_select_design_variable",
    "human_design_decision",
    "human_policy_approval",
)


def make_requirement(
    requirement_id: str,
    description: str,
    description_zh: str,
    *,
    requirement_class: str,
    required_before_stage: str,
    resolution_strategy: str,
) -> dict[str, Any]:
    """Create a validated machine-readable requirement declaration."""

    if not requirement_id or not description or not description_zh:
        raise ValueError("Requirement ID and bilingual descriptions must be non-empty")
    if requirement_class not in REQUIREMENT_CLASSES:
        raise ValueError(f"Unknown requirement class: {requirement_class}")
    if required_before_stage not in STAGE_BY_ID:
        raise ValueError(f"Unknown requirement deadline stage: {required_before_stage}")
    if resolution_strategy not in RESOLUTION_STRATEGIES:
        raise ValueError(f"Unknown requirement resolution strategy: {resolution_strategy}")
    return {
        "requirement_id": requirement_id,
        "status": "missing",
        "description": description,
        "description_zh": description_zh,
        "requirement_class": requirement_class,
        "required_before_stage": required_before_stage,
        "resolution_strategy": resolution_strategy,
        "exploratory_progress_allowed": requirement_class != "blocking_now",
    }


def requirement_class_counts(
    requirements: list[dict[str, Any]],
) -> dict[str, int]:
    counts = {requirement_class: 0 for requirement_class in REQUIREMENT_CLASSES}
    for requirement in requirements:
        requirement_class = requirement.get("requirement_class")
        if requirement_class not in counts:
            raise ValueError(f"Unknown requirement class: {requirement_class}")
        counts[requirement_class] += 1
    return counts
