"""Deterministic Stage 2 proposal expansion with immutable lineage."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
from html import escape
from io import StringIO
from itertools import permutations, product
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

from .config import ProjectConfig, load_project_config
from .qc import CANONICAL_AMINO_ACIDS
from .verification import (
    ARTIFACT_INDEX_FILENAME,
    build_artifact_index,
    sha256_file,
    verify_run,
)
from .workflow import STAGE_BY_ID


GRAMMAR_SCHEMA = "vaxflow.stage2-proposal-grammar.v1"
PROPOSAL_BATCH_SCHEMA = "vaxflow.stage2-proposal-batch.v1"
GENERATION_CONTEXT_SCHEMA = "vaxflow.stage2-generation-context.v1"
GENERATOR_ID = "deterministic-combinatorial-enumerator"
GENERATOR_VERSION = "3"
SUPPORTED_GENERATOR_VERSIONS = frozenset({"1", "2", "3"})
CANDIDATE_STAGE_ID = "candidate_specification"
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
APPROVAL_STATUSES = frozenset({"approved", "approved_for_mock_execution"})
ORDER_POLICIES = frozenset({"fixed", "all_permutations"})
MODEL_ROLE_STATUSES = frozenset({"active", "deferred", "not_applicable"})
MODEL_ROLES = frozenset(
    {"proposal_generator", "sequence_optimizer", "evaluator", "product_optimizer"}
)
EXPECTED_FILES = {
    "inputs/generation_context.json",
    "inputs/proposal_grammar.json",
    "inputs/seed_candidate_batch.json",
    "candidate_specification.generated.json",
    "proposal_batch.json",
    "proposals.csv",
    "proposals.fasta",
    "report.html",
    ARTIFACT_INDEX_FILENAME,
}


@dataclass(frozen=True)
class SeedRun:
    directory: Path
    manifest: dict[str, Any]
    candidate_batch: dict[str, Any]
    stage1_run_id: str
    stage1_run_path: str
    artifact_index_sha256: str
    candidate_batch_sha256: str


def _json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError(f"Value cannot be represented as canonical JSON: {error}") from error


def _document_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _sequence_sha256(sequence: str) -> str:
    return hashlib.sha256(sequence.encode("ascii")).hexdigest()


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read {label} from {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} root must be a JSON object")
    return value


def _text(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    normalized = value.strip()
    if value != normalized:
        raise ValueError(f"{field} must not contain surrounding whitespace")
    if not allow_empty and not normalized:
        raise ValueError(f"{field} must be a non-empty string")
    return normalized


def _identifier(value: Any, field: str) -> str:
    normalized = _text(value, field)
    if not ID_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field} contains unsupported characters: {normalized!r}")
    return normalized


def _positive_integer(value: Any, field: str, *, minimum: int = 1) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{field} must be an integer >= {minimum}")
    return value


def _resolve_seed_run(
    config: ProjectConfig,
    seed_run_dir: str | Path | None,
) -> SeedRun:
    if seed_run_dir is None:
        latest = _load_object(config.run_root / "latest.json", "latest run pointer")
        seed_run_dir = _text(latest.get("run_path"), "latest.run_path")
    directory = Path(seed_run_dir).expanduser().resolve()
    if not directory.is_dir():
        raise ValueError(f"Stage 2 seed run does not exist: {directory}")
    verification = verify_run(directory)
    if verification["status"] != "pass":
        raise ValueError(
            "Stage 2 seed run verification failed: "
            + "; ".join(verification["errors"][:5])
        )
    manifest = _load_object(directory / "manifest.json", "Stage 2 seed manifest")
    if manifest.get("project_id") != config.project_id:
        raise ValueError("Stage 2 seed run belongs to another project")
    if manifest.get("current_stage") != CANDIDATE_STAGE_ID:
        raise ValueError(
            f"Proposal generation requires a Stage 2 seed run, got "
            f"{manifest.get('current_stage')}"
        )
    lineage = manifest.get("lineage")
    if not isinstance(lineage, dict):
        raise ValueError("Stage 2 seed manifest has no parent lineage")
    stage1_run_id = _text(lineage.get("parent_run_id"), "lineage.parent_run_id")
    stage1_run_path = _text(lineage.get("parent_run_path"), "lineage.parent_run_path")
    candidate_batch_path = directory / "nodes" / CANDIDATE_STAGE_ID / "candidate_batch.json"
    candidate_batch = _load_object(candidate_batch_path, "Stage 2 seed candidate batch")
    return SeedRun(
        directory=directory,
        manifest=manifest,
        candidate_batch=candidate_batch,
        stage1_run_id=stage1_run_id,
        stage1_run_path=stage1_run_path,
        artifact_index_sha256=sha256_file(directory / ARTIFACT_INDEX_FILENAME),
        candidate_batch_sha256=sha256_file(candidate_batch_path),
    )


def _validate_grammar(
    grammar: dict[str, Any],
    *,
    project_id: str,
    round_id: str,
    candidate_keys: set[str],
) -> dict[str, Any]:
    if grammar.get("schema_version") != GRAMMAR_SCHEMA:
        raise ValueError(f"proposal grammar schema_version must be {GRAMMAR_SCHEMA}")
    if _text(grammar.get("project_id"), "grammar.project_id") != project_id:
        raise ValueError("proposal grammar project_id differs from the project")
    if _text(grammar.get("design_round_id"), "grammar.design_round_id") != round_id:
        raise ValueError("proposal grammar design_round_id differs from the seed run")
    _identifier(grammar.get("grammar_id"), "grammar.grammar_id")
    status = _text(grammar.get("status"), "grammar.status")
    if status not in APPROVAL_STATUSES:
        raise ValueError(
            "proposal grammar must be approved or approved_for_mock_execution"
        )

    linkers = grammar.get("linkers")
    if not isinstance(linkers, list) or not linkers:
        raise ValueError("grammar.linkers must be a non-empty array")
    linker_ids: set[str] = set()
    for index, raw in enumerate(linkers):
        field = f"grammar.linkers[{index}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{field} must be an object")
        linker_id = _identifier(raw.get("linker_id"), f"{field}.linker_id")
        if linker_id in linker_ids:
            raise ValueError(f"Duplicate linker_id: {linker_id}")
        linker_ids.add(linker_id)
        sequence = _text(raw.get("sequence", ""), f"{field}.sequence", allow_empty=True)
        if set(sequence) - CANONICAL_AMINO_ACIDS:
            raise ValueError(f"{field}.sequence contains non-canonical amino acids")
        _text(raw.get("class"), f"{field}.class")
        _text(raw.get("rationale"), f"{field}.rationale")

    templates = grammar.get("composition_templates")
    if not isinstance(templates, list) or not templates:
        raise ValueError("grammar.composition_templates must be a non-empty array")
    template_ids: set[str] = set()
    for index, raw in enumerate(templates):
        field = f"grammar.composition_templates[{index}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{field} must be an object")
        template_id = _identifier(raw.get("template_id"), f"{field}.template_id")
        if template_id in template_ids:
            raise ValueError(f"Duplicate template_id: {template_id}")
        template_ids.add(template_id)
        slots = raw.get("component_slots")
        if not isinstance(slots, list) or len(slots) < 2:
            raise ValueError(f"{field}.component_slots must contain at least two slots")
        slot_ids: set[str] = set()
        for slot_index, slot in enumerate(slots):
            slot_field = f"{field}.component_slots[{slot_index}]"
            if not isinstance(slot, dict):
                raise ValueError(f"{slot_field} must be an object")
            slot_id = _identifier(slot.get("slot_id"), f"{slot_field}.slot_id")
            if slot_id in slot_ids:
                raise ValueError(f"Duplicate slot_id in {template_id}: {slot_id}")
            slot_ids.add(slot_id)
            keys = slot.get("candidate_keys")
            if not isinstance(keys, list) or not keys or not all(
                isinstance(key, str) and key.strip() for key in keys
            ):
                raise ValueError(f"{slot_field}.candidate_keys must be non-empty strings")
            normalized_keys = [key.strip() for key in keys]
            if normalized_keys != keys:
                raise ValueError(
                    f"{slot_field}.candidate_keys must not contain surrounding whitespace"
                )
            if len(normalized_keys) != len(set(normalized_keys)):
                raise ValueError(f"{slot_field}.candidate_keys contains duplicates")
            unknown = sorted(set(normalized_keys) - candidate_keys)
            if unknown:
                raise ValueError(f"{slot_field} references unknown candidates: {unknown}")
        order_policy = _text(raw.get("order_policy"), f"{field}.order_policy")
        if order_policy not in ORDER_POLICIES:
            raise ValueError(f"{field}.order_policy must be one of {sorted(ORDER_POLICIES)}")
        selected_linkers = raw.get("linker_ids")
        if (
            not isinstance(selected_linkers, list)
            or not selected_linkers
            or not all(isinstance(value, str) and value.strip() for value in selected_linkers)
        ):
            raise ValueError(f"{field}.linker_ids must be a non-empty array")
        unknown_linkers = sorted(
            {value.strip() for value in selected_linkers} - linker_ids
        )
        if [value.strip() for value in selected_linkers] != selected_linkers:
            raise ValueError(
                f"{field}.linker_ids must not contain surrounding whitespace"
            )
        if unknown_linkers:
            raise ValueError(f"{field} references unknown linkers: {unknown_linkers}")
        _text(raw.get("rationale"), f"{field}.rationale")

    constraints = grammar.get("constraints")
    if not isinstance(constraints, dict):
        raise ValueError("grammar.constraints must be an object")
    _positive_integer(constraints.get("maximum_aa_length"), "constraints.maximum_aa_length")
    _positive_integer(
        constraints.get("maximum_generated_candidates"),
        "constraints.maximum_generated_candidates",
    )

    feedback_ids = grammar.get("consumed_feedback_request_ids", [])
    if not isinstance(feedback_ids, list) or not all(
        isinstance(value, str) and value.strip() for value in feedback_ids
    ):
        raise ValueError("grammar.consumed_feedback_request_ids must be a string array")
    if [value.strip() for value in feedback_ids] != feedback_ids:
        raise ValueError(
            "grammar.consumed_feedback_request_ids must not contain surrounding whitespace"
        )
    if len(feedback_ids) != len(set(feedback_ids)):
        raise ValueError("grammar.consumed_feedback_request_ids contains duplicates")

    roles = grammar.get("model_roles", [])
    if not isinstance(roles, list):
        raise ValueError("grammar.model_roles must be an array")
    adapter_ids: set[str] = set()
    for index, raw in enumerate(roles):
        field = f"grammar.model_roles[{index}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{field} must be an object")
        adapter_id = _identifier(raw.get("adapter_id"), f"{field}.adapter_id")
        if adapter_id in adapter_ids:
            raise ValueError(f"Duplicate model-role adapter_id: {adapter_id}")
        adapter_ids.add(adapter_id)
        role = _text(raw.get("role"), f"{field}.role")
        if role not in MODEL_ROLES:
            raise ValueError(f"{field}.role must be one of {sorted(MODEL_ROLES)}")
        stage_id = _text(raw.get("stage_id"), f"{field}.stage_id")
        if stage_id not in STAGE_BY_ID:
            raise ValueError(f"{field}.stage_id is not a workflow stage")
        role_status = _text(raw.get("status"), f"{field}.status")
        if role_status not in MODEL_ROLE_STATUSES:
            raise ValueError(
                f"{field}.status must be one of {sorted(MODEL_ROLE_STATUSES)}"
            )
        if role_status == "active" and adapter_id != GENERATOR_ID:
            raise ValueError(
                f"{field} cannot mark an adapter active before it has executed"
            )
        if adapter_id == GENERATOR_ID and (
            role != "proposal_generator"
            or stage_id != CANDIDATE_STAGE_ID
            or role_status != "active"
        ):
            raise ValueError(
                f"{field} must register {GENERATOR_ID} as the active Stage 2 "
                "proposal_generator"
            )
        _text(raw.get("reason"), f"{field}.reason")
    return grammar


def _proposal_from_seed(candidate: dict[str, Any]) -> dict[str, Any]:
    proposal = candidate.get("proposal")
    if not isinstance(proposal, dict):
        raise ValueError(f"Seed candidate {candidate.get('candidate_key')} has no proposal")
    return {
        "generator": proposal["generator"],
        "parent_candidate_keys": proposal["parent_candidate_keys"],
        "transformation": proposal["transformation"],
        "rationale": proposal["rationale"],
        "feedback_request_ids": proposal["feedback_request_ids"],
    }


def _manual_record_from_seed(candidate: dict[str, Any]) -> dict[str, Any]:
    candidate_type = candidate.get("candidate_type")
    if candidate_type not in {"truncation", "fusion", "manual_control"}:
        raise ValueError(
            f"Cannot carry seed candidate type into Stage 2 specification: {candidate_type}"
        )
    claimed = candidate.get("claimed_annotation")
    claimed = claimed if isinstance(claimed, dict) else {}
    record: dict[str, Any] = {
        "candidate_key": candidate["candidate_key"],
        "display_name": candidate["display_name"],
        "candidate_type": candidate_type,
        "amino_acid_sequence": candidate["amino_acid_sequence"],
        "annotation_status": candidate["annotation_status"],
        "proposal": _proposal_from_seed(candidate),
    }
    nucleotide = candidate.get("nucleotide_sequence")
    if isinstance(nucleotide, str) and nucleotide:
        record["nucleotide_sequence"] = nucleotide
    if candidate_type == "truncation":
        record.update(
            {
                "claimed_source_id": claimed.get("source_protein_id"),
                "claimed_source_start": claimed.get("source_start"),
                "claimed_source_end": claimed.get("source_end"),
            }
        )
    elif candidate_type == "fusion":
        record["claimed_component_keys"] = claimed.get("component_keys", [])
    return record


def _ordered_component_sets(
    template: dict[str, Any],
) -> list[tuple[str, ...]]:
    slot_options = [
        [str(key).strip() for key in slot["candidate_keys"]]
        for slot in template["component_slots"]
    ]
    ordered: set[tuple[str, ...]] = set()
    for selected in product(*slot_options):
        if len(selected) != len(set(selected)):
            continue
        if template["order_policy"] == "fixed":
            ordered.add(tuple(selected))
        else:
            ordered.update(permutations(selected))
    return sorted(ordered)


def _wrap_fasta(sequence: str, width: int = 80) -> str:
    return "\n".join(
        sequence[offset : offset + width]
        for offset in range(0, len(sequence), width)
    )


def _csv_text(records: list[dict[str, Any]]) -> str:
    fields = [
        "candidate_key",
        "template_id",
        "linker_id",
        "component_count",
        "ordered_parent_keys",
        "aa_length",
        "amino_acid_sha256",
    ]
    handle = StringIO(newline="")
    writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for record in records:
        writer.writerow(
            {
                **{field: record.get(field, "") for field in fields},
                "ordered_parent_keys": "|".join(record["ordered_parent_keys"]),
            }
        )
    return handle.getvalue()


def _fasta_text(records: list[dict[str, Any]]) -> str:
    return "".join(
        f">{record['candidate_key']} template={record['template_id']} "
        f"linker={record['linker_id']} length={record['aa_length']}\n"
        f"{_wrap_fasta(record['amino_acid_sequence'])}\n"
        for record in records
    )


def _generation_identity(
    context: dict[str, Any],
    grammar: dict[str, Any],
    generated: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
) -> str:
    payload = {
        "schema_version": PROPOSAL_BATCH_SCHEMA,
        "project_id": context["project_id"],
        "design_round_id": context["design_round_id"],
        "seed_run_id": context["seed_run_id"],
        "seed_artifact_index_sha256": context["seed_artifact_index_sha256"],
        "seed_candidate_batch_sha256": context["seed_candidate_batch_sha256"],
        "proposal_grammar_sha256": context["proposal_grammar_sha256"],
        "generator": context.get(
            "generator",
            {"id": GENERATOR_ID, "version": "1"},
        ),
        "grammar": grammar,
        "generated": [
            {key: value for key, value in record.items() if key != "amino_acid_sequence"}
            for record in generated
        ],
        "skipped": skipped,
    }
    return _document_sha256(payload)


def _build_generation(
    context: dict[str, Any],
    seed_batch: dict[str, Any],
    grammar: dict[str, Any],
) -> dict[str, Any]:
    if context.get("schema_version") != GENERATION_CONTEXT_SCHEMA:
        raise ValueError(
            f"generation context schema_version must be {GENERATION_CONTEXT_SCHEMA}"
        )
    project_id = _text(context.get("project_id"), "context.project_id")
    context_round_id = _text(
        context.get("design_round_id"),
        "context.design_round_id",
    )
    seed_run_id = _text(context.get("seed_run_id"), "context.seed_run_id")
    generator = context.get(
        "generator",
        {"id": GENERATOR_ID, "version": "1"},
    )
    if not isinstance(generator, dict) or generator.get("id") != GENERATOR_ID:
        raise ValueError("generation context declares an unsupported generator")
    generator_version = _text(generator.get("version"), "context.generator.version")
    if generator_version not in SUPPORTED_GENERATOR_VERSIONS:
        raise ValueError(
            f"Unsupported {GENERATOR_ID} version: {generator_version}"
        )
    candidates = seed_batch.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("seed candidate batch has no candidates")
    candidate_by_key = {
        candidate.get("candidate_key"): candidate
        for candidate in candidates
        if isinstance(candidate, dict) and isinstance(candidate.get("candidate_key"), str)
    }
    if len(candidate_by_key) != len(candidates):
        raise ValueError("seed candidate keys are missing or duplicated")
    round_id = _text(seed_batch.get("design_round_id"), "seed.design_round_id")
    if seed_batch.get("project_id") != project_id:
        raise ValueError("seed candidate batch belongs to another project")
    if round_id != context_round_id:
        raise ValueError("seed candidate batch round differs from generation context")
    if seed_batch.get("run_id") != seed_run_id:
        raise ValueError("seed candidate batch run_id differs from generation context")
    _validate_grammar(
        grammar,
        project_id=project_id,
        round_id=round_id,
        candidate_keys=set(candidate_by_key),
    )
    linker_by_id = {linker["linker_id"]: linker for linker in grammar["linkers"]}
    maximum_length = int(grammar["constraints"]["maximum_aa_length"])
    maximum_candidates = int(
        grammar["constraints"]["maximum_generated_candidates"]
    )
    seen_sequences = {
        candidate["amino_acid_sha256"]: candidate["candidate_key"]
        for candidate in candidates
    }
    generated: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for template in grammar["composition_templates"]:
        for ordered_parent_keys in _ordered_component_sets(template):
            parent_sequences = [
                _text(
                    candidate_by_key[key].get("amino_acid_sequence"),
                    f"candidate[{key}].amino_acid_sequence",
                )
                for key in ordered_parent_keys
            ]
            for linker_id in template["linker_ids"]:
                linker = linker_by_id[linker_id]
                linker_sequence = linker["sequence"]
                sequence = linker_sequence.join(parent_sequences)
                sequence_sha = _sequence_sha256(sequence)
                proposal_identity = _document_sha256(
                    {
                        "template_id": template["template_id"],
                        "ordered_parent_keys": ordered_parent_keys,
                        "linker_id": linker_id,
                        "amino_acid_sha256": sequence_sha,
                    }
                )
                candidate_key = (
                    f"gen-{template['template_id']}-{linker_id}-{proposal_identity[:12]}"
                )
                base = {
                    "candidate_key": candidate_key,
                    "template_id": template["template_id"],
                    "linker_id": linker_id,
                    "linker_class": linker["class"],
                    "component_count": len(ordered_parent_keys),
                    "ordered_parent_keys": list(ordered_parent_keys),
                    "aa_length": len(sequence),
                    "amino_acid_sha256": sequence_sha,
                }
                if len(sequence) > maximum_length:
                    skipped.append(
                        {**base, "reason": "maximum_aa_length_exceeded"}
                    )
                    continue
                duplicate_of = seen_sequences.get(sequence_sha)
                if duplicate_of is not None:
                    skipped.append(
                        {**base, "reason": "duplicate_sequence", "duplicate_of": duplicate_of}
                    )
                    continue
                seen_sequences[sequence_sha] = candidate_key
                generated.append({**base, "amino_acid_sequence": sequence})
    if len(generated) > maximum_candidates:
        raise ValueError(
            f"Grammar generated {len(generated)} unique candidates, exceeding explicit "
            f"maximum_generated_candidates={maximum_candidates}"
        )

    identity = _generation_identity(context, grammar, generated, skipped)
    source_controls: list[str] = []
    manual_candidates: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.get("candidate_type") == "source_control":
            components = candidate.get("inferred_components")
            if not isinstance(components, list) or len(components) != 1:
                raise ValueError("source control has no unique source component")
            source_controls.append(
                _text(components[0].get("source_protein_id"), "source_protein_id")
            )
        else:
            manual_candidates.append(_manual_record_from_seed(candidate))
    feedback_ids = list(grammar.get("consumed_feedback_request_ids", []))
    for record in generated:
        linker = linker_by_id[record["linker_id"]]
        template = next(
            item
            for item in grammar["composition_templates"]
            if item["template_id"] == record["template_id"]
        )
        manual_candidates.append(
            {
                "candidate_key": record["candidate_key"],
                "display_name": (
                    f"Generated {record['template_id']}: "
                    + " -> ".join(record["ordered_parent_keys"])
                    + f" [{record['linker_id']}]"
                ),
                "candidate_type": "fusion",
                "amino_acid_sequence": record["amino_acid_sequence"],
                "claimed_component_keys": record["ordered_parent_keys"],
                "annotation_status": "unreviewed",
                "proposal": {
                    "generator": {
                        "id": GENERATOR_ID,
                        "version": generator_version,
                        "parameters": {
                            "grammar_id": grammar["grammar_id"],
                            "template_id": record["template_id"],
                            "linker_id": record["linker_id"],
                            "linker_sequence": linker["sequence"],
                            "seed_run_id": context["seed_run_id"],
                        },
                    },
                    "parent_candidate_keys": record["ordered_parent_keys"],
                    "transformation": "ordered_component_concatenation",
                    "rationale": template["rationale"],
                    "feedback_request_ids": feedback_ids,
                },
            }
        )
    specification = {
        "schema_version": 1,
        "specification_id": f"{grammar['grammar_id']}-{identity[:12]}",
        "batch_label": grammar.get(
            "batch_label",
            f"Expanded {round_id} proposal pool from {context['seed_run_id']}",
        ),
        "design_round_id": round_id,
        "release_mode": "provisional",
        "include_source_controls": source_controls,
        "manual_candidates": manual_candidates,
        "generation_grammar": {
            "status": "approved",
            "approval_scope": grammar["status"],
            "generate_new_candidates": False,
            "structure_max_length": maximum_length,
            "materialized_generator": {
                "id": GENERATOR_ID,
                "version": generator_version,
                "generation_identity": identity,
                "grammar_id": grammar["grammar_id"],
            },
            "model_roles": grammar.get("model_roles", []),
        },
    }
    specification_sha = (
        _document_sha256(specification)
        if generator_version == "1"
        else hashlib.sha256(_json_text(specification).encode("utf-8")).hexdigest()
    )
    proposal_batch = {
        "schema_version": PROPOSAL_BATCH_SCHEMA,
        "generation_identity": identity,
        "project_id": context["project_id"],
        "design_round_id": round_id,
        "status": "materialized_for_mock_evaluation",
        "source": {
            "seed_run_id": context["seed_run_id"],
            "seed_artifact_index_sha256": context["seed_artifact_index_sha256"],
            "seed_candidate_batch_sha256": context["seed_candidate_batch_sha256"],
            "stage1_run_id": context["stage1_run_id"],
        },
        "generator": {"id": GENERATOR_ID, "version": generator_version},
        "grammar": {
            "grammar_id": grammar["grammar_id"],
            "status": grammar["status"],
            "sha256": context["proposal_grammar_sha256"],
        },
        "counts": {
            "seed_candidates": len(candidates),
            "generated_candidates": len(generated),
            "skipped_candidates": len(skipped),
            "total_candidate_specification_records": len(candidates) + len(generated),
        },
        "generated_candidates": [
            {key: value for key, value in record.items() if key != "amino_acid_sequence"}
            for record in generated
        ],
        "skipped_candidates": skipped,
        "model_roles": grammar.get("model_roles", []),
        "output": {
            "candidate_specification": "candidate_specification.generated.json",
            "candidate_specification_sha256": specification_sha,
            "generated_fasta": "proposals.fasta",
            "generated_csv": "proposals.csv",
            "report": "report.html",
            "stage1_source_run_path": context["stage1_run_path"],
        },
        "limitations": [
            "Generated records are technical proposals, not approved vaccine designs.",
            "No internal antigen residue is mutated by this grammar.",
            "Model-role registration does not imply that a deferred adapter was executed.",
            "Structure, immune evidence, developability, product design, and experiments remain downstream.",
        ],
    }
    return {
        "identity": identity,
        "specification": specification,
        "proposal_batch": proposal_batch,
        "generated": generated,
        "skipped": skipped,
    }


def _render_report(result: dict[str, Any], grammar: dict[str, Any]) -> str:
    batch = result["proposal_batch"]
    counts = batch["counts"]
    generated = result["generated"]
    template_counts: dict[tuple[str, str], int] = {}
    for record in generated:
        key = (record["template_id"], record["linker_id"])
        template_counts[key] = template_counts.get(key, 0) + 1
    matrix_rows = "".join(
        "<tr>"
        f"<td><code>{escape(template_id)}</code></td>"
        f"<td><code>{escape(linker_id)}</code></td>"
        f"<td>{count}</td>"
        "</tr>"
        for (template_id, linker_id), count in sorted(template_counts.items())
    )
    role_rows = "".join(
        "<tr>"
        f"<td><code>{escape(role['adapter_id'])}</code></td>"
        f"<td>{escape(role['role'])}</td>"
        f"<td><code>{escape(role['stage_id'])}</code></td>"
        f"<td><span class='state {escape(role['status'])}'>{escape(role['status'])}</span></td>"
        f"<td>{escape(role['reason'])}</td>"
        "</tr>"
        for role in grammar.get("model_roles", [])
    )
    candidate_rows = "".join(
        "<tr>"
        f"<td><code>{escape(record['candidate_key'])}</code></td>"
        f"<td>{escape(record['template_id'])}</td>"
        f"<td>{escape(' -> '.join(record['ordered_parent_keys']))}</td>"
        f"<td><code>{escape(record['linker_id'])}</code></td>"
        f"<td>{record['aa_length']}</td>"
        f"<td><code>{escape(record['amino_acid_sha256'][:12])}</code></td>"
        "</tr>"
        for record in generated
    )
    minimum = min((record["aa_length"] for record in generated), default=0)
    maximum = max((record["aa_length"] for record in generated), default=0)
    generator_version = str(batch.get("generator", {}).get("version", "1"))
    report_seed_count = 9 if generator_version in {"1", "2"} else counts["seed_candidates"]
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stage 2 Proposal Kitchen</title>
<style>
:root{{--ink:#17211b;--muted:#5d6b63;--line:#d6ded8;--paper:#f7f9f7;--white:#fff;--green:#176b45;--amber:#9a5d00;--red:#9f2f2f}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 Arial,"Noto Sans SC",sans-serif;letter-spacing:0}}
header{{background:#163c2d;color:white;padding:36px max(24px,calc((100vw - 1240px)/2)) 30px}}h1{{margin:0 0 8px;font-size:32px}}header p{{margin:0;color:#d9e8df;max-width:900px}}
main{{max-width:1240px;margin:auto;padding:28px 24px 64px}}section{{margin:0 0 34px}}h2{{font-size:20px;margin:0 0 12px}}.lead{{color:var(--muted);max-width:980px}}
.metrics{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));border:1px solid var(--line);background:var(--white)}}.metric{{padding:18px;border-right:1px solid var(--line)}}.metric:last-child{{border:0}}.metric b{{display:block;font-size:26px;color:var(--green)}}.metric span{{color:var(--muted)}}
.table{{overflow:auto;border:1px solid var(--line);background:var(--white)}}table{{border-collapse:collapse;width:100%;min-width:760px}}th,td{{padding:10px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{background:#edf2ee;font-size:12px;text-transform:uppercase}}code{{font-family:ui-monospace,monospace;font-size:12px}}.state{{font-size:12px;font-weight:bold}}.active{{color:var(--green)}}.deferred{{color:var(--amber)}}.not_applicable{{color:var(--red)}}
.notice{{border-left:4px solid var(--amber);padding:12px 16px;background:#fff8e9}}footer{{color:var(--muted);padding-top:12px;border-top:1px solid var(--line)}}
@media(max-width:800px){{.metrics{{grid-template-columns:1fr 1fr}}.metric{{border-bottom:1px solid var(--line)}}h1{{font-size:26px}}}}
</style></head><body>
<header><h1>Stage 2 菜谱生成台 / Proposal Kitchen</h1><p>按获批 grammar 展开组件、顺序和 linker。这里生产的是待评估菜谱，不是获批疫苗。</p></header>
<main>
<section><h2>本轮发生了什么 / What happened</h2><p class="lead">系统从 {report_seed_count} 条冻结 seed 出发，保持抗原内部序列不变，只搜索获准的组件选择、排列顺序和连接序列。每条新菜谱都有父候选、生成参数、精确序列和哈希。</p>
<div class="metrics"><div class="metric"><b>{counts['seed_candidates']}</b><span>种子 / seeds</span></div><div class="metric"><b>{counts['generated_candidates']}</b><span>新菜谱 / generated</span></div><div class="metric"><b>{counts['skipped_candidates']}</b><span>跳过 / skipped</span></div><div class="metric"><b>{minimum}-{maximum}</b><span>长度 / AA length</span></div><div class="metric"><b>{counts['total_candidate_specification_records']}</b><span>总候选 / total</span></div></div></section>
<section><h2>组合覆盖 / Combination coverage</h2><div class="table"><table><thead><tr><th>模板</th><th>Linker</th><th>候选数</th></tr></thead><tbody>{matrix_rows}</tbody></table></div></section>
<section><h2>模型和算法分工 / Model and algorithm roles</h2><p class="lead">“所有手段都上”通过分工实现，不是把所有模型塞进同一个分数。只有 <code>active</code> 表示本轮实际启用；<code>deferred</code> 表示等待对应输入或阶段。</p><div class="table"><table><thead><tr><th>Adapter</th><th>角色</th><th>阶段</th><th>状态</th><th>原因</th></tr></thead><tbody>{role_rows}</tbody></table></div></section>
<section><h2>生成候选 / Generated candidates</h2><div class="table"><table><thead><tr><th>候选</th><th>模板</th><th>组件顺序</th><th>Linker</th><th>AA</th><th>SHA-256</th></tr></thead><tbody>{candidate_rows}</tbody></table></div></section>
<section class="notice"><strong>结论边界 / Boundary</strong><br>这些候选仅完成数字配方和血缘审计。结构、免疫证据、可开发性、蛋白/mRNA 产品设计和湿实验尚未给出结论。</section>
<footer>Generation <code>{escape(result['identity'])}</code> | Grammar <code>{escape(grammar['grammar_id'])}</code> | Round <code>{escape(batch['design_round_id'])}</code></footer>
</main></body></html>"""


def _artifact_index_valid(directory: Path, index: dict[str, Any]) -> bool:
    expected = build_artifact_index(
        directory,
        str(index.get("project_id", "")),
        str(index.get("run_id", "")),
    )
    return index == expected


def verify_proposal_generation(directory: str | Path) -> dict[str, Any]:
    root = Path(directory).expanduser().resolve()
    errors: list[str] = []
    if not root.is_dir():
        return {"status": "fail", "identity": root.name, "errors": [f"Missing directory: {root}"]}
    actual_files: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            errors.append(f"Symlink is not allowed: {path}")
        elif path.is_file():
            actual_files.add(path.relative_to(root).as_posix())
    if actual_files != EXPECTED_FILES:
        errors.append(
            f"Artifact set differs: missing={sorted(EXPECTED_FILES - actual_files)} "
            f"unexpected={sorted(actual_files - EXPECTED_FILES)}"
        )
    try:
        context = _load_object(root / "inputs/generation_context.json", "generation context")
        grammar = _load_object(root / "inputs/proposal_grammar.json", "proposal grammar")
        seed_batch = _load_object(root / "inputs/seed_candidate_batch.json", "seed batch")
        stored_specification = _load_object(
            root / "candidate_specification.generated.json",
            "generated candidate specification",
        )
        stored_batch = _load_object(root / "proposal_batch.json", "proposal batch")
        index = _load_object(root / ARTIFACT_INDEX_FILENAME, "artifact index")
        if index.get("project_id") != context.get("project_id"):
            errors.append("Artifact index project_id differs from generation context")
        if index.get("run_id") != root.name:
            errors.append("Artifact index run_id differs from generation identity")
        if sha256_file(root / "inputs/proposal_grammar.json") != context.get(
            "proposal_grammar_sha256"
        ):
            errors.append("Proposal grammar snapshot hash differs from generation context")
        if sha256_file(root / "inputs/seed_candidate_batch.json") != context.get(
            "seed_candidate_batch_sha256"
        ):
            errors.append("Seed candidate snapshot hash differs from generation context")
        rebuilt = _build_generation(context, seed_batch, grammar)
        if rebuilt["identity"] != root.name:
            errors.append("Directory name differs from recomputed generation identity")
        if rebuilt["specification"] != stored_specification:
            errors.append("Generated candidate specification differs from recomputation")
        if rebuilt["proposal_batch"] != stored_batch:
            errors.append("Proposal batch differs from recomputation")
        if (root / "proposals.csv").read_text(encoding="utf-8") != _csv_text(
            rebuilt["generated"]
        ):
            errors.append("Proposal CSV differs from recomputation")
        if (root / "proposals.fasta").read_text(encoding="ascii") != _fasta_text(
            rebuilt["generated"]
        ):
            errors.append("Proposal FASTA differs from recomputation")
        if (root / "report.html").read_text(encoding="utf-8") != _render_report(
            rebuilt, grammar
        ):
            errors.append("Proposal report differs from recomputation")
        if not _artifact_index_valid(root, index):
            errors.append("Artifact index differs from files on disk")
    except (KeyError, OSError, ValueError) as error:
        errors.append(str(error))
    return {
        "schema_version": 1,
        "identity": root.name,
        "path": str(root),
        "status": "fail" if errors else "pass",
        "errors": errors,
    }


def write_proposal_generation(
    project_config: str | Path,
    *,
    grammar_path: str | Path,
    seed_run_dir: str | Path | None = None,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    config = load_project_config(Path(project_config))
    seed = _resolve_seed_run(config, seed_run_dir)
    grammar_source = Path(grammar_path).expanduser().resolve()
    grammar = _load_object(grammar_source, "proposal grammar")
    seed_round_id = _text(
        seed.candidate_batch.get("design_round_id"),
        "seed.design_round_id",
    )
    _validate_grammar(
        grammar,
        project_id=config.project_id,
        round_id=seed_round_id,
        candidate_keys={
            str(candidate.get("candidate_key"))
            for candidate in seed.candidate_batch.get("candidates", [])
            if isinstance(candidate, dict)
        },
    )
    context = {
        "schema_version": GENERATION_CONTEXT_SCHEMA,
        "project_id": config.project_id,
        "design_round_id": seed_round_id,
        "seed_run_id": str(seed.manifest["run_id"]),
        "seed_run_path": str(seed.directory),
        "seed_artifact_index_sha256": seed.artifact_index_sha256,
        "seed_candidate_batch_sha256": seed.candidate_batch_sha256,
        "proposal_grammar_sha256": sha256_file(grammar_source),
        "stage1_run_id": seed.stage1_run_id,
        "stage1_run_path": seed.stage1_run_path,
        "generator": {"id": GENERATOR_ID, "version": GENERATOR_VERSION},
    }
    result = _build_generation(context, seed.candidate_batch, grammar)
    root = (
        Path(output_root).expanduser().resolve()
        if output_root is not None
        else config.runtime_root / "input" / "stage2" / "proposals"
    )
    root.mkdir(parents=True, exist_ok=True)
    output_dir = root / result["identity"]
    if output_dir.exists():
        verification = verify_proposal_generation(output_dir)
        if verification["status"] != "pass":
            raise ValueError(
                "Existing proposal generation is invalid: "
                + "; ".join(verification["errors"][:5])
            )
    else:
        temporary = Path(tempfile.mkdtemp(prefix=f".{result['identity']}.", dir=root))
        try:
            (temporary / "inputs").mkdir()
            shutil.copyfile(grammar_source, temporary / "inputs/proposal_grammar.json")
            shutil.copyfile(
                seed.directory / "nodes" / CANDIDATE_STAGE_ID / "candidate_batch.json",
                temporary / "inputs/seed_candidate_batch.json",
            )
            (temporary / "inputs/generation_context.json").write_text(
                _json_text(context), encoding="utf-8"
            )
            (temporary / "candidate_specification.generated.json").write_text(
                _json_text(result["specification"]), encoding="utf-8"
            )
            (temporary / "proposal_batch.json").write_text(
                _json_text(result["proposal_batch"]), encoding="utf-8"
            )
            (temporary / "proposals.csv").write_text(
                _csv_text(result["generated"]), encoding="utf-8"
            )
            (temporary / "proposals.fasta").write_text(
                _fasta_text(result["generated"]), encoding="ascii"
            )
            (temporary / "report.html").write_text(
                _render_report(result, grammar), encoding="utf-8"
            )
            artifact_index = build_artifact_index(
                temporary,
                config.project_id,
                result["identity"],
            )
            (temporary / ARTIFACT_INDEX_FILENAME).write_text(
                _json_text(artifact_index), encoding="utf-8"
            )
            os.replace(temporary, output_dir)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
    verification = verify_proposal_generation(output_dir)
    if verification["status"] != "pass":
        raise ValueError(
            "Proposal generation verification failed: "
            + "; ".join(verification["errors"][:5])
        )
    return {
        "schema_version": 1,
        "project_id": config.project_id,
        "design_round_id": seed_round_id,
        "identity": result["identity"],
        "seed_run_id": context["seed_run_id"],
        "stage1_run_id": context["stage1_run_id"],
        "stage1_run_path": context["stage1_run_path"],
        "generated_candidates": len(result["generated"]),
        "skipped_candidates": len(result["skipped"]),
        "total_candidates": result["proposal_batch"]["counts"][
            "total_candidate_specification_records"
        ],
        "output_dir": str(output_dir),
        "candidate_specification": str(
            output_dir / "candidate_specification.generated.json"
        ),
        "proposal_batch": str(output_dir / "proposal_batch.json"),
        "fasta": str(output_dir / "proposals.fasta"),
        "csv": str(output_dir / "proposals.csv"),
        "report": str(output_dir / "report.html"),
        "verification_status": verification["status"],
    }
