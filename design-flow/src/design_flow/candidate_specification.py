"""Deterministic candidate specification and manual-construct reconciliation."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .config import load_project_config
from .domain import ProjectConfig, QCIssue
from .fasta import parse_fasta
from .qc import (
    CANONICAL_AMINO_ACIDS,
    normalize_amino_acid,
    normalize_nucleotide,
    translate_cds,
)
from .verification import sha256_file, verify_run


CANDIDATE_STAGE_ID = "candidate_specification"
NEXT_STAGE_ID = "protein_structure_assessment"
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
MANUAL_CANDIDATE_TYPES = frozenset({"truncation", "fusion", "manual_control"})
ANNOTATION_STATUSES = frozenset({"unreviewed", "approved"})
GRAMMAR_STATUSES = frozenset({"not_configured", "draft", "approved"})
MIN_SOURCE_SEGMENT_LENGTH = 8


@dataclass(frozen=True)
class ManualCandidateSpec:
    candidate_key: str
    display_name: str
    candidate_type: str
    amino_acid_fasta: Path
    nucleotide_fasta: Path | None
    claimed_source_id: str | None
    claimed_source_start: int | None
    claimed_source_end: int | None
    claimed_component_keys: tuple[str, ...]
    annotation_status: str
    proposal: dict[str, Any]


@dataclass(frozen=True)
class CandidateSpecification:
    schema_version: int
    specification_id: str
    batch_label: str
    release_mode: str
    design_round_id: str | None
    include_source_controls: tuple[str, ...]
    manual_candidates: tuple[ManualCandidateSpec, ...]
    generation_grammar: dict[str, Any]
    path: Path
    sha256: str


@dataclass
class CandidateRecord:
    candidate_key: str
    candidate_id: str
    display_name: str
    candidate_type: str
    amino_acid_sequence: str
    nucleotide_sequence: str | None
    translated_sequence: str | None
    translation_relation: dict[str, Any]
    inferred_components: list[dict[str, Any]]
    claimed_annotation: dict[str, Any]
    observed_component_keys: list[str]
    annotation_status: str
    release_status: str
    exploratory_structure_ready: bool
    formal_structure_ready: bool
    duplicate_of: str | None
    proposal: dict[str, Any]
    issues: list[QCIssue] = field(default_factory=list)

    @property
    def computational_status(self) -> str:
        return "fail" if any(issue.severity == "error" for issue in self.issues) else "pass"

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_key": self.candidate_key,
            "candidate_id": self.candidate_id,
            "display_name": self.display_name,
            "candidate_type": self.candidate_type,
            "computational_status": self.computational_status,
            "release_status": self.release_status,
            "annotation_status": self.annotation_status,
            "amino_acid_sequence": self.amino_acid_sequence,
            "amino_acid_sha256": _sha256_text(self.amino_acid_sequence),
            "nucleotide_sequence": self.nucleotide_sequence,
            "nucleotide_sha256": (
                _sha256_text(self.nucleotide_sequence)
                if self.nucleotide_sequence is not None
                else None
            ),
            "translated_sequence": self.translated_sequence,
            "translation_relation": self.translation_relation,
            "inferred_components": self.inferred_components,
            "claimed_annotation": self.claimed_annotation,
            "observed_component_keys": self.observed_component_keys,
            "exploratory_structure_ready": self.exploratory_structure_ready,
            "formal_structure_ready": self.formal_structure_ready,
            "duplicate_of": self.duplicate_of,
            "proposal": self.proposal,
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass
class CandidateBatchAnalysis:
    config: ProjectConfig
    source_run_dir: Path
    source_run_id: str
    source_manifest: dict[str, Any]
    source_summary: dict[str, Any]
    source_handoff: dict[str, Any]
    specification: CandidateSpecification
    source_proteins: dict[str, dict[str, Any]]
    candidates: list[CandidateRecord]
    issues: list[QCIssue]
    input_paths: dict[str, Path]
    input_digests: dict[str, str]

    @property
    def all_issues(self) -> list[QCIssue]:
        return self.issues + [issue for candidate in self.candidates for issue in candidate.issues]

    @property
    def computational_status(self) -> str:
        return "fail" if any(issue.severity == "error" for issue in self.all_issues) else "pass"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return value


def _text(mapping: dict[str, Any], key: str, field_name: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name}.{key} must be a non-empty string")
    return value.strip()


def _optional_positive_int(mapping: dict[str, Any], key: str, field_name: str) -> int | None:
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{field_name}.{key} must be a positive integer")
    return value


def _runtime_input_path(config: ProjectConfig, value: str, field_name: str) -> Path:
    path = Path(value).expanduser()
    path = (path if path.is_absolute() else config.runtime_root / path).resolve()
    if not path.is_relative_to(config.runtime_root):
        raise ValueError(f"{field_name} must resolve inside runtime_root")
    if not path.is_file():
        raise ValueError(f"{field_name} not found: {path}")
    return path


def load_candidate_specification(
    config: ProjectConfig,
    path: Path | None = None,
) -> CandidateSpecification:
    specification_path = (path or config.candidate_specification)
    if specification_path is None:
        raise ValueError(
            "No candidate specification configured; set inputs.candidate_specification "
            "or pass --specification"
        )
    specification_path = specification_path.expanduser().resolve()
    if not specification_path.is_relative_to(config.runtime_root):
        raise ValueError("candidate specification must be inside runtime_root")
    try:
        raw = json.loads(specification_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read candidate specification {specification_path}: {error}") from error
    data = _mapping(raw, "candidate specification")
    if data.get("schema_version") != 1:
        raise ValueError("candidate specification schema_version must be 1")
    specification_id = _text(data, "specification_id", "candidate specification")
    if not ID_PATTERN.fullmatch(specification_id):
        raise ValueError(f"Invalid specification_id: {specification_id}")
    release_mode = str(data.get("release_mode", "provisional")).strip()
    if release_mode not in {"provisional", "approved"}:
        raise ValueError("release_mode must be 'provisional' or 'approved'")
    design_round_value = data.get("design_round_id")
    if design_round_value is not None and (
        not isinstance(design_round_value, str)
        or not design_round_value.strip()
        or not ID_PATTERN.fullmatch(design_round_value.strip())
    ):
        raise ValueError("design_round_id must be a valid non-empty ID when provided")
    design_round_id = design_round_value.strip() if isinstance(design_round_value, str) else None

    controls = data.get("include_source_controls")
    if not isinstance(controls, list) or not controls or not all(
        isinstance(value, str) and value.strip() for value in controls
    ):
        raise ValueError("include_source_controls must be a non-empty string array")
    normalized_controls = tuple(value.strip() for value in controls)
    if len(normalized_controls) != len(set(normalized_controls)):
        raise ValueError("include_source_controls contains duplicate IDs")

    raw_candidates = data.get("manual_candidates")
    if not isinstance(raw_candidates, list):
        raise ValueError("manual_candidates must be a JSON array")
    manual_candidates: list[ManualCandidateSpec] = []
    known_keys: set[str] = set()
    source_candidate_keys = {f"source-{source_id}" for source_id in normalized_controls}
    for index, raw_candidate in enumerate(raw_candidates):
        field_name = f"manual_candidates[{index}]"
        candidate = _mapping(raw_candidate, field_name)
        candidate_key = _text(candidate, "candidate_key", field_name)
        if not ID_PATTERN.fullmatch(candidate_key):
            raise ValueError(f"Invalid candidate_key: {candidate_key}")
        if candidate_key in known_keys:
            raise ValueError(f"Duplicate candidate_key: {candidate_key}")
        if candidate_key in source_candidate_keys:
            raise ValueError(
                f"manual candidate_key collides with a source control: {candidate_key}"
            )
        known_keys.add(candidate_key)
        candidate_type = _text(candidate, "candidate_type", field_name)
        if candidate_type not in MANUAL_CANDIDATE_TYPES:
            raise ValueError(
                f"{field_name}.candidate_type must be one of {sorted(MANUAL_CANDIDATE_TYPES)}"
            )
        annotation_status = str(candidate.get("annotation_status", "unreviewed")).strip()
        if annotation_status not in ANNOTATION_STATUSES:
            raise ValueError(
                f"{field_name}.annotation_status must be one of {sorted(ANNOTATION_STATUSES)}"
            )
        aa_path = _runtime_input_path(
            config,
            _text(candidate, "amino_acid_fasta", field_name),
            f"{field_name}.amino_acid_fasta",
        )
        cds_value = candidate.get("nucleotide_fasta")
        cds_path = None
        if cds_value is not None:
            if not isinstance(cds_value, str) or not cds_value.strip():
                raise ValueError(f"{field_name}.nucleotide_fasta must be a non-empty path")
            cds_path = _runtime_input_path(config, cds_value, f"{field_name}.nucleotide_fasta")
        claimed_components = candidate.get("claimed_component_keys", [])
        if not isinstance(claimed_components, list) or not all(
            isinstance(value, str) and value.strip() for value in claimed_components
        ):
            raise ValueError(f"{field_name}.claimed_component_keys must be a string array")
        proposal = candidate.get("proposal", {})
        if not isinstance(proposal, dict):
            raise ValueError(f"{field_name}.proposal must be an object")
        generator = proposal.get("generator", {})
        if not isinstance(generator, dict):
            raise ValueError(f"{field_name}.proposal.generator must be an object")
        generator_id = str(generator.get("id", "manual_import")).strip()
        generator_version = str(generator.get("version", "1")).strip()
        if not generator_id or not generator_version:
            raise ValueError(f"{field_name}.proposal generator id/version must be non-empty")
        parameters = generator.get("parameters", {})
        if not isinstance(parameters, dict):
            raise ValueError(f"{field_name}.proposal.generator.parameters must be an object")
        parent_keys_value = proposal.get("parent_candidate_keys")
        if parent_keys_value is None:
            if candidate_type == "fusion":
                parent_keys_value = claimed_components
            elif candidate.get("claimed_source_id") is not None:
                parent_keys_value = [f"source-{str(candidate['claimed_source_id']).strip()}"]
            else:
                parent_keys_value = []
        if not isinstance(parent_keys_value, list) or not all(
            isinstance(value, str) and value.strip() for value in parent_keys_value
        ):
            raise ValueError(f"{field_name}.proposal.parent_candidate_keys must be a string array")
        feedback_ids = proposal.get("feedback_request_ids", [])
        if not isinstance(feedback_ids, list) or not all(
            isinstance(value, str) and value.strip() for value in feedback_ids
        ):
            raise ValueError(f"{field_name}.proposal.feedback_request_ids must be a string array")
        manual_candidates.append(
            ManualCandidateSpec(
                candidate_key=candidate_key,
                display_name=str(candidate.get("display_name", candidate_key)).strip()
                or candidate_key,
                candidate_type=candidate_type,
                amino_acid_fasta=aa_path,
                nucleotide_fasta=cds_path,
                claimed_source_id=(
                    str(candidate["claimed_source_id"]).strip()
                    if candidate.get("claimed_source_id") is not None
                    else None
                ),
                claimed_source_start=_optional_positive_int(
                    candidate, "claimed_source_start", field_name
                ),
                claimed_source_end=_optional_positive_int(
                    candidate, "claimed_source_end", field_name
                ),
                claimed_component_keys=tuple(value.strip() for value in claimed_components),
                annotation_status=annotation_status,
                proposal={
                    "generator": {
                        "id": generator_id,
                        "version": generator_version,
                        "parameters": parameters,
                    },
                    "parent_candidate_keys": [value.strip() for value in parent_keys_value],
                    "transformation": str(
                        proposal.get("transformation", candidate_type)
                    ).strip() or candidate_type,
                    "rationale": str(
                        proposal.get(
                            "rationale",
                            "User-supplied round-0 seed; scientific rationale not encoded.",
                        )
                    ).strip(),
                    "feedback_request_ids": [value.strip() for value in feedback_ids],
                },
            )
        )

    known_proposal_parent_keys = known_keys | source_candidate_keys
    manual_by_key = {candidate.candidate_key: candidate for candidate in manual_candidates}
    for candidate in manual_candidates:
        unknown = sorted(set(candidate.claimed_component_keys) - known_keys)
        if unknown:
            raise ValueError(
                f"{candidate.candidate_key} claims unknown component keys: {unknown}"
            )
        if candidate.candidate_key in candidate.claimed_component_keys:
            raise ValueError(f"{candidate.candidate_key} cannot contain itself")
        unknown_parents = sorted(
            set(candidate.proposal["parent_candidate_keys"])
            - known_proposal_parent_keys
        )
        if unknown_parents:
            raise ValueError(
                f"{candidate.candidate_key} has unknown proposal parents: {unknown_parents}"
            )
        if candidate.candidate_key in candidate.proposal["parent_candidate_keys"]:
            raise ValueError(f"{candidate.candidate_key} cannot be its own proposal parent")

    visited: set[str] = set()
    visiting: set[str] = set()

    def visit_proposal(candidate_key: str) -> None:
        if candidate_key in visited or candidate_key in source_candidate_keys:
            return
        if candidate_key in visiting:
            raise ValueError(f"proposal lineage contains a cycle at {candidate_key}")
        visiting.add(candidate_key)
        for parent_key in manual_by_key[candidate_key].proposal["parent_candidate_keys"]:
            visit_proposal(parent_key)
        visiting.remove(candidate_key)
        visited.add(candidate_key)

    for candidate_key in sorted(manual_by_key):
        visit_proposal(candidate_key)

    grammar = _mapping(data.get("generation_grammar", {}), "generation_grammar")
    grammar_status = str(grammar.get("status", "not_configured")).strip()
    if grammar_status not in GRAMMAR_STATUSES:
        raise ValueError(f"generation_grammar.status must be one of {sorted(GRAMMAR_STATUSES)}")
    if grammar.get("generate_new_candidates", False) is not False:
        raise ValueError(
            "Automatic candidate generation is not implemented in stage-2 v1; "
            "set generation_grammar.generate_new_candidates to false"
        )
    structure_max_length = grammar.get("structure_max_length", 1024)
    if (
        not isinstance(structure_max_length, int)
        or isinstance(structure_max_length, bool)
        or structure_max_length < 1
    ):
        raise ValueError("generation_grammar.structure_max_length must be a positive integer")
    normalized_grammar = dict(grammar)
    normalized_grammar["status"] = grammar_status
    normalized_grammar["generate_new_candidates"] = False
    normalized_grammar["structure_max_length"] = structure_max_length

    return CandidateSpecification(
        schema_version=1,
        specification_id=specification_id,
        batch_label=str(data.get("batch_label", specification_id)).strip() or specification_id,
        release_mode=release_mode,
        design_round_id=design_round_id,
        include_source_controls=normalized_controls,
        manual_candidates=tuple(manual_candidates),
        generation_grammar=normalized_grammar,
        path=specification_path,
        sha256=sha256_file(specification_path),
    )


def _single_record(path: Path, candidate_key: str) -> str:
    records = parse_fasta(path)
    if len(records) != 1:
        raise ValueError(
            f"Candidate {candidate_key} input must contain exactly one FASTA record: {path}"
        )
    return records[0].sequence


def _candidate_issue(issue: QCIssue, candidate_key: str) -> QCIssue:
    return QCIssue(issue.severity, issue.code, issue.message, candidate_key)


def _translation_relation(aa: str, translated: str | None) -> dict[str, Any]:
    if translated is None:
        return {
            "relation": "unavailable",
            "n_terminal_addition": "",
            "c_terminal_addition": "",
        }
    if translated == aa:
        return {
            "relation": "exact",
            "n_terminal_addition": "",
            "c_terminal_addition": "",
        }
    position = translated.find(aa)
    if position >= 0 and translated.find(aa, position + 1) < 0:
        prefix = translated[:position]
        suffix = translated[position + len(aa) :]
        if prefix and suffix:
            relation = "both_terminal_additions"
        elif prefix:
            relation = "n_terminal_addition"
        elif suffix:
            relation = "c_terminal_addition"
        else:
            relation = "exact"
        return {
            "relation": relation,
            "n_terminal_addition": prefix,
            "c_terminal_addition": suffix,
        }
    return {
        "relation": "mismatch",
        "n_terminal_addition": "",
        "c_terminal_addition": "",
    }


def _longest_match_at(
    sequence: str,
    offset: int,
    libraries: dict[str, str],
) -> tuple[str, int, int] | None:
    best: tuple[int, str, int] | None = None
    for library_id, library_sequence in sorted(libraries.items()):
        for library_offset in range(len(library_sequence)):
            length = 0
            while (
                offset + length < len(sequence)
                and library_offset + length < len(library_sequence)
                and sequence[offset + length] == library_sequence[library_offset + length]
            ):
                length += 1
            candidate = (length, library_id, library_offset)
            if best is None or candidate > best:
                best = candidate
    if best is None or best[0] < MIN_SOURCE_SEGMENT_LENGTH:
        return None
    return best[1], best[2], best[0]


def _infer_source_components(
    sequence: str,
    source_sequences: dict[str, str],
) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    offset = 0
    while offset < len(sequence):
        match = _longest_match_at(sequence, offset, source_sequences)
        if match is not None:
            source_id, source_offset, length = match
            component_sequence = sequence[offset : offset + length]
            components.append(
                {
                    "component_type": "source_segment",
                    "source_protein_id": source_id,
                    "source_start": source_offset + 1,
                    "source_end": source_offset + length,
                    "candidate_start": offset + 1,
                    "candidate_end": offset + length,
                    "sequence": component_sequence,
                    "sequence_sha256": _sha256_text(component_sequence),
                }
            )
            offset += length
            continue
        addition_start = offset
        offset += 1
        while offset < len(sequence) and _longest_match_at(
            sequence, offset, source_sequences
        ) is None:
            offset += 1
        component_sequence = sequence[addition_start:offset]
        components.append(
            {
                "component_type": "addition",
                "candidate_start": addition_start + 1,
                "candidate_end": offset,
                "sequence": component_sequence,
                "sequence_sha256": _sha256_text(component_sequence),
            }
        )
    return components


def _infer_component_keys(sequence: str, library: dict[str, str]) -> list[str]:
    keys: list[str] = []
    offset = 0
    ordered_library = sorted(library.items(), key=lambda item: (-len(item[1]), item[0]))
    while offset < len(sequence):
        matches = [
            (len(component_sequence), component_key)
            for component_key, component_sequence in ordered_library
            if sequence.startswith(component_sequence, offset)
        ]
        if not matches:
            return []
        length, component_key = max(matches)
        keys.append(component_key)
        offset += length
    return keys


def _candidate_id(
    candidate_type: str,
    aa: str,
    cds: str | None,
    components: list[dict[str, Any]],
) -> str:
    identity = {
        "candidate_type": candidate_type,
        "amino_acid_sequence": aa,
        "nucleotide_sequence": cds,
        "components": [
            {
                key: component.get(key)
                for key in (
                    "component_type",
                    "source_protein_id",
                    "source_start",
                    "source_end",
                    "candidate_start",
                    "candidate_end",
                    "sequence_sha256",
                )
            }
            for component in components
        ],
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"candidate-{digest[:16]}"


def _load_source_run(
    config: ProjectConfig,
    source_run_dir: Path | None,
) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    if source_run_dir is None:
        latest_path = config.run_root / "latest.json"
        try:
            latest = json.loads(latest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"Cannot read latest source run from {latest_path}: {error}") from error
        source_run_value = latest.get("run_path")
        if not isinstance(source_run_value, str) or not source_run_value:
            raise ValueError(f"latest.json has no run_path: {latest_path}")
        source_run_dir = Path(source_run_value)
    source_run_dir = source_run_dir.expanduser().resolve()
    try:
        initial_manifest = json.loads(
            (source_run_dir / "manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read source run manifest from {source_run_dir}: {error}") from error
    if initial_manifest.get("current_stage") != "program_and_source_intake":
        lineage = initial_manifest.get("lineage")
        parent_path = lineage.get("parent_run_path") if isinstance(lineage, dict) else None
        if initial_manifest.get("current_stage") == CANDIDATE_STAGE_ID and isinstance(
            parent_path, str
        ):
            return _load_source_run(config, Path(parent_path))
        raise ValueError("Stage 2 currently requires a stage-1 source run")
    verification = verify_run(source_run_dir)
    if verification["status"] != "pass":
        raise ValueError(
            "Source run verification failed: " + "; ".join(verification["errors"][:5])
        )
    manifest = json.loads((source_run_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("project_id") != config.project_id:
        raise ValueError("Source run project_id differs from the active project")
    node_dir = source_run_dir / "nodes" / "program_and_source_intake"
    summary = json.loads((node_dir / "summary.json").read_text(encoding="utf-8"))
    handoff = json.loads((node_dir / "handoff.json").read_text(encoding="utf-8"))
    proteins_document = json.loads((node_dir / "proteins.json").read_text(encoding="utf-8"))
    protein_records = proteins_document.get("proteins")
    if not isinstance(protein_records, list):
        raise ValueError("Source run proteins.json is malformed")
    source_proteins = {
        record["protein_id"]: record
        for record in protein_records
        if isinstance(record, dict)
        and isinstance(record.get("protein_id"), str)
        and record.get("status") == "pass"
    }
    return source_run_dir, manifest, summary, handoff, source_proteins


def analyze_candidate_specification(
    config_path: Path,
    *,
    source_run_dir: Path | None = None,
    specification_path: Path | None = None,
) -> CandidateBatchAnalysis:
    config = load_project_config(config_path)
    specification = load_candidate_specification(config, specification_path)
    (
        source_run_dir,
        source_manifest,
        source_summary,
        source_handoff,
        source_proteins,
    ) = _load_source_run(config, source_run_dir)

    missing_controls = sorted(set(specification.include_source_controls) - set(source_proteins))
    if missing_controls:
        raise ValueError(f"Candidate specification references unavailable source controls: {missing_controls}")

    source_sequences = {
        protein_id: str(record["amino_acid_sequence"])
        for protein_id, record in source_proteins.items()
    }
    issues: list[QCIssue] = []
    candidates: list[CandidateRecord] = []
    input_paths: dict[str, Path] = {"candidate_specification": specification.path}
    input_digests: dict[str, str] = {"candidate_specification": specification.sha256}

    source_blockers = set(source_handoff.get("blocking_action_ids", []))
    if "approve-design-round-contract" in source_blockers:
        raise ValueError(
            "Stage 2 is blocked until the Stage 1 design-round contract is approved"
        )
    design_round = source_handoff.get("carried_forward", {}).get("design_round", {})
    round_id = design_round.get("round_id")
    if not isinstance(round_id, str) or not round_id:
        raise ValueError("Stage 1 handoff has no versioned design round")
    if (
        specification.design_round_id is not None
        and specification.design_round_id != round_id
    ):
        raise ValueError(
            "Candidate specification design_round_id differs from the Stage 1 design round"
        )
    source_controls_approved = not {
        "confirm-source-provenance",
        "confirm-reference-controls",
    } & source_blockers
    for source_id in specification.include_source_controls:
        source = source_proteins[source_id]
        aa = str(source["amino_acid_sequence"])
        cds = str(source["nucleotide_sequence"])
        components = [
            {
                "component_type": "source_segment",
                "source_protein_id": source_id,
                "source_candidate_id": source["candidate_id"],
                "source_start": 1,
                "source_end": len(aa),
                "candidate_start": 1,
                "candidate_end": len(aa),
                "sequence": aa,
                "sequence_sha256": _sha256_text(aa),
            }
        ]
        candidates.append(
            CandidateRecord(
                candidate_key=f"source-{source_id}",
                candidate_id=_candidate_id("source_control", aa, cds, components),
                display_name=f"{source_id} full-length source control",
                candidate_type="source_control",
                amino_acid_sequence=aa,
                nucleotide_sequence=cds,
                translated_sequence=str(source["translated_sequence"]),
                translation_relation={
                    "relation": "exact",
                    "n_terminal_addition": "",
                    "c_terminal_addition": "",
                },
                inferred_components=components,
                claimed_annotation={"source_protein_id": source_id, "source_start": 1, "source_end": len(aa)},
                observed_component_keys=[],
                annotation_status="approved" if source_controls_approved else "unreviewed",
                release_status="released" if source_controls_approved else "provisional",
                exploratory_structure_ready=True,
                formal_structure_ready=source_controls_approved,
                duplicate_of=None,
                proposal={
                    "round_id": round_id,
                    "generator": {"id": "source_intake", "version": "1", "parameters": {}},
                    "parent_candidate_keys": [],
                    "parent_candidate_ids": [],
                    "transformation": "immutable_source_control",
                    "rationale": "Preserve the exact Stage 1 source as a round control.",
                    "feedback_request_ids": [],
                },
            )
        )

    raw_manual: dict[str, dict[str, Any]] = {}
    for manual in specification.manual_candidates:
        aa_raw = _single_record(manual.amino_acid_fasta, manual.candidate_key)
        aa, aa_issues = normalize_amino_acid(aa_raw, manual.candidate_key)
        cds: str | None = None
        translated: str | None = None
        candidate_issues = [_candidate_issue(issue, manual.candidate_key) for issue in aa_issues]
        if manual.nucleotide_fasta is not None:
            cds_raw = _single_record(manual.nucleotide_fasta, manual.candidate_key)
            cds, cds_issues = normalize_nucleotide(cds_raw, manual.candidate_key)
            translated, translation_issues, _ = translate_cds(cds, manual.candidate_key)
            candidate_issues.extend(
                _candidate_issue(issue, manual.candidate_key)
                for issue in [*cds_issues, *translation_issues]
            )
        relation = _translation_relation(aa, translated)
        if relation["relation"] == "mismatch":
            candidate_issues.append(
                QCIssue(
                    "error",
                    "manual_translation_mismatch",
                    "CDS translation does not contain the supplied amino-acid construct exactly",
                    manual.candidate_key,
                )
            )
        elif relation["relation"] not in {"exact", "unavailable"}:
            candidate_issues.append(
                QCIssue(
                    "warning",
                    "translation_additions_inferred",
                    (
                        "CDS adds residues around the supplied AA construct: "
                        f"N={relation['n_terminal_addition'] or 'none'}, "
                        f"C={relation['c_terminal_addition'] or 'none'}"
                    ),
                    manual.candidate_key,
                )
            )
        components = _infer_source_components(aa, source_sequences)
        raw_manual[manual.candidate_key] = {
            "spec": manual,
            "aa": aa,
            "cds": cds,
            "translated": translated,
            "relation": relation,
            "components": components,
            "issues": candidate_issues,
        }
        aa_name = f"manual:{manual.candidate_key}:amino_acid_fasta"
        input_paths[aa_name] = manual.amino_acid_fasta
        input_digests[aa_name] = sha256_file(manual.amino_acid_fasta)
        if manual.nucleotide_fasta is not None:
            cds_name = f"manual:{manual.candidate_key}:nucleotide_fasta"
            input_paths[cds_name] = manual.nucleotide_fasta
            input_digests[cds_name] = sha256_file(manual.nucleotide_fasta)

    component_library = {
        key: value["aa"]
        for key, value in raw_manual.items()
        if value["spec"].candidate_type != "fusion"
    }
    seen_sequences: dict[str, str] = {
        _sha256_text(candidate.amino_acid_sequence): candidate.candidate_key
        for candidate in candidates
    }
    structure_max_length = int(specification.generation_grammar["structure_max_length"])
    for manual in specification.manual_candidates:
        raw = raw_manual[manual.candidate_key]
        aa = raw["aa"]
        components = raw["components"]
        candidate_issues = raw["issues"]
        observed_component_keys = (
            _infer_component_keys(aa, component_library)
            if manual.candidate_type == "fusion"
            else []
        )
        annotation_conflict = False
        source_segments = [
            component
            for component in components
            if component["component_type"] == "source_segment"
        ]
        additions = [
            component for component in components if component["component_type"] == "addition"
        ]
        if additions:
            annotation_conflict = True
            candidate_issues.append(
                QCIssue(
                    "warning",
                    "unmapped_manual_sequence",
                    "Part of the supplied AA sequence could not be mapped to a source protein",
                    manual.candidate_key,
                )
            )
        if manual.claimed_source_id is not None:
            observed_range = None
            if len(source_segments) == 1 and not additions:
                segment = source_segments[0]
                observed_range = (
                    segment["source_protein_id"],
                    segment["source_start"],
                    segment["source_end"],
                )
            claimed_range = (
                manual.claimed_source_id,
                manual.claimed_source_start,
                manual.claimed_source_end,
            )
            if observed_range != claimed_range:
                annotation_conflict = True
                candidate_issues.append(
                    QCIssue(
                        "warning",
                        "claimed_source_range_mismatch",
                        f"Claimed source range {claimed_range} differs from sequence-derived range {observed_range}",
                        manual.candidate_key,
                    )
                )
        if manual.claimed_component_keys:
            claimed_order = list(manual.claimed_component_keys)
            if observed_component_keys != claimed_order:
                annotation_conflict = True
                candidate_issues.append(
                    QCIssue(
                        "warning",
                        "claimed_component_order_mismatch",
                        f"Claimed order {claimed_order} differs from sequence-derived order {observed_component_keys}",
                        manual.candidate_key,
                    )
                )
        relation_requires_review = raw["relation"]["relation"] not in {"exact", "unavailable"}
        if manual.annotation_status != "approved":
            candidate_issues.append(
                QCIssue(
                    "warning",
                    "manual_annotation_unreviewed",
                    "Sequence-derived construct annotations have not been approved by a human reviewer",
                    manual.candidate_key,
                )
            )
        fatal = any(issue.severity == "error" for issue in candidate_issues)
        release_status = (
            "rejected"
            if fatal
            else "quarantined"
            if manual.annotation_status != "approved" or annotation_conflict or relation_requires_review
            else "released"
        )
        sequence_digest = _sha256_text(aa)
        duplicate_of = seen_sequences.get(sequence_digest)
        if duplicate_of is None:
            seen_sequences[sequence_digest] = manual.candidate_key
        else:
            candidate_issues.append(
                QCIssue(
                    "warning",
                    "duplicate_amino_acid_sequence",
                    f"AA sequence duplicates candidate {duplicate_of}; duplicate model execution will be skipped",
                    manual.candidate_key,
                )
            )
        exploratory_ready = (
            not fatal
            and bool(aa)
            and set(aa) <= CANONICAL_AMINO_ACIDS
            and len(aa) <= structure_max_length
        )
        candidates.append(
            CandidateRecord(
                candidate_key=manual.candidate_key,
                candidate_id=_candidate_id(
                    manual.candidate_type,
                    aa,
                    raw["cds"],
                    components,
                ),
                display_name=manual.display_name,
                candidate_type=manual.candidate_type,
                amino_acid_sequence=aa,
                nucleotide_sequence=raw["cds"],
                translated_sequence=raw["translated"],
                translation_relation=raw["relation"],
                inferred_components=components,
                claimed_annotation={
                    "source_protein_id": manual.claimed_source_id,
                    "source_start": manual.claimed_source_start,
                    "source_end": manual.claimed_source_end,
                    "component_keys": list(manual.claimed_component_keys),
                },
                observed_component_keys=observed_component_keys,
                annotation_status=manual.annotation_status,
                release_status=release_status,
                exploratory_structure_ready=exploratory_ready,
                formal_structure_ready=exploratory_ready and release_status == "released",
                duplicate_of=duplicate_of,
                proposal={
                    "round_id": round_id,
                    **manual.proposal,
                    "parent_candidate_ids": [],
                },
                issues=candidate_issues,
            )
        )

    candidate_by_key = {candidate.candidate_key: candidate for candidate in candidates}
    for candidate in candidates:
        parent_keys = candidate.proposal["parent_candidate_keys"]
        unknown_parent_keys = [key for key in parent_keys if key not in candidate_by_key]
        if unknown_parent_keys:
            candidate.issues.append(
                QCIssue(
                    "error",
                    "unknown_proposal_parent",
                    f"Proposal lineage references unknown candidate keys: {unknown_parent_keys}",
                    candidate.candidate_key,
                )
            )
            candidate.release_status = "rejected"
            candidate.exploratory_structure_ready = False
            candidate.formal_structure_ready = False
        candidate.proposal["parent_candidate_ids"] = [
            candidate_by_key[key].candidate_id
            for key in parent_keys
            if key in candidate_by_key
        ]

    return CandidateBatchAnalysis(
        config=config,
        source_run_dir=source_run_dir,
        source_run_id=str(source_manifest["run_id"]),
        source_manifest=source_manifest,
        source_summary=source_summary,
        source_handoff=source_handoff,
        specification=specification,
        source_proteins=source_proteins,
        candidates=candidates,
        issues=issues,
        input_paths=input_paths,
        input_digests=input_digests,
    )
