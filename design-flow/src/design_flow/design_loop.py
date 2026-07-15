"""Versioned design-round contracts shared by the workflow stages."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any


DESIGN_BRIEF_SCHEMA = "vaxflow.design-brief.v1"
VARIABLE_REGISTRY_SCHEMA = "vaxflow.design-variable-registry.v1"
OBJECTIVE_POLICY_SCHEMA = "vaxflow.objective-policy.v1"
PROPOSAL_LINEAGE_SCHEMA = "vaxflow.proposal-lineage.v1"
REDESIGN_REQUEST_SCHEMA = "vaxflow.redesign-requests.v1"
ROUND_FEEDBACK_SCHEMA = "vaxflow.round-feedback.v1"

APPROVAL_STATUSES = frozenset({"draft", "approved", "approved_for_mock_execution"})
VARIABLE_STATUSES = frozenset({"fixed", "searchable", "deferred", "forbidden"})
VARIABLE_SCOPES = frozenset({"antigen", "protein_product", "mrna_product", "portfolio"})
OBJECTIVE_DIRECTIONS = frozenset({"maximize", "minimize", "target", "satisfy"})
DECISION_ROLES = frozenset({"hard_gate", "optimize", "monitor"})
MISSING_EVIDENCE_POLICIES = frozenset({"block_release", "not_evaluated", "exclude_metric"})
REQUEST_STATUSES = frozenset({"proposed", "accepted", "rejected", "superseded"})
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


@dataclass(frozen=True)
class DesignDossier:
    brief: dict[str, Any]
    variable_registry: dict[str, Any]
    objective_policy: dict[str, Any]
    paths: dict[str, Path]
    digests: dict[str, str]

    @property
    def round_id(self) -> str:
        return str(self.brief["round"]["round_id"])

    @property
    def round_index(self) -> int:
        return int(self.brief["round"]["index"])

    @property
    def parent_round_id(self) -> str | None:
        value = self.brief["round"].get("parent_round_id")
        return str(value) if value is not None else None

    @property
    def approval_statuses(self) -> dict[str, str]:
        return {
            "design_brief": str(self.brief["status"]),
            "design_variable_registry": str(self.variable_registry["status"]),
            "objective_policy": str(self.objective_policy["status"]),
        }

    @property
    def approved_for_execution(self) -> bool:
        return all(status != "draft" for status in self.approval_statuses.values())

    def summary(self) -> dict[str, Any]:
        return {
            "round_id": self.round_id,
            "round_index": self.round_index,
            "parent_round_id": self.parent_round_id,
            "approval_statuses": self.approval_statuses,
            "approved_for_execution": self.approved_for_execution,
            "objective_count": len(self.objective_policy["objectives"]),
            "variable_count": len(self.variable_registry["variables"]),
            "searchable_variable_count": sum(
                item["status"] == "searchable"
                for item in self.variable_registry["variables"]
            ),
            "prior_feedback_request_count": len(
                self.brief.get("prior_feedback", {}).get("request_ids", [])
            ),
        }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read {label} from {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} root must be a JSON object")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _id(value: Any, field: str) -> str:
    normalized = _text(value, field)
    if not ID_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field} contains unsupported characters: {normalized!r}")
    return normalized


def _string_array(value: Any, field: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(f"{field} must be an array of non-empty strings")
    normalized = [item.strip() for item in value]
    if not allow_empty and not normalized:
        raise ValueError(f"{field} must not be empty")
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field} contains duplicate values")
    return normalized


def _approval_status(document: dict[str, Any], field: str) -> str:
    status = _text(document.get("status"), f"{field}.status")
    if status not in APPROVAL_STATUSES:
        raise ValueError(f"{field}.status must be one of {sorted(APPROVAL_STATUSES)}")
    return status


def validate_design_brief(document: dict[str, Any], *, project_id: str) -> dict[str, Any]:
    if document.get("schema_version") != DESIGN_BRIEF_SCHEMA:
        raise ValueError(f"design brief schema_version must be {DESIGN_BRIEF_SCHEMA}")
    if _text(document.get("project_id"), "design_brief.project_id") != project_id:
        raise ValueError("design brief project_id differs from the project configuration")
    _id(document.get("brief_id"), "design_brief.brief_id")
    _approval_status(document, "design_brief")
    round_record = document.get("round")
    if not isinstance(round_record, dict):
        raise ValueError("design_brief.round must be an object")
    _id(round_record.get("round_id"), "design_brief.round.round_id")
    index = round_record.get("index")
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise ValueError("design_brief.round.index must be a non-negative integer")
    parent = round_record.get("parent_round_id")
    if parent is not None:
        _id(parent, "design_brief.round.parent_round_id")
    if index == 0 and parent is not None:
        raise ValueError("round 0 cannot declare a parent_round_id")
    if index > 0 and parent is None:
        raise ValueError("rounds after round 0 require parent_round_id")
    problem = document.get("problem")
    if not isinstance(problem, dict):
        raise ValueError("design_brief.problem must be an object")
    for key in ("target_indication", "intended_host_species", "product_intent"):
        _text(problem.get(key), f"design_brief.problem.{key}")
    _string_array(problem.get("product_modalities"), "design_brief.problem.product_modalities")
    _string_array(
        document.get("success_criteria"),
        "design_brief.success_criteria",
    )
    prior = document.get("prior_feedback", {})
    if not isinstance(prior, dict):
        raise ValueError("design_brief.prior_feedback must be an object")
    source_run_id = prior.get("source_run_id")
    if source_run_id is not None:
        _id(source_run_id, "design_brief.prior_feedback.source_run_id")
    _string_array(
        prior.get("request_ids", []),
        "design_brief.prior_feedback.request_ids",
        allow_empty=True,
    )
    return document


def validate_variable_registry(
    document: dict[str, Any],
    *,
    project_id: str,
    round_id: str,
    known_stage_ids: set[str],
) -> dict[str, Any]:
    if document.get("schema_version") != VARIABLE_REGISTRY_SCHEMA:
        raise ValueError(
            f"design variable registry schema_version must be {VARIABLE_REGISTRY_SCHEMA}"
        )
    if _text(document.get("project_id"), "variable_registry.project_id") != project_id:
        raise ValueError("design variable registry project_id differs from the project")
    if _text(document.get("round_id"), "variable_registry.round_id") != round_id:
        raise ValueError("design variable registry round_id differs from the design brief")
    _id(document.get("registry_id"), "variable_registry.registry_id")
    _approval_status(document, "variable_registry")
    variables = document.get("variables")
    if not isinstance(variables, list) or not variables:
        raise ValueError("variable_registry.variables must be a non-empty array")
    seen: set[str] = set()
    for index, raw in enumerate(variables):
        field = f"variable_registry.variables[{index}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{field} must be an object")
        variable_id = _id(raw.get("variable_id"), f"{field}.variable_id")
        if variable_id in seen:
            raise ValueError(f"Duplicate design variable ID: {variable_id}")
        seen.add(variable_id)
        scope = _text(raw.get("scope"), f"{field}.scope")
        if scope not in VARIABLE_SCOPES:
            raise ValueError(f"{field}.scope must be one of {sorted(VARIABLE_SCOPES)}")
        status = _text(raw.get("status"), f"{field}.status")
        if status not in VARIABLE_STATUSES:
            raise ValueError(f"{field}.status must be one of {sorted(VARIABLE_STATUSES)}")
        stage_id = _text(raw.get("introduced_at_stage"), f"{field}.introduced_at_stage")
        if stage_id not in known_stage_ids:
            raise ValueError(f"{field}.introduced_at_stage is not a workflow stage: {stage_id}")
        _text(raw.get("description"), f"{field}.description")
        values = raw.get("allowed_values", [])
        if not isinstance(values, list):
            raise ValueError(f"{field}.allowed_values must be an array")
        if status == "searchable" and not values and not raw.get("adapter_contract"):
            raise ValueError(
                f"{field} is searchable but declares neither allowed_values nor adapter_contract"
            )
    return document


def validate_objective_policy(
    document: dict[str, Any],
    *,
    project_id: str,
    round_id: str,
    known_stage_ids: set[str],
) -> dict[str, Any]:
    if document.get("schema_version") != OBJECTIVE_POLICY_SCHEMA:
        raise ValueError(f"objective policy schema_version must be {OBJECTIVE_POLICY_SCHEMA}")
    if _text(document.get("project_id"), "objective_policy.project_id") != project_id:
        raise ValueError("objective policy project_id differs from the project")
    if _text(document.get("round_id"), "objective_policy.round_id") != round_id:
        raise ValueError("objective policy round_id differs from the design brief")
    _id(document.get("policy_id"), "objective_policy.policy_id")
    _approval_status(document, "objective_policy")
    objectives = document.get("objectives")
    if not isinstance(objectives, list) or not objectives:
        raise ValueError("objective_policy.objectives must be a non-empty array")
    seen: set[str] = set()
    for index, raw in enumerate(objectives):
        field = f"objective_policy.objectives[{index}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{field} must be an object")
        objective_id = _id(raw.get("objective_id"), f"{field}.objective_id")
        if objective_id in seen:
            raise ValueError(f"Duplicate objective ID: {objective_id}")
        seen.add(objective_id)
        stage_id = _text(raw.get("evidence_stage"), f"{field}.evidence_stage")
        if stage_id not in known_stage_ids:
            raise ValueError(f"{field}.evidence_stage is not a workflow stage: {stage_id}")
        direction = _text(raw.get("direction"), f"{field}.direction")
        if direction not in OBJECTIVE_DIRECTIONS:
            raise ValueError(f"{field}.direction must be one of {sorted(OBJECTIVE_DIRECTIONS)}")
        role = _text(raw.get("decision_role"), f"{field}.decision_role")
        if role not in DECISION_ROLES:
            raise ValueError(f"{field}.decision_role must be one of {sorted(DECISION_ROLES)}")
        missing = _text(raw.get("missing_evidence"), f"{field}.missing_evidence")
        if missing not in MISSING_EVIDENCE_POLICIES:
            raise ValueError(
                f"{field}.missing_evidence must be one of {sorted(MISSING_EVIDENCE_POLICIES)}"
            )
        _text(raw.get("metric"), f"{field}.metric")
    selection = document.get("selection")
    if not isinstance(selection, dict):
        raise ValueError("objective_policy.selection must be an object")
    _text(selection.get("strategy"), "objective_policy.selection.strategy")
    return document


def load_design_dossier(
    *,
    project_id: str,
    design_brief_path: Path,
    variable_registry_path: Path,
    objective_policy_path: Path,
    known_stage_ids: set[str],
) -> DesignDossier:
    paths = {
        "design_brief": design_brief_path.resolve(),
        "design_variable_registry": variable_registry_path.resolve(),
        "objective_policy": objective_policy_path.resolve(),
    }
    for name, path in paths.items():
        if not path.is_file():
            raise ValueError(f"Configured {name} not found: {path}")
    brief = validate_design_brief(
        _load_object(paths["design_brief"], "design brief"),
        project_id=project_id,
    )
    round_id = str(brief["round"]["round_id"])
    registry = validate_variable_registry(
        _load_object(paths["design_variable_registry"], "design variable registry"),
        project_id=project_id,
        round_id=round_id,
        known_stage_ids=known_stage_ids,
    )
    policy = validate_objective_policy(
        _load_object(paths["objective_policy"], "objective policy"),
        project_id=project_id,
        round_id=round_id,
        known_stage_ids=known_stage_ids,
    )
    return DesignDossier(
        brief=brief,
        variable_registry=registry,
        objective_policy=policy,
        paths=paths,
        digests={name: _sha256_file(path) for name, path in paths.items()},
    )


def proposal_lineage_document(
    *,
    project_id: str,
    run_id: str,
    round_id: str,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": PROPOSAL_LINEAGE_SCHEMA,
        "project_id": project_id,
        "run_id": run_id,
        "round_id": round_id,
        "records": records,
    }


def redesign_request_document(
    *,
    project_id: str,
    run_id: str,
    round_id: str,
    stage_id: str,
    requests: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(requests):
        if not isinstance(raw, dict):
            raise ValueError(f"redesign request {index} must be an object")
        request_id = _id(raw.get("request_id"), f"redesign_requests[{index}].request_id")
        if request_id in seen:
            raise ValueError(f"Duplicate redesign request ID: {request_id}")
        seen.add(request_id)
        status = _text(raw.get("status", "proposed"), f"redesign_requests[{index}].status")
        if status not in REQUEST_STATUSES:
            raise ValueError(f"Invalid redesign request status: {status}")
        candidate_id = raw.get("candidate_id")
        if candidate_id is not None:
            _id(candidate_id, f"redesign_requests[{index}].candidate_id")
        normalized.append(
            {
                "request_id": request_id,
                "status": status,
                "candidate_id": candidate_id,
                "trigger": _text(raw.get("trigger"), f"redesign_requests[{index}].trigger"),
                "evidence_ref": _text(
                    raw.get("evidence_ref"),
                    f"redesign_requests[{index}].evidence_ref",
                ),
                "requested_variable_ids": _string_array(
                    raw.get("requested_variable_ids", []),
                    f"redesign_requests[{index}].requested_variable_ids",
                    allow_empty=True,
                ),
                "instruction": _text(
                    raw.get("instruction"),
                    f"redesign_requests[{index}].instruction",
                ),
                "authority": _text(
                    raw.get("authority", "deterministic_rule"),
                    f"redesign_requests[{index}].authority",
                ),
            }
        )
    return {
        "schema_version": REDESIGN_REQUEST_SCHEMA,
        "project_id": project_id,
        "run_id": run_id,
        "round_id": round_id,
        "stage_id": stage_id,
        "request_count": len(normalized),
        "requests": normalized,
        "consumption_contract": (
            "Requests are proposals for the next immutable design round. They do not mutate "
            "the current candidate or authorize scientific release."
        ),
    }


def validate_redesign_request_document(
    document: dict[str, Any],
    *,
    project_id: str,
    run_id: str,
    round_id: str,
    stage_id: str,
) -> bool:
    try:
        if document.get("schema_version") != REDESIGN_REQUEST_SCHEMA:
            return False
        rebuilt = redesign_request_document(
            project_id=project_id,
            run_id=run_id,
            round_id=round_id,
            stage_id=stage_id,
            requests=document.get("requests", []),
        )
    except (KeyError, TypeError, ValueError):
        return False
    return document == rebuilt


def round_feedback_document(
    *,
    project_id: str,
    run_id: str,
    round_id: str,
    source_documents: list[dict[str, Any]],
) -> dict[str, Any]:
    requests = []
    source_stages = []
    seen: set[str] = set()
    for document in source_documents:
        stage_id = _text(document.get("stage_id"), "redesign source stage_id")
        source_stages.append(stage_id)
        for request in document.get("requests", []):
            request_id_value = _text(request.get("request_id"), "redesign request_id")
            if request_id_value in seen:
                raise ValueError(f"Duplicate redesign request across stages: {request_id_value}")
            seen.add(request_id_value)
            requests.append({"origin_stage": stage_id, **request})
    requests.sort(key=lambda item: (item["origin_stage"], item["request_id"]))
    return {
        "schema_version": ROUND_FEEDBACK_SCHEMA,
        "project_id": project_id,
        "run_id": run_id,
        "round_id": round_id,
        "source_stages": sorted(set(source_stages)),
        "request_count": len(requests),
        "requests": requests,
        "next_round_contract": (
            "Only accepted request IDs may be listed in the next design brief prior_feedback. "
            "This document does not accept requests automatically."
        ),
    }


def request_id(stage_id: str, candidate_id: str | None, trigger: str, ordinal: int) -> str:
    identity = f"{stage_id}|{candidate_id or 'project'}|{trigger}|{ordinal}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"redesign-{digest}"


def default_design_documents(
    *,
    project_id: str,
    target_indication: str,
    intended_host_species: str,
    product_modalities: list[str],
    mock_approved: bool = False,
) -> dict[str, dict[str, Any]]:
    """Return conservative round-0 templates without inventing biological thresholds."""

    status = "approved_for_mock_execution" if mock_approved else "draft"
    round_id = "round-000"
    modalities = product_modalities or ["unspecified"]
    brief = {
        "schema_version": DESIGN_BRIEF_SCHEMA,
        "brief_id": f"{project_id}-round-000-brief",
        "project_id": project_id,
        "status": status,
        "round": {"round_id": round_id, "index": 0, "parent_round_id": None},
        "problem": {
            "target_indication": target_indication,
            "intended_host_species": intended_host_species,
            "product_modalities": modalities,
            "product_intent": (
                "Establish a traceable round-0 candidate portfolio and validate the complete "
                "computational workflow; no efficacy or safety claim is authorized."
            ),
        },
        "success_criteria": [
            "Every candidate and product sequence has immutable identity and complete lineage.",
            "Hard constraints fail explicitly and missing evidence remains not evaluated.",
            "The round produces a reproducible evidence table and next-round feedback requests.",
        ],
        "prior_feedback": {"source_run_id": None, "request_ids": []},
    }
    registry = {
        "schema_version": VARIABLE_REGISTRY_SCHEMA,
        "registry_id": f"{project_id}-round-000-variables",
        "project_id": project_id,
        "round_id": round_id,
        "status": status,
        "variables": [
            {
                "variable_id": "antigen.recipe",
                "scope": "antigen",
                "status": "searchable",
                "introduced_at_stage": "candidate_specification",
                "description": "Exact source segments, order, and explicitly declared connecting sequence.",
                "allowed_values": ["source_control", "manual_seed", "adapter_generated"],
            },
            {
                "variable_id": "protein.expression_elements",
                "scope": "protein_product",
                "status": "deferred",
                "introduced_at_stage": "protein_product_design",
                "description": "Expression-only signal, tag, cleavage, and purification elements.",
                "allowed_values": [],
            },
            {
                "variable_id": "mrna.synonymous_coding_sequence",
                "scope": "mrna_product",
                "status": "searchable",
                "introduced_at_stage": "mrna_product_design",
                "description": "Synonymous coding sequence constrained to preserve the approved protein.",
                "allowed_values": [],
                "adapter_contract": "vaxflow.synonymous-cds-generator.v1",
            },
            {
                "variable_id": "portfolio.selection",
                "scope": "portfolio",
                "status": "searchable",
                "introduced_at_stage": "integrated_ranking",
                "description": "Diverse candidate subset chosen from explicit evidence and controls.",
                "allowed_values": ["pareto_frontier", "control_preserving_portfolio"],
            },
        ],
    }
    policy = {
        "schema_version": OBJECTIVE_POLICY_SCHEMA,
        "policy_id": f"{project_id}-round-000-objectives",
        "project_id": project_id,
        "round_id": round_id,
        "status": status,
        "objectives": [
            {
                "objective_id": "sequence.translation_identity",
                "evidence_stage": "program_and_source_intake",
                "metric": "translation_matches",
                "direction": "satisfy",
                "decision_role": "hard_gate",
                "missing_evidence": "block_release",
            },
            {
                "objective_id": "structure.model_confidence",
                "evidence_stage": "protein_structure_assessment",
                "metric": "mean_plddt_and_ptm",
                "direction": "maximize",
                "decision_role": "monitor",
                "missing_evidence": "not_evaluated",
            },
            {
                "objective_id": "immune.evidence_coverage",
                "evidence_stage": "immune_evidence_assessment",
                "metric": "declared_panel_evidence_coverage",
                "direction": "maximize",
                "decision_role": "monitor",
                "missing_evidence": "not_evaluated",
            },
            {
                "objective_id": "developability.review_liabilities",
                "evidence_stage": "developability_assessment",
                "metric": "review_liability_count",
                "direction": "minimize",
                "decision_role": "optimize",
                "missing_evidence": "exclude_metric",
            },
            {
                "objective_id": "mrna.translation_identity",
                "evidence_stage": "mrna_product_design",
                "metric": "translation_verified",
                "direction": "satisfy",
                "decision_role": "hard_gate",
                "missing_evidence": "block_release",
            },
        ],
        "selection": {
            "strategy": "hard_gates_then_pareto",
            "formal_release_enabled": False,
        },
    }
    return {
        "design_brief": brief,
        "design_variable_registry": registry,
        "objective_policy": policy,
    }
