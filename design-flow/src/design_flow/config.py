"""Project configuration loading and validation."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from .domain import ProjectConfig


PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return value


def _required_text(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


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

    return ProjectConfig(
        schema_version=schema_version,
        project_id=project_id,
        expected_protein_count=expected_count,
        amino_acid_fasta=(project_dir / _required_text(inputs, "amino_acid_fasta")).resolve(),
        nucleotide_fasta=(project_dir / _required_text(inputs, "nucleotide_fasta")).resolve(),
        run_root=(project_dir / str(outputs.get("run_root", "runs"))).resolve(),
        protein_expression_host=str(context.get("protein_expression_host", "unspecified")),
        mrna_target_species=str(context.get("mrna_target_species", "unspecified")),
        config_path=path,
    )
