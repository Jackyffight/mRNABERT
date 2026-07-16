"""Reconcile mutable project declarations with immutable continuation lineage."""

from __future__ import annotations

from typing import Any

from .domain import ProjectConfig
from .requirement_gates import REQUIREMENT_CLASSES


UNSPECIFIED_VALUES = frozenset({"", "none", "tbd", "unknown", "unspecified"})


def project_context(config: ProjectConfig) -> dict[str, Any]:
    context: dict[str, Any] = {
        "target_indication": config.target_indication,
        "intended_host_species": config.intended_host_species,
        "product_modalities": list(config.product_modalities),
        "protein_expression_host": config.protein_expression_host,
        "mrna_target_species": config.mrna_target_species,
    }
    if config.project_mode is not None:
        context["project_mode"] = config.project_mode
    if config.scientific_release_allowed is not None:
        context["scientific_release_allowed"] = config.scientific_release_allowed
    if config.mrna_manufacturing_method is not None:
        context["mrna_manufacturing_method"] = config.mrna_manufacturing_method
    return context


def _specified(value: str) -> bool:
    return value.strip().lower() not in UNSPECIFIED_VALUES


def _context_resolution(
    action_id: str,
    config: ProjectConfig,
) -> tuple[str, str] | None:
    if action_id == "define-target-indication" and _specified(config.target_indication):
        return (
            f"Satisfied by versioned project context: target_indication={config.target_indication}.",
            f"已由版本化项目上下文满足：target_indication={config.target_indication}。",
        )
    if action_id == "confirm-intended-host-species" and _specified(
        config.intended_host_species
    ):
        return (
            "Satisfied by versioned project context: "
            f"intended_host_species={config.intended_host_species}.",
            "已由版本化项目上下文满足："
            f"intended_host_species={config.intended_host_species}。",
        )
    if action_id == "select-product-modalities" and config.product_modalities:
        modalities = ",".join(config.product_modalities)
        return (
            f"Satisfied by versioned project context: product_modalities={modalities}.",
            f"已由版本化项目上下文满足：product_modalities={modalities}。",
        )
    if action_id == "select-protein-expression-host" and _specified(
        config.protein_expression_host
    ):
        return (
            "Satisfied by versioned project context: "
            f"protein_expression_host={config.protein_expression_host}.",
            "已由版本化项目上下文满足："
            f"protein_expression_host={config.protein_expression_host}。",
        )
    if action_id == "confirm-mrna-target-species" and _specified(
        config.mrna_target_species
    ):
        return (
            "Satisfied by versioned project context: "
            f"mrna_target_species={config.mrna_target_species}.",
            "已由版本化项目上下文满足："
            f"mrna_target_species={config.mrna_target_species}。",
        )
    return None


def reconcile_human_actions(
    parent_actions: list[dict[str, Any]],
    config: ProjectConfig,
) -> list[dict[str, Any]]:
    """Apply current declarations without rewriting immutable parent evidence."""

    declared = {
        action.action_id: action.to_dict()
        for action in config.human_actions
    }
    reconciled: list[dict[str, Any]] = []
    seen: set[str] = set()
    for parent_action in parent_actions:
        action_id = str(parent_action["action_id"])
        if action_id in declared:
            action = {**parent_action, **declared[action_id]}
        else:
            action = dict(parent_action)
            resolution = _context_resolution(action_id, config)
            if resolution is not None:
                action.update(
                    {
                        "status": "resolved",
                        "owner": "project_configuration",
                        "resolution": resolution[0],
                        "resolution_zh": resolution[1],
                    }
                )
        reconciled.append(action)
        seen.add(action_id)
    reconciled.extend(
        dict(action)
        for action_id, action in declared.items()
        if action_id not in seen
    )
    return reconciled


def merge_requirement_actions(
    parent_actions: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
    *,
    required_before_stage: str | None = None,
    question_zh: str | None = None,
) -> list[dict[str, Any]]:
    actions = [dict(action) for action in parent_actions]
    action_by_id = {action["action_id"]: action for action in actions}
    for requirement in requirements:
        action_id = requirement["requirement_id"]
        requirement_class = requirement.get("requirement_class")
        if requirement_class not in REQUIREMENT_CLASSES:
            raise ValueError(
                f"Requirement {action_id} has invalid class: {requirement_class}"
            )
        deadline = requirement.get("required_before_stage", required_before_stage)
        if not isinstance(deadline, str) or not deadline:
            raise ValueError(f"Requirement {action_id} has no deadline stage")
        metadata = {
            "question": requirement["description"],
            "question_zh": requirement.get("description_zh")
            or question_zh
            or "补充并确认该版本化输入或证据。",
            "required_before_stage": deadline,
            "requirement_class": requirement_class,
            "resolution_strategy": requirement["resolution_strategy"],
            "exploratory_progress_allowed": requirement[
                "exploratory_progress_allowed"
            ],
        }
        if action_id in action_by_id:
            action_by_id[action_id].update(
                {
                    **metadata,
                    "status": "open",
                    "resolution": "",
                    "resolution_zh": "",
                }
            )
            continue
        action = {
            "action_id": action_id,
            **metadata,
            "status": "open",
            "owner": "unassigned",
            "resolution": "",
            "resolution_zh": "",
        }
        actions.append(action)
        action_by_id[action_id] = action
    return actions
