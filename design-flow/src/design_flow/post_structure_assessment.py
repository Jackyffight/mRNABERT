"""Deterministic Stage 4 immune-evidence and Stage 5 developability analysis."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any

from .assessment_specs import (
    ADAPTER_IDS,
    DEVELOPABILITY_ADAPTER_IDS,
    DEVELOPABILITY_STAGE_ID,
    IMMUNE_STAGE_ID,
    _resolve_structure_run,
    load_structure_candidate_scope,
    load_developability_specification,
    load_immune_specification,
    load_residue_evidence,
    resolve_spec_path,
)
from .config import ProjectConfig, load_project_config
from .continuation_state import UNSPECIFIED_VALUES
from .requirement_gates import make_requirement
from .structure_job import _load_json
from .structure_metrics import ParsedStructure, parse_ca_pdb
from .verification import sha256_file


IMMUNE_RULESET_ID = "immune-evidence-exploratory-rules-v1"
DEVELOPABILITY_RULESET_ID = "developability-intrinsic-rules-v1"
CANONICAL_AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")
KYTE_DOOLITTLE = {
    "I": 4.5, "V": 4.2, "L": 3.8, "F": 2.8, "C": 2.5,
    "M": 1.9, "A": 1.8, "G": -0.4, "T": -0.7, "S": -0.8,
    "W": -0.9, "Y": -1.3, "P": -1.6, "H": -3.2, "E": -3.5,
    "Q": -3.5, "D": -3.5, "N": -3.5, "K": -3.9, "R": -4.5,
}
EVIDENCE_STATUSES = ("supported", "risk", "context", "not_supported")


@dataclass
class PostStructureAnalysis:
    config: ProjectConfig
    source_run_dir: Path
    source_manifest: dict[str, Any]
    candidate_batch: dict[str, Any]
    structure_assessments: dict[str, Any]
    immune_specification: dict[str, Any]
    immune_specification_path: Path
    developability_specification: dict[str, Any]
    developability_specification_path: Path
    input_paths: dict[str, Path]
    immune_result: dict[str, Any]
    developability_result: dict[str, Any]


def _distance(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right, strict=True)))


def _surface_proxy(
    structure: ParsedStructure,
    *,
    radius: float,
    maximum_neighbors: int,
) -> list[dict[str, Any]]:
    residues = structure.residues
    evidence = []
    for index, residue in enumerate(residues):
        neighbors = sum(
            abs(index - other_index) > 2
            and _distance(residue.ca, other.ca) <= radius
            for other_index, other in enumerate(residues)
        )
        evidence.append(
            {
                "position": index + 1,
                "amino_acid": residue.amino_acid,
                "plddt": round(residue.plddt, 4),
                "ca_nonlocal_neighbor_count": neighbors,
                "surface_proxy_exposed": neighbors <= maximum_neighbors,
            }
        )
    return evidence


def _parse_gapped_fasta(path: Path) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    record_id: str | None = None
    chunks: list[str] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith((";", "#")):
            continue
        if line.startswith(">"):
            if record_id is not None:
                records.append((record_id, "".join(chunks)))
            record_id = line[1:].split(maxsplit=1)[0]
            if not record_id:
                raise ValueError(f"Empty alignment record ID at {path}:{line_number}")
            chunks = []
            continue
        if record_id is None:
            raise ValueError(f"Alignment sequence appears before header at {path}:{line_number}")
        sequence = line.upper().replace(".", "-")
        invalid = set(sequence) - CANONICAL_AMINO_ACIDS - {"-", "X"}
        if invalid:
            raise ValueError(f"Invalid alignment symbols at {path}:{line_number}: {sorted(invalid)}")
        chunks.append(sequence)
    if record_id is not None:
        records.append((record_id, "".join(chunks)))
    if not records:
        raise ValueError(f"Alignment has no records: {path}")
    if len({record_id for record_id, _ in records}) != len(records):
        raise ValueError(f"Alignment has duplicate record IDs: {path}")
    lengths = {len(sequence) for _, sequence in records}
    if len(lengths) != 1 or 0 in lengths:
        raise ValueError(f"Alignment records must have one non-zero aligned length: {path}")
    return records


def _alignment_profile(
    path: Path,
    *,
    reference_record_id: str,
    source_sequence: str,
    minimum_sequences: int,
) -> dict[str, Any]:
    records = _parse_gapped_fasta(path)
    if len(records) < minimum_sequences:
        raise ValueError(
            f"Alignment {path} has {len(records)} records; minimum is {minimum_sequences}"
        )
    record_map = dict(records)
    if reference_record_id not in record_map:
        raise ValueError(f"Alignment reference not found: {reference_record_id}")
    reference = record_map[reference_record_id]
    if reference.replace("-", "") != source_sequence:
        raise ValueError(f"Alignment reference does not equal source sequence: {path}")
    profile = []
    source_position = 0
    for column_index, reference_residue in enumerate(reference):
        if reference_residue == "-":
            continue
        source_position += 1
        observed = [
            sequence[column_index]
            for _, sequence in records
            if sequence[column_index] not in {"-", "X"}
        ]
        coverage = len(observed) / len(records)
        conservation = (
            sum(residue == reference_residue for residue in observed) / len(observed)
            if observed
            else None
        )
        entropy = None
        if observed:
            counts = {residue: observed.count(residue) for residue in set(observed)}
            entropy = -sum(
                (count / len(observed)) * math.log2(count / len(observed))
                for count in counts.values()
            )
        profile.append(
            {
                "source_position": source_position,
                "reference_amino_acid": reference_residue,
                "panel_coverage": round(coverage, 6),
                "conservation_fraction": (
                    round(conservation, 6) if conservation is not None else None
                ),
                "shannon_entropy_bits": round(entropy, 6) if entropy is not None else None,
            }
        )
    if source_position != len(source_sequence):
        raise ValueError(f"Alignment source coordinate count mismatch: {path}")
    return {
        "alignment_path": str(path),
        "alignment_sha256": sha256_file(path),
        "reference_record_id": reference_record_id,
        "sequence_count": len(records),
        "aligned_length": len(reference),
        "profile": profile,
    }


def _source_controls(candidate_batch: dict[str, Any]) -> dict[str, dict[str, Any]]:
    controls = {}
    for candidate in candidate_batch["candidates"]:
        if candidate["candidate_type"] != "source_control":
            continue
        component = candidate["inferred_components"][0]
        controls[component["source_protein_id"]] = candidate
    return controls


def _adapter_state(
    config: ProjectConfig,
    adapter_id: str,
    declaration: Any,
    *,
    candidate_by_id: dict[str, dict[str, Any]],
    candidate_batch_sha256: str,
    candidate_set_sha256: str,
    require_candidate_set_identity: bool,
    input_paths: dict[str, Path],
) -> dict[str, Any]:
    if not isinstance(declaration, dict):
        raise ValueError(f"Adapter declaration must be an object: {adapter_id}")
    status = declaration.get("status")
    result_path = resolve_spec_path(
        config,
        declaration.get("result_path"),
        f"adapters.{adapter_id}.result_path",
    )
    if status == "not_configured":
        if result_path is not None:
            raise ValueError(f"Adapter {adapter_id} is not_configured but has a result path")
        return {"adapter_id": adapter_id, "status": "not_evaluated", "reason": "not_configured"}
    if status != "provided" or result_path is None or not result_path.is_file():
        raise ValueError(f"Adapter {adapter_id} status/path is inconsistent")
    document = load_residue_evidence(
        result_path,
        adapter_id=adapter_id,
        candidate_by_id=candidate_by_id,
        candidate_batch_sha256=candidate_batch_sha256,
        candidate_set_sha256=candidate_set_sha256,
        require_candidate_set_identity=require_candidate_set_identity,
    )
    input_paths[f"adapter:{adapter_id}"] = result_path
    summaries = {
        candidate_id: {
            "observation_count": 0,
            **{f"{status}_count": 0 for status in EVIDENCE_STATUSES},
        }
        for candidate_id in candidate_by_id
    }
    for observation in document["observations"]:
        summary = summaries[observation["candidate_id"]]
        summary["observation_count"] += 1
        summary[f"{observation['status']}_count"] += 1
    return {
        "adapter_id": adapter_id,
        "status": "evaluated",
        "tool": document["tool"],
        "result_sha256": sha256_file(result_path),
        "observation_count": len(document["observations"]),
        "candidate_observation_summaries": summaries,
    }


def _candidate_observation_summary(
    state: dict[str, Any], candidate_id: str
) -> dict[str, int]:
    summary = state.get("candidate_observation_summaries", {}).get(candidate_id)
    if summary is not None:
        return summary
    return {
        "observation_count": 0,
        **{f"{status}_count": 0 for status in EVIDENCE_STATUSES},
    }


def _immune_analysis(
    config: ProjectConfig,
    spec: dict[str, Any],
    candidate_batch: dict[str, Any],
    structure_by_id: dict[str, dict[str, Any]],
    structures: dict[str, ParsedStructure],
    input_paths: dict[str, Path],
    candidate_batch_sha256: str,
    candidate_set_sha256: str,
    require_candidate_set_identity: bool,
) -> dict[str, Any]:
    candidates = candidate_batch["candidates"]
    candidate_by_id = {candidate["candidate_id"]: candidate for candidate in candidates}
    controls = _source_controls(candidate_batch)
    policy = spec.get("policy", {})
    radius = float(policy.get("surface_proxy_ca_radius_angstrom", 10.0))
    max_neighbors = int(policy.get("surface_proxy_max_nonlocal_neighbors", 8))
    minimum_sequences = int(policy.get("minimum_alignment_sequences", 3))
    minimum_coverage = float(policy.get("minimum_residue_panel_coverage", 0.8))
    if radius <= 0 or max_neighbors < 0 or minimum_sequences < 2:
        raise ValueError("Immune evidence policy contains invalid numeric bounds")
    source_alignment_declarations = spec.get("pathogen_panel", {}).get(
        "source_alignments", {}
    )
    if set(source_alignment_declarations) != set(controls):
        raise ValueError("Immune specification source alignments differ from source controls")
    alignment_profiles = {}
    requirements = []
    for source_id, declaration in source_alignment_declarations.items():
        if not isinstance(declaration, dict):
            raise ValueError(f"Alignment declaration must be an object: {source_id}")
        alignment_path = resolve_spec_path(
            config,
            declaration.get("alignment_path"),
            f"pathogen_panel.source_alignments.{source_id}.alignment_path",
        )
        reference_id = declaration.get("reference_record_id")
        if alignment_path is None or reference_id is None:
            requirements.append(
                make_requirement(
                    f"provide-{source_id.lower()}-pathogen-alignment",
                    f"Provide a versioned multiple-sequence alignment for {source_id} "
                    "and identify the record matching the immutable source control.",
                    f"提供 {source_id} 的版本化多序列比对，并标明与不可变源对照匹配的记录。",
                    requirement_class="required_before_ranking",
                    required_before_stage="integrated_ranking",
                    resolution_strategy="automated_enrichment",
                )
            )
            continue
        if not isinstance(reference_id, str) or not alignment_path.is_file():
            raise ValueError(f"Alignment path/reference is invalid for {source_id}")
        profile = _alignment_profile(
            alignment_path,
            reference_record_id=reference_id,
            source_sequence=controls[source_id]["amino_acid_sequence"],
            minimum_sequences=minimum_sequences,
        )
        alignment_profiles[source_id] = profile
        input_paths[f"alignment:{source_id}"] = alignment_path
    host = spec.get("host", {})
    if (
        host.get("population_status") != "approved"
        or not isinstance(host.get("population_description"), str)
        or not host["population_description"].strip()
    ):
        requirements.append(
            make_requirement(
                "approve-host-population-and-mhc-panel",
                f"Declare the target population for {config.intended_host_species} "
                "and approve a versioned host MHC allele panel.",
                f"声明 {config.intended_host_species} 的目标群体并批准版本化宿主 MHC 等位基因 panel。",
                requirement_class="required_before_release",
                required_before_stage="experiment_release",
                resolution_strategy="human_policy_approval",
            )
        )
    mhc_panel_path = resolve_spec_path(
        config, host.get("mhc_panel_path"), "host.mhc_panel_path"
    )
    if mhc_panel_path is None:
        requirements.append(
            make_requirement(
                "provide-versioned-host-mhc-panel",
                "Provide the versioned host MHC allele panel used by presentation analysis.",
                "提供抗原呈递分析使用的版本化宿主 MHC 等位基因 panel。",
                requirement_class="required_before_ranking",
                required_before_stage="integrated_ranking",
                resolution_strategy="automated_enrichment_and_human_approval",
            )
        )
    else:
        if not mhc_panel_path.is_file():
            raise ValueError(f"Host MHC panel file not found: {mhc_panel_path}")
        input_paths["mhc_panel"] = mhc_panel_path
    if spec.get("pathogen_panel", {}).get("status") != "approved":
        requirements.append(
            make_requirement(
                "approve-pathogen-sequence-panel",
                "Approve the isolate/strain panel represented by the source alignments.",
                "批准源序列比对所代表的分离株或毒株 panel。",
                requirement_class="required_before_release",
                required_before_stage="experiment_release",
                resolution_strategy="human_policy_approval",
            )
        )
    adapter_states = {}
    for adapter_id in ADAPTER_IDS:
        declaration = spec.get("adapters", {}).get(adapter_id)
        state = _adapter_state(
            config,
            adapter_id,
            declaration,
            candidate_by_id=candidate_by_id,
            candidate_batch_sha256=candidate_batch_sha256,
            candidate_set_sha256=candidate_set_sha256,
            require_candidate_set_identity=require_candidate_set_identity,
            input_paths=input_paths,
        )
        adapter_states[adapter_id] = state
        if state["status"] != "evaluated":
            requirement_class = (
                "required_before_release"
                if adapter_id == "epitope_support"
                else "required_before_ranking"
            )
            required_before_stage = (
                "experiment_release"
                if requirement_class == "required_before_release"
                else "integrated_ranking"
            )
            requirements.append(
                make_requirement(
                    f"provide-{adapter_id.replace('_', '-')}-evidence",
                    f"Provide checksum-bound {adapter_id} adapter evidence.",
                    f"提供与候选校验和绑定的 {adapter_id} 适配器证据。",
                    requirement_class=requirement_class,
                    required_before_stage=required_before_stage,
                    resolution_strategy="computational_or_experimental_evidence",
                )
            )
    if spec.get("policy", {}).get("status") != "approved":
        requirements.append(
            make_requirement(
                "approve-immune-evidence-policy",
                "Approve population assumptions, coverage thresholds, and evidence use policy.",
                "批准群体假设、覆盖阈值和证据使用策略。",
                requirement_class="required_before_release",
                required_before_stage="experiment_release",
                resolution_strategy="human_policy_approval",
            )
        )

    candidate_results = []
    for candidate in candidates:
        candidate_id = candidate["candidate_id"]
        surface = _surface_proxy(
            structures[candidate_id],
            radius=radius,
            maximum_neighbors=max_neighbors,
        )
        conservation_by_position: dict[int, dict[str, Any]] = {}
        for component in candidate["inferred_components"]:
            if component["component_type"] != "source_segment":
                continue
            profile_document = alignment_profiles.get(component["source_protein_id"])
            if profile_document is None:
                continue
            source_profile = {
                row["source_position"]: row for row in profile_document["profile"]
            }
            for offset, candidate_position in enumerate(
                range(component["candidate_start"], component["candidate_end"] + 1)
            ):
                source_position = component["source_start"] + offset
                conservation_by_position[candidate_position] = source_profile[source_position]
        residue_evidence = []
        for row in surface:
            conservation = conservation_by_position.get(row["position"])
            residue_evidence.append(
                {
                    **row,
                    "conservation_fraction": (
                        conservation["conservation_fraction"] if conservation else None
                    ),
                    "panel_coverage": conservation["panel_coverage"] if conservation else None,
                    "shannon_entropy_bits": (
                        conservation["shannon_entropy_bits"] if conservation else None
                    ),
                }
            )
        usable_conservation = [
            row["conservation_fraction"]
            for row in residue_evidence
            if row["conservation_fraction"] is not None
            and row["panel_coverage"] >= minimum_coverage
        ]
        categories = {
            "surface_accessibility_proxy": {
                "status": "evaluated",
                "method": "ca_nonlocal_neighbor_count",
                "exposed_fraction": round(
                    sum(row["surface_proxy_exposed"] for row in residue_evidence)
                    / len(residue_evidence),
                    6,
                ),
                "limitation": "C-alpha neighbor proxy; not solvent-accessible surface area.",
            },
            "pathogen_conservation": {
                "status": "evaluated" if usable_conservation else "not_evaluated",
                "evaluated_residue_fraction": round(
                    len(usable_conservation) / len(residue_evidence), 6
                ),
                "mean_conservation_fraction": (
                    round(sum(usable_conservation) / len(usable_conservation), 6)
                    if usable_conservation
                    else None
                ),
                "minimum_conservation_fraction": (
                    round(min(usable_conservation), 6) if usable_conservation else None
                ),
            },
        }
        for adapter_id, state in adapter_states.items():
            summary = _candidate_observation_summary(state, candidate_id)
            categories[adapter_id] = {
                "status": state["status"],
                **summary,
            }
        evaluated_categories = sum(
            category["status"] == "evaluated" for category in categories.values()
        )
        candidate_results.append(
            {
                "candidate_id": candidate_id,
                "candidate_key": candidate["candidate_key"],
                "sequence_sha256": candidate["amino_acid_sha256"],
                "length": len(candidate["amino_acid_sequence"]),
                "status": "partial" if evaluated_categories < len(categories) else "evaluated",
                "structure_confidence_band": structure_by_id[candidate_id][
                    "confidence_band"
                ],
                "categories": categories,
                "residue_evidence": residue_evidence,
            }
        )
    return {
        "schema_version": 1,
        "stage_id": IMMUNE_STAGE_ID,
        "mode": "exploratory",
        "ruleset_id": IMMUNE_RULESET_ID,
        "specification_id": spec["specification_id"],
        "candidate_set_sha256": candidate_set_sha256,
        "status": "needs_data" if requirements else "evaluated",
        "release_gate_enabled": False,
        "requirements": requirements,
        "alignment_profiles": alignment_profiles,
        "adapter_states": adapter_states,
        "candidates": candidate_results,
        "limitations": [
            "Surface exposure is a C-alpha neighbor proxy, not solvent-accessible surface area.",
            "Computational immune evidence is not experimental immunogenicity or efficacy.",
            "Missing evidence remains not_evaluated and is never imputed as favorable.",
        ],
    }


def _window_entropy(sequence: str) -> float:
    counts = {residue: sequence.count(residue) for residue in set(sequence)}
    return -sum(
        (count / len(sequence)) * math.log2(count / len(sequence))
        for count in counts.values()
    )


def _merge_windows(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not windows:
        return []
    merged = [dict(windows[0])]
    for window in windows[1:]:
        current = merged[-1]
        if window["start"] <= current["end"] + 1:
            current["end"] = max(current["end"], window["end"])
            current["maximum_score"] = max(
                current["maximum_score"], window["maximum_score"]
            )
        else:
            merged.append(dict(window))
    for item in merged:
        item["length"] = item["end"] - item["start"] + 1
        item["maximum_score"] = round(item["maximum_score"], 6)
    return merged


def _hydrophobic_regions(sequence: str, length: int, threshold: float) -> list[dict[str, Any]]:
    windows = []
    for offset in range(0, len(sequence) - length + 1):
        window = sequence[offset : offset + length]
        score = sum(KYTE_DOOLITTLE[residue] for residue in window) / length
        if score >= threshold:
            windows.append(
                {"start": offset + 1, "end": offset + length, "maximum_score": score}
            )
    return _merge_windows(windows)


def _low_complexity_regions(sequence: str, length: int, threshold: float) -> list[dict[str, Any]]:
    windows = []
    for offset in range(0, len(sequence) - length + 1):
        score = threshold - _window_entropy(sequence[offset : offset + length])
        if score > 0:
            windows.append(
                {"start": offset + 1, "end": offset + length, "maximum_score": score}
            )
    return _merge_windows(windows)


def _homopolymers(sequence: str, minimum: int) -> list[dict[str, Any]]:
    regions = []
    start = 0
    for index in range(1, len(sequence) + 1):
        if index < len(sequence) and sequence[index] == sequence[start]:
            continue
        length = index - start
        if length >= minimum:
            regions.append(
                {
                    "start": start + 1,
                    "end": index,
                    "length": length,
                    "amino_acid": sequence[start],
                }
            )
        start = index
    return regions


def _motif_positions(sequence: str) -> list[dict[str, Any]]:
    motifs = []
    for index in range(len(sequence) - 2):
        triplet = sequence[index : index + 3]
        if triplet[0] == "N" and triplet[1] != "P" and triplet[2] in {"S", "T"}:
            motifs.append({"start": index + 1, "end": index + 3, "motif": triplet})
    return motifs


def _developability_analysis(
    config: ProjectConfig,
    spec: dict[str, Any],
    candidate_batch: dict[str, Any],
    structure_by_id: dict[str, dict[str, Any]],
    input_paths: dict[str, Path],
    candidate_batch_sha256: str,
    candidate_set_sha256: str,
    require_candidate_set_identity: bool,
) -> dict[str, Any]:
    candidates = candidate_batch["candidates"]
    candidate_by_id = {candidate["candidate_id"]: candidate for candidate in candidates}
    policy = spec.get("policy", {})
    hydrophobic_length = int(policy.get("hydrophobic_window_length", 19))
    hydrophobic_threshold = float(policy.get("hydrophobic_window_mean_kd", 1.6))
    complexity_length = int(policy.get("low_complexity_window_length", 12))
    complexity_threshold = float(policy.get("low_complexity_entropy_bits", 2.2))
    homopolymer_minimum = int(policy.get("homopolymer_min_length", 4))
    if min(hydrophobic_length, complexity_length, homopolymer_minimum) < 2:
        raise ValueError("Developability policy has invalid window lengths")
    requirements = []
    context = spec.get("expression_context", {})
    def context_field_is_specified(field: str) -> bool:
        value = context.get(field)
        return (
            isinstance(value, str)
            and value.strip().lower() not in UNSPECIFIED_VALUES
        )

    if not context_field_is_specified("host"):
        requirements.append(
            make_requirement(
                "declare-protein-expression-host",
                "Declare the protein expression host before constructing the recombinant protein branch.",
                "在构建重组蛋白分支前声明蛋白表达宿主。",
                requirement_class="blocking_now",
                required_before_stage="protein_product_design",
                resolution_strategy="human_design_decision",
            )
        )
    if not context_field_is_specified("compartment"):
        requirements.append(
            make_requirement(
                "define-protein-expression-compartment",
                "Enumerate or select the protein expression compartment as a product-design variable.",
                "将蛋白表达区室作为产品设计变量进行枚举或选择。",
                requirement_class="design_variable",
                required_before_stage="experiment_release",
                resolution_strategy="enumerate_or_select_design_variable",
            )
        )
    context_fields_complete = all(
        context_field_is_specified(field)
        for field in ("host", "compartment", "purification_strategy", "formulation_context")
    )
    if context.get("status") != "approved" or not context_fields_complete:
        requirements.append(
            make_requirement(
                "approve-expression-and-product-context",
                "Approve the selected expression compartment, purification strategy, and formulation context before release.",
                "在放行前批准选定的表达区室、纯化策略和制剂上下文。",
                requirement_class="required_before_release",
                required_before_stage="experiment_release",
                resolution_strategy="human_design_decision",
            )
        )
    adapter_states = {}
    for adapter_id in DEVELOPABILITY_ADAPTER_IDS:
        declaration = spec.get("external_adapters", {}).get(adapter_id)
        state = _adapter_state(
            config,
            adapter_id,
            declaration,
            candidate_by_id=candidate_by_id,
            candidate_batch_sha256=candidate_batch_sha256,
            candidate_set_sha256=candidate_set_sha256,
            require_candidate_set_identity=require_candidate_set_identity,
            input_paths=input_paths,
        )
        adapter_states[adapter_id] = state
        if state["status"] != "evaluated":
            if adapter_id in {"signal_peptide", "transmembrane_topology"}:
                requirement_class = "design_variable"
                resolution_strategy = "enumerate_or_select_design_variable"
                deadline = "experiment_release"
            elif adapter_id == "disorder":
                requirement_class = "required_before_ranking"
                resolution_strategy = "computational_or_experimental_evidence"
                deadline = "integrated_ranking"
            else:
                requirement_class = "required_before_release"
                resolution_strategy = "computational_or_experimental_evidence"
                deadline = "experiment_release"
            requirements.append(
                make_requirement(
                    f"provide-{adapter_id.replace('_', '-')}-prediction",
                    f"Provide checksum-bound {adapter_id} predictor evidence.",
                    f"提供与候选校验和绑定的 {adapter_id} 预测证据。",
                    requirement_class=requirement_class,
                    required_before_stage=deadline,
                    resolution_strategy=resolution_strategy,
                )
            )
    if policy.get("status") != "approved":
        requirements.append(
            make_requirement(
                "approve-developability-policy",
                "Calibrate and approve developability thresholds for release use.",
                "校准并批准用于实验放行的可开发性阈值。",
                requirement_class="required_before_release",
                required_before_stage="experiment_release",
                resolution_strategy="human_policy_approval",
            )
        )
    candidate_results = []
    for candidate in candidates:
        sequence = candidate["amino_acid_sequence"]
        hydrophobic = _hydrophobic_regions(
            sequence, hydrophobic_length, hydrophobic_threshold
        )
        low_complexity = _low_complexity_regions(
            sequence, complexity_length, complexity_threshold
        )
        homopolymers = _homopolymers(sequence, homopolymer_minimum)
        glycosylation = _motif_positions(sequence)
        cysteine_count = sequence.count("C")
        liabilities = []
        for region in hydrophobic:
            liabilities.append(
                {
                    "code": "hydrophobic_window",
                    "severity": "review",
                    "start": region["start"],
                    "end": region["end"],
                    "evidence": region,
                    "interpretation": "Intrinsic hydrophobicity descriptor; not a transmembrane prediction.",
                }
            )
        for region in low_complexity:
            liabilities.append(
                {
                    "code": "low_complexity_window",
                    "severity": "review",
                    "start": region["start"],
                    "end": region["end"],
                    "evidence": region,
                }
            )
        for region in homopolymers:
            liabilities.append(
                {
                    "code": "homopolymer",
                    "severity": "review",
                    "start": region["start"],
                    "end": region["end"],
                    "evidence": region,
                }
            )
        for motif in glycosylation:
            liabilities.append(
                {
                    "code": "n_linked_glycosylation_sequon",
                    "severity": "information",
                    "start": motif["start"],
                    "end": motif["end"],
                    "evidence": motif,
                    "interpretation": "Sequence motif only; occupancy and host processing are not predicted.",
                }
            )
        if cysteine_count % 2:
            liabilities.append(
                {
                    "code": "odd_cysteine_count",
                    "severity": "review",
                    "start": None,
                    "end": None,
                    "evidence": {"cysteine_count": cysteine_count},
                    "interpretation": "Construct-level descriptor; disulfide pairing is not inferred.",
                }
            )
        structure = structure_by_id[candidate["candidate_id"]]
        if structure["confidence_band"] == "low_confidence":
            liabilities.append(
                {
                    "code": "stage3_low_structure_confidence",
                    "severity": "review",
                    "start": None,
                    "end": None,
                    "evidence": {
                        "mean_plddt": structure["mean_plddt"],
                        "ptm": structure["ptm"],
                    },
                }
            )
        for flag in structure["review_flags"]:
            if flag["code"] == "low_confidence_component_boundary":
                liabilities.append(
                    {
                        "code": "stage3_low_confidence_component_boundary",
                        "severity": "review",
                        "start": None,
                        "end": None,
                        "evidence": flag,
                    }
                )
        gravy = sum(KYTE_DOOLITTLE[residue] for residue in sequence) / len(sequence)
        charge_proxy = (
            sequence.count("K")
            + sequence.count("R")
            + 0.1 * sequence.count("H")
            - sequence.count("D")
            - sequence.count("E")
        )
        candidate_results.append(
            {
                "candidate_id": candidate["candidate_id"],
                "candidate_key": candidate["candidate_key"],
                "sequence_sha256": candidate["amino_acid_sha256"],
                "length": len(sequence),
                "status": "intrinsic_assessed",
                "descriptors": {
                    "gravy": round(gravy, 6),
                    "charge_proxy": round(charge_proxy, 4),
                    "cysteine_count": cysteine_count,
                    "n_linked_glycosylation_sequon_count": len(glycosylation),
                    "hydrophobic_region_count": len(hydrophobic),
                    "low_complexity_region_count": len(low_complexity),
                    "homopolymer_count": len(homopolymers),
                },
                "liabilities": liabilities,
                "review_liability_count": sum(
                    liability["severity"] == "review" for liability in liabilities
                ),
                "information_liability_count": sum(
                    liability["severity"] == "information" for liability in liabilities
                ),
                "external_evidence": {
                    adapter_id: {
                        "status": state["status"],
                        **_candidate_observation_summary(
                            state, candidate["candidate_id"]
                        ),
                    }
                    for adapter_id, state in adapter_states.items()
                },
            }
        )
    return {
        "schema_version": 1,
        "stage_id": DEVELOPABILITY_STAGE_ID,
        "mode": "exploratory",
        "ruleset_id": DEVELOPABILITY_RULESET_ID,
        "specification_id": spec["specification_id"],
        "candidate_set_sha256": candidate_set_sha256,
        "status": "needs_data" if requirements else "evaluated",
        "release_gate_enabled": False,
        "expression_context": context,
        "requirements": requirements,
        "adapter_states": adapter_states,
        "candidates": candidate_results,
        "limitations": [
            "Intrinsic descriptors are not substitutes for calibrated property predictors.",
            "Hydrophobic windows are not transmembrane predictions.",
            "Sequence motifs do not establish modification occupancy or manufacturing failure.",
            "Missing external predictions remain not_evaluated.",
        ],
    }


def analyze_post_structure_stages(
    project_config: str | Path,
    *,
    source_run_dir: str | Path | None = None,
) -> PostStructureAnalysis:
    config = load_project_config(Path(project_config))
    source_run = _resolve_structure_run(
        config,
        Path(source_run_dir) if source_run_dir is not None else None,
    )
    source_manifest = _load_json(source_run / "manifest.json")
    candidate_scope = load_structure_candidate_scope(source_run)
    candidate_batch_path = candidate_scope["candidate_batch_path"]
    candidate_batch = candidate_scope["candidate_batch"]
    structure_document = _load_json(
        source_run
        / "nodes/protein_structure_assessment/structure_assessments.json"
    )
    structure_by_id = {
        assessment["candidate_id"]: assessment
        for assessment in structure_document["assessments"]
    }
    candidate_ids = [candidate["candidate_id"] for candidate in candidate_batch["candidates"]]
    if set(structure_by_id) != set(candidate_ids):
        raise ValueError("Stage 3 structures do not cover the exact candidate batch")
    structures = {
        candidate_id: parse_ca_pdb(
            source_run
            / "nodes/protein_structure_assessment/structures"
            / f"{candidate_id}.pdb"
        )
        for candidate_id in candidate_ids
    }
    immune_spec, immune_path = load_immune_specification(config)
    developability_spec, developability_path = load_developability_specification(config)
    input_paths = {
        "immune_specification": immune_path,
        "developability_specification": developability_path,
    }
    candidate_batch_sha = candidate_scope["candidate_batch_sha256"]
    candidate_set_sha = candidate_scope["candidate_set_sha256"]
    immune_result = _immune_analysis(
        config,
        immune_spec,
        candidate_batch,
        structure_by_id,
        structures,
        input_paths,
        candidate_batch_sha,
        candidate_set_sha,
        candidate_scope["is_subset"],
    )
    developability_result = _developability_analysis(
        config,
        developability_spec,
        candidate_batch,
        structure_by_id,
        input_paths,
        candidate_batch_sha,
        candidate_set_sha,
        candidate_scope["is_subset"],
    )
    return PostStructureAnalysis(
        config=config,
        source_run_dir=source_run,
        source_manifest=source_manifest,
        candidate_batch=candidate_batch,
        structure_assessments=structure_document,
        immune_specification=immune_spec,
        immune_specification_path=immune_path,
        developability_specification=developability_spec,
        developability_specification_path=developability_path,
        input_paths=input_paths,
        immune_result=immune_result,
        developability_result=developability_result,
    )
