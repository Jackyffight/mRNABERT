"""Project configuration loading and validation."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from .domain import HumanAction, ProjectConfig


PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
HUMAN_ACTION_STATUSES = frozenset({"open", "resolved", "waived"})


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return value


def _required_text(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _runtime_path(runtime_root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else runtime_root / path).resolve()


def _human_actions(value: Any) -> tuple[HumanAction, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("human_actions must be a JSON array")
    actions: list[HumanAction] = []
    seen_ids: set[str] = set()
    for index, raw_action in enumerate(value):
        action = _mapping(raw_action, f"human_actions[{index}]")
        action_id = _required_text(action, "action_id")
        if not PROJECT_ID_PATTERN.fullmatch(action_id):
            raise ValueError(f"Invalid human action ID: {action_id}")
        if action_id in seen_ids:
            raise ValueError(f"Duplicate human action ID: {action_id}")
        seen_ids.add(action_id)
        status = str(action.get("status", "open"))
        if status not in HUMAN_ACTION_STATUSES:
            raise ValueError(
                f"human action {action_id} has invalid status {status!r}; "
                f"expected one of {sorted(HUMAN_ACTION_STATUSES)}"
            )
        resolution = str(action.get("resolution", "")).strip()
        if status == "resolved" and not resolution:
            raise ValueError(f"resolved human action {action_id} requires a resolution")
        actions.append(
            HumanAction(
                action_id=action_id,
                question=_required_text(action, "question"),
                required_before_stage=_required_text(action, "required_before_stage"),
                question_zh=str(action.get("question_zh", "")).strip(),
                status=status,
                owner=str(action.get("owner", "unassigned")).strip() or "unassigned",
                resolution=resolution,
                resolution_zh=str(action.get("resolution_zh", "")).strip(),
            )
        )
    return tuple(actions)


def load_project_config(path: Path) -> ProjectConfig:
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"Project configuration not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON in {path}: {error}") from error
    data = _mapping(raw, "project configuration")

    schema_version = data.get("schema_version")
    if schema_version != 1:
        raise ValueError(f"Unsupported schema_version {schema_version!r}; expected 1")

    project_id = _required_text(data, "project_id")
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ValueError("project_id may only contain letters, numbers, '.', '_' and '-'")

    expected_count = data.get("expected_protein_count", 3)
    if not isinstance(expected_count, int) or isinstance(expected_count, bool) or expected_count < 1:
        raise ValueError("expected_protein_count must be a positive integer")

    inputs = _mapping(data.get("inputs"), "inputs")
    outputs = _mapping(data.get("outputs", {}), "outputs")
    context = _mapping(data.get("context", {}), "context")
    project_dir = path.parent
    runtime_root_value = Path(_required_text(data, "runtime_root")).expanduser()
    if not runtime_root_value.is_absolute():
        raise ValueError("runtime_root must be an absolute path outside the source project")
    runtime_root = runtime_root_value.resolve()
    if runtime_root == project_dir or runtime_root.is_relative_to(project_dir):
        raise ValueError(
            f"runtime_root must be outside the source project directory: {project_dir}"
        )

    amino_acid_fasta = _runtime_path(
        runtime_root,
        _required_text(inputs, "amino_acid_fasta"),
    )
    nucleotide_fasta = _runtime_path(
        runtime_root,
        _required_text(inputs, "nucleotide_fasta"),
    )
    candidate_specification_value = inputs.get("candidate_specification")
    if candidate_specification_value is not None and (
        not isinstance(candidate_specification_value, str)
        or not candidate_specification_value.strip()
    ):
        raise ValueError("inputs.candidate_specification must be a non-empty path")
    candidate_specification = (
        _runtime_path(runtime_root, candidate_specification_value)
        if isinstance(candidate_specification_value, str)
        else None
    )
    design_input_defaults = {
        "design_brief": "input/design/design_brief.json",
        "design_variable_registry": "input/design/design_variable_registry.json",
        "objective_policy": "input/design/objective_policy.json",
    }
    design_input_values: dict[str, str] = {}
    for name, default in design_input_defaults.items():
        value = inputs.get(name, default)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"inputs.{name} must be a non-empty path")
        design_input_values[name] = value.strip()
    design_brief = _runtime_path(runtime_root, design_input_values["design_brief"])
    design_variable_registry = _runtime_path(
        runtime_root,
        design_input_values["design_variable_registry"],
    )
    objective_policy = _runtime_path(runtime_root, design_input_values["objective_policy"])
    run_root = _runtime_path(runtime_root, str(outputs.get("run_root", "runs")))
    checked_runtime_paths: list[tuple[str, Path]] = [
        ("amino_acid_fasta", amino_acid_fasta),
        ("nucleotide_fasta", nucleotide_fasta),
        ("design_brief", design_brief),
        ("design_variable_registry", design_variable_registry),
        ("objective_policy", objective_policy),
        ("run_root", run_root),
    ]
    if candidate_specification is not None:
        checked_runtime_paths.append(("candidate_specification", candidate_specification))
    for field_name, runtime_path in checked_runtime_paths:
        if not runtime_path.is_relative_to(runtime_root):
            raise ValueError(f"{field_name} must resolve inside runtime_root")

    modalities_value = context.get("product_modalities", [])
    if not isinstance(modalities_value, list) or not all(
        isinstance(modality, str) and modality.strip() for modality in modalities_value
    ):
        raise ValueError("context.product_modalities must be an array of non-empty strings")

    project_mode = context.get("project_mode")
    if project_mode is not None and (
        not isinstance(project_mode, str) or not project_mode.strip()
    ):
        raise ValueError("context.project_mode must be a non-empty string when provided")
    scientific_release_allowed = context.get("scientific_release_allowed")
    if scientific_release_allowed is not None and not isinstance(
        scientific_release_allowed, bool
    ):
        raise ValueError("context.scientific_release_allowed must be a boolean")
    mrna_manufacturing_method = context.get("mrna_manufacturing_method")
    if mrna_manufacturing_method is not None and (
        not isinstance(mrna_manufacturing_method, str)
        or not mrna_manufacturing_method.strip()
    ):
        raise ValueError(
            "context.mrna_manufacturing_method must be a non-empty string when provided"
        )

    return ProjectConfig(
        schema_version=schema_version,
        project_id=project_id,
        expected_protein_count=expected_count,
        runtime_root=runtime_root,
        amino_acid_fasta=amino_acid_fasta,
        nucleotide_fasta=nucleotide_fasta,
        candidate_specification=candidate_specification,
        design_brief=design_brief,
        design_variable_registry=design_variable_registry,
        objective_policy=objective_policy,
        run_root=run_root,
        target_indication=str(context.get("target_indication", "unspecified")),
        intended_host_species=str(context.get("intended_host_species", "unspecified")),
        product_modalities=tuple(modality.strip() for modality in modalities_value),
        protein_expression_host=str(context.get("protein_expression_host", "unspecified")),
        mrna_target_species=str(context.get("mrna_target_species", "unspecified")),
        project_mode=project_mode.strip() if isinstance(project_mode, str) else None,
        scientific_release_allowed=scientific_release_allowed,
        mrna_manufacturing_method=(
            mrna_manufacturing_method.strip()
            if isinstance(mrna_manufacturing_method, str)
            else None
        ),
        human_actions=_human_actions(data.get("human_actions")),
        config_path=path,
    )
