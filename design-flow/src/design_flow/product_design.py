"""Deterministic Stage 6A protein and Stage 6B mRNA product design."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .config import ProjectConfig, load_project_config
from .product_specs import (
    CODON_USAGE_SCHEMA,
    MRNA_ADAPTER_IDS,
    MRNA_EVIDENCE_SCHEMA,
    MRNA_PRODUCT_STAGE_ID,
    PRODUCT_EVIDENCE_SCHEMA,
    PROTEIN_ADAPTER_IDS,
    PROTEIN_PRODUCT_STAGE_ID,
    _resolve_stage5_run,
    load_product_specifications,
    resolve_runtime_input,
)
from .qc import CANONICAL_AMINO_ACIDS, CODON_TABLE, normalize_nucleotide, translate_cds
from .structure_job import _load_json
from .verification import sha256_file


PROTEIN_RULESET_ID = "protein-product-audit-rules-v1"
MRNA_RULESET_ID = "mrna-synonymous-design-rules-v1"
SENSE_CODONS = {codon: amino_acid for codon, amino_acid in CODON_TABLE.items() if amino_acid != "*"}


@dataclass
class ProductDesignAnalysis:
    config: ProjectConfig
    source_run_dir: Path
    source_manifest: dict[str, Any]
    candidate_batch: dict[str, Any]
    protein_specification: dict[str, Any]
    protein_specification_path: Path
    mrna_specification: dict[str, Any]
    mrna_specification_path: Path
    input_paths: dict[str, Path]
    protein_result: dict[str, Any]
    mrna_result: dict[str, Any]


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_bindings(
    specification: dict[str, Any],
    candidate_by_id: dict[str, dict[str, Any]],
    field_name: str,
) -> list[dict[str, Any]]:
    selection = specification.get("selection")
    if not isinstance(selection, dict) or not isinstance(selection.get("candidates"), list):
        raise ValueError(f"{field_name}.selection.candidates must be an array")
    bindings = selection["candidates"]
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, binding in enumerate(bindings):
        if not isinstance(binding, dict):
            raise ValueError(f"{field_name}.selection.candidates[{index}] must be an object")
        candidate_id = binding.get("candidate_id")
        candidate = candidate_by_id.get(candidate_id)
        if candidate is None or candidate_id in seen:
            raise ValueError(f"{field_name} has an unknown or duplicate candidate binding")
        if (
            binding.get("candidate_key") != candidate["candidate_key"]
            or binding.get("amino_acid_sha256") != candidate["amino_acid_sha256"]
        ):
            raise ValueError(f"{field_name} candidate binding identity mismatch: {candidate_id}")
        seen.add(candidate_id)
        selected.append(candidate)
    if not selected:
        raise ValueError(f"{field_name} must select at least one candidate")
    return selected


def _read_sequence(path: Path) -> str:
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    headers = [line for line in lines if line.startswith(">")]
    if len(headers) > 1:
        raise ValueError(f"Sequence file must contain exactly one record: {path}")
    sequence_lines = [line for line in lines if line and not line.startswith((">", ";", "#"))]
    if not sequence_lines:
        raise ValueError(f"Sequence file is empty: {path}")
    return "".join(sequence_lines).upper().replace("U", "T")


def _load_codon_usage(path: Path) -> dict[str, Any]:
    document = _load_json(path)
    if document.get("schema_version") != CODON_USAGE_SCHEMA:
        raise ValueError("Unsupported codon usage table schema")
    provenance = document.get("provenance")
    if not isinstance(provenance, dict) or not all(
        isinstance(provenance.get(key), str) and provenance[key].strip()
        for key in ("source", "version", "revision")
    ):
        raise ValueError("Codon usage table must pin source/version/revision")
    frequencies = document.get("codon_frequencies")
    if not isinstance(frequencies, dict) or set(frequencies) != set(SENSE_CODONS):
        raise ValueError("Codon usage table must contain exactly the 61 sense codons")
    normalized: dict[str, float] = {}
    for codon, value in frequencies.items():
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or value < 0
        ):
            raise ValueError(f"Invalid codon frequency for {codon}")
        normalized[codon] = float(value)
    for amino_acid in CANONICAL_AMINO_ACIDS:
        if not any(
            normalized[codon] > 0
            for codon, encoded in SENSE_CODONS.items()
            if encoded == amino_acid
        ):
            raise ValueError(f"Codon usage has no positive codon for {amino_acid}")
    return {**document, "codon_frequencies": normalized}


def _synonymous_codons(codon_usage: dict[str, Any]) -> dict[str, list[str]]:
    frequencies = codon_usage["codon_frequencies"]
    return {
        amino_acid: sorted(
            (codon for codon, encoded in SENSE_CODONS.items() if encoded == amino_acid),
            key=lambda codon: (-frequencies[codon], codon),
        )
        for amino_acid in sorted(CANONICAL_AMINO_ACIDS)
    }


def _backtranslate_best(
    amino_acid_sequence: str,
    codon_usage: dict[str, Any],
    stop_codon: str,
) -> str:
    codons = _synonymous_codons(codon_usage)
    return "".join(codons[residue][0] for residue in amino_acid_sequence) + stop_codon


def _translation_matches(cds: str, amino_acid_sequence: str, identity: str) -> bool:
    normalized, normalization_issues = normalize_nucleotide(cds, identity)
    translated, translation_issues, _ = translate_cds(normalized, identity)
    errors = [
        issue
        for issue in (*normalization_issues, *translation_issues)
        if issue.severity == "error"
    ]
    return not errors and translated == amino_acid_sequence


def _validate_elements(raw_elements: Any, candidate_id: str) -> list[dict[str, Any]]:
    if not isinstance(raw_elements, list):
        raise ValueError(f"constructs.{candidate_id}.elements must be an array")
    elements: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_elements):
        if not isinstance(raw, dict):
            raise ValueError(f"construct element {index} for {candidate_id} must be an object")
        element_id = raw.get("element_id")
        sequence = raw.get("amino_acid_sequence")
        location = raw.get("location")
        role = raw.get("role")
        retained = raw.get("retained_in_final_product")
        if not isinstance(element_id, str) or not element_id or element_id in seen:
            raise ValueError(f"Invalid or duplicate element_id for {candidate_id}")
        if (
            not isinstance(sequence, str)
            or not sequence
            or set(sequence.upper()) - CANONICAL_AMINO_ACIDS
        ):
            raise ValueError(f"Invalid element sequence for {candidate_id}:{element_id}")
        if location not in {"n_terminal", "c_terminal"}:
            raise ValueError(f"Invalid element location for {candidate_id}:{element_id}")
        if not isinstance(role, str) or not role.strip() or not isinstance(retained, bool):
            raise ValueError(f"Incomplete element declaration for {candidate_id}:{element_id}")
        seen.add(element_id)
        elements.append(
            {
                "element_id": element_id,
                "location": location,
                "amino_acid_sequence": sequence.upper(),
                "role": role.strip(),
                "retained_in_final_product": retained,
            }
        )
    return elements


def _adapter_state(
    config: ProjectConfig,
    declaration: Any,
    *,
    adapter_id: str,
    schema: str,
    binding_field: str,
    binding_sha256: str,
    valid_ids: set[str],
    input_paths: dict[str, Path],
    input_key: str,
) -> dict[str, Any]:
    if not isinstance(declaration, dict):
        raise ValueError(f"Adapter declaration must be an object: {adapter_id}")
    status = declaration.get("status")
    path = resolve_runtime_input(
        config, declaration.get("result_path"), f"{input_key}.result_path"
    )
    if status == "not_configured":
        if path is not None:
            raise ValueError(f"Adapter {adapter_id} is not_configured but has a path")
        return {"adapter_id": adapter_id, "status": "not_evaluated", "reason": "not_configured"}
    if status != "provided" or path is None or not path.is_file():
        raise ValueError(f"Adapter {adapter_id} status/path is inconsistent")
    document = _load_json(path)
    if (
        document.get("schema_version") != schema
        or document.get("adapter_id") != adapter_id
        or document.get(binding_field) != binding_sha256
    ):
        raise ValueError(f"Adapter {adapter_id} evidence identity mismatch")
    tool = document.get("tool")
    if not isinstance(tool, dict) or not all(
        isinstance(tool.get(field), str) and tool[field].strip()
        for field in ("name", "version", "revision")
    ):
        raise ValueError(f"Adapter {adapter_id} must pin tool name/version/revision")
    observations = document.get("observations")
    if not isinstance(observations, list):
        raise ValueError(f"Adapter {adapter_id} observations must be an array")
    for observation in observations:
        if not isinstance(observation, dict) or observation.get("design_id") not in valid_ids:
            raise ValueError(f"Adapter {adapter_id} references an unknown design")
        if observation.get("status") not in {"supported", "risk", "context", "not_supported"}:
            raise ValueError(f"Adapter {adapter_id} observation has invalid status")
        if not isinstance(observation.get("evidence_id"), str):
            raise ValueError(f"Adapter {adapter_id} observation requires evidence_id")
        score = observation.get("score")
        if score is not None and (
            not isinstance(score, (int, float))
            or isinstance(score, bool)
            or not math.isfinite(float(score))
        ):
            raise ValueError(f"Adapter {adapter_id} observation score must be finite")
    input_paths[input_key] = path
    return {
        "adapter_id": adapter_id,
        "status": "evaluated",
        "tool": tool,
        "result_sha256": sha256_file(path),
        "observation_count": len(observations),
        "observations": observations,
    }


def _protein_analysis(
    config: ProjectConfig,
    spec: dict[str, Any],
    spec_path: Path,
    candidate_batch: dict[str, Any],
    codon_usage: dict[str, Any] | None,
    input_paths: dict[str, Path],
) -> dict[str, Any]:
    candidate_by_id = {
        candidate["candidate_id"]: candidate for candidate in candidate_batch["candidates"]
    }
    selected = _validate_bindings(spec, candidate_by_id, "protein_product")
    constructs = spec.get("constructs")
    if not isinstance(constructs, dict) or set(constructs) != {
        candidate["candidate_id"] for candidate in selected
    }:
        raise ValueError("Protein constructs must cover the exact selected candidate IDs")
    policy = spec.get("policy", {})
    stop_codon = policy.get("terminal_stop_codon", "TAA")
    if CODON_TABLE.get(stop_codon) != "*":
        raise ValueError("Protein product terminal_stop_codon must encode stop")
    requirements: list[dict[str, Any]] = []
    if spec.get("selection", {}).get("status") != "approved":
        requirements.append({
            "requirement_id": "approve-protein-product-selection",
            "status": "missing",
            "description": "Approve the selected antigen candidates for recombinant product design.",
        })
    expression_context = spec.get("expression_context", {})
    expression_context_complete = all(
        isinstance(expression_context.get(field), str)
        and expression_context[field].strip()
        and expression_context[field].strip().lower() != "unspecified"
        for field in (
            "host", "compartment", "vector_family", "purification_strategy",
            "final_product_form",
        )
    )
    if (
        expression_context.get("status") != "approved"
        or not expression_context_complete
    ):
        requirements.append({
            "requirement_id": "approve-protein-expression-context",
            "status": "missing",
            "description": "Approve expression host, vector, compartment, purification, and final product form.",
        })
    if policy.get("status") != "approved":
        requirements.append({
            "requirement_id": "approve-protein-product-policy",
            "status": "missing",
            "description": "Approve exact expression additions and protein-product release policy.",
        })
    products = []
    for candidate in selected:
        candidate_id = candidate["candidate_id"]
        declaration = constructs[candidate_id]
        if not isinstance(declaration, dict):
            raise ValueError(f"Protein construct declaration must be an object: {candidate_id}")
        elements = _validate_elements(declaration.get("elements"), candidate_id)
        n_terminal = [element for element in elements if element["location"] == "n_terminal"]
        c_terminal = [element for element in elements if element["location"] == "c_terminal"]
        antigen = candidate["amino_acid_sequence"]
        expression_sequence = (
            "".join(element["amino_acid_sequence"] for element in n_terminal)
            + antigen
            + "".join(element["amino_acid_sequence"] for element in c_terminal)
        )
        final_product_sequence = (
            "".join(
                element["amino_acid_sequence"]
                for element in n_terminal
                if element["retained_in_final_product"]
            )
            + antigen
            + "".join(
                element["amino_acid_sequence"]
                for element in c_terminal
                if element["retained_in_final_product"]
            )
        )
        coding_path = resolve_runtime_input(
            config,
            declaration.get("coding_sequence_path"),
            f"constructs.{candidate_id}.coding_sequence_path",
        )
        coding_source = "not_available"
        coding_sequence = None
        if coding_path is not None:
            if not coding_path.is_file():
                raise ValueError(f"Protein coding sequence file not found: {coding_path}")
            coding_sequence = _read_sequence(coding_path)
            input_paths[f"protein_cds:{candidate_id}"] = coding_path
            coding_source = "provided"
        elif not elements and candidate.get("nucleotide_sequence"):
            coding_sequence = candidate["nucleotide_sequence"]
            coding_source = "candidate_control"
        elif codon_usage is not None:
            coding_sequence = _backtranslate_best(expression_sequence, codon_usage, stop_codon)
            coding_source = "deterministic_codon_table"
        if coding_sequence is not None and not _translation_matches(
            coding_sequence, expression_sequence, candidate_id
        ):
            if coding_source == "candidate_control":
                coding_sequence = None
                coding_source = "candidate_control_rejected_translation_mismatch"
                if codon_usage is not None:
                    coding_sequence = _backtranslate_best(
                        expression_sequence, codon_usage, stop_codon
                    )
                    coding_source = "deterministic_codon_table_after_control_rejection"
            else:
                raise ValueError(
                    "Protein product CDS does not translate to expression construct: "
                    f"{candidate_id}"
                )
        if coding_sequence is None:
            requirements.append({
                "requirement_id": f"provide-protein-cds-{candidate_id}",
                "status": "missing",
                "description": f"Provide a matching CDS or codon table for protein product {candidate_id}.",
            })
        product_identity = {
            "candidate_id": candidate_id,
            "expression_sequence_sha256": _sha256_text(expression_sequence),
            "final_product_sequence_sha256": _sha256_text(final_product_sequence),
            "elements": elements,
            "specification_id": spec["specification_id"],
        }
        products.append({
            "design_id": f"protein-{_canonical_json_sha256(product_identity)[:16]}",
            "candidate_id": candidate_id,
            "candidate_key": candidate["candidate_key"],
            "antigen_sequence": antigen,
            "antigen_sequence_sha256": candidate["amino_acid_sha256"],
            "elements": elements,
            "expression_sequence": expression_sequence,
            "expression_sequence_sha256": _sha256_text(expression_sequence),
            "final_product_sequence": final_product_sequence,
            "final_product_sequence_sha256": _sha256_text(final_product_sequence),
            "coding_sequence_dna": coding_sequence,
            "coding_sequence_sha256": _sha256_text(coding_sequence) if coding_sequence else None,
            "coding_source": coding_source,
            "translation_verified": coding_sequence is not None,
            "requires_structure_recheck": expression_sequence != antigen,
            "status": "draft" if requirements else "audited",
        })
    product_batch_sha = _canonical_json_sha256(
        [{key: product[key] for key in ("design_id", "expression_sequence_sha256")} for product in products]
    )
    adapter_states = {}
    product_ids = {product["design_id"] for product in products}
    for adapter_id in PROTEIN_ADAPTER_IDS:
        state = _adapter_state(
            config,
            spec.get("external_adapters", {}).get(adapter_id),
            adapter_id=adapter_id,
            schema=PRODUCT_EVIDENCE_SCHEMA,
            binding_field="product_batch_sha256",
            binding_sha256=product_batch_sha,
            valid_ids=product_ids,
            input_paths=input_paths,
            input_key=f"protein_adapter:{adapter_id}",
        )
        adapter_states[adapter_id] = state
        if state["status"] != "evaluated":
            requirements.append({
                "requirement_id": f"provide-protein-{adapter_id.replace('_', '-')}",
                "status": "missing",
                "description": f"Provide checksum-bound {adapter_id} evidence for the protein product batch.",
            })
    for product in products:
        product["status"] = "needs_data" if any(
            requirement["requirement_id"].endswith(product["candidate_id"])
            for requirement in requirements
        ) else "draft_audited"
    return {
        "schema_version": 1,
        "stage_id": PROTEIN_PRODUCT_STAGE_ID,
        "mode": "exploratory",
        "ruleset_id": PROTEIN_RULESET_ID,
        "specification_id": spec["specification_id"],
        "specification_sha256": sha256_file(spec_path),
        "status": "needs_data" if requirements else "evaluated",
        "release_gate_enabled": False,
        "product_batch_sha256": product_batch_sha,
        "expression_context": spec["expression_context"],
        "requirements": requirements,
        "adapter_states": adapter_states,
        "products": products,
        "limitations": [
            "Expression constructs are exact sequence specifications, not evidence of expression yield.",
            "Expression-only additions never alter the antigen lineage silently.",
            "External expression and structure evidence remains not_evaluated until supplied.",
        ],
    }


def _longest_homopolymer(sequence: str) -> int:
    longest = current = 0
    previous = ""
    for character in sequence:
        current = current + 1 if character == previous else 1
        longest = max(longest, current)
        previous = character
    return longest


def _coding_metrics(
    cds: str,
    amino_acid_sequence: str,
    codon_usage: dict[str, Any],
    constraints: dict[str, Any],
) -> dict[str, Any]:
    coding = cds[:-3] if CODON_TABLE.get(cds[-3:]) == "*" else cds
    frequencies = codon_usage["codon_frequencies"]
    maxima = {
        amino_acid: max(
            frequencies[codon]
            for codon, encoded in SENSE_CODONS.items()
            if encoded == amino_acid
        )
        for amino_acid in CANONICAL_AMINO_ACIDS
    }
    relative = [
        max(frequencies[coding[index * 3 : index * 3 + 3]] / maxima[residue], 1e-12)
        for index, residue in enumerate(amino_acid_sequence)
    ]
    cai = math.exp(sum(math.log(value) for value in relative) / len(relative))
    gc_fraction = sum(base in "GC" for base in coding) / len(coding)
    motifs = [str(motif).upper().replace("U", "T") for motif in constraints["forbidden_motifs"]]
    motif_hits = [motif for motif in motifs if motif and motif in cds]
    return {
        "cai_proxy": round(cai, 6),
        "gc_fraction": round(gc_fraction, 6),
        "gc_target_deviation": round(abs(gc_fraction - constraints["target_gc_fraction"]), 6),
        "longest_homopolymer": _longest_homopolymer(cds),
        "forbidden_motif_hits": motif_hits,
    }


def _passes_coding_constraints(metrics: dict[str, Any], constraints: dict[str, Any]) -> bool:
    return (
        constraints["minimum_gc_fraction"] <= metrics["gc_fraction"] <= constraints["maximum_gc_fraction"]
        and metrics["longest_homopolymer"] <= constraints["maximum_homopolymer_length"]
        and not metrics["forbidden_motif_hits"]
    )


def _deterministic_cds(
    sequence: str,
    synonymous: dict[str, list[str]],
    *,
    seed: int,
    trial: int,
    candidate_id: str,
    stop_codon: str,
) -> str:
    selected = []
    for position, residue in enumerate(sequence):
        choices = synonymous[residue]
        if trial == 0 or len(choices) == 1:
            selected.append(choices[0])
            continue
        digest = hashlib.sha256(
            f"{seed}:{trial}:{candidate_id}:{position}".encode("ascii")
        ).digest()
        selector = int.from_bytes(digest[:4], "big")
        rank = 0 if selector % 100 < 65 else 1 + (selector % (len(choices) - 1))
        selected.append(choices[rank])
    return "".join(selected) + stop_codon


def _pareto_designs(
    records: list[dict[str, Any]],
    count: int,
) -> list[dict[str, Any]]:
    def dominates(left: dict[str, Any], right: dict[str, Any]) -> bool:
        left_values = (
            -left["metrics"]["cai_proxy"],
            left["metrics"]["gc_target_deviation"],
            left["metrics"]["longest_homopolymer"],
        )
        right_values = (
            -right["metrics"]["cai_proxy"],
            right["metrics"]["gc_target_deviation"],
            right["metrics"]["longest_homopolymer"],
        )
        return all(a <= b for a, b in zip(left_values, right_values, strict=True)) and any(
            a < b for a, b in zip(left_values, right_values, strict=True)
        )

    frontier = [
        record for record in records
        if not any(dominates(other, record) for other in records if other is not record)
    ]
    ordering = lambda item: (
        item["metrics"]["gc_target_deviation"],
        -item["metrics"]["cai_proxy"],
        item["metrics"]["longest_homopolymer"],
        item["coding_sequence_sha256"],
    )
    selected = [
        {**item, "selection_basis": "pareto_frontier"}
        for item in sorted(frontier, key=ordering)[:count]
    ]
    if len(selected) < count:
        selected_ids = {item["coding_sequence_sha256"] for item in selected}
        selected.extend(
            {**item, "selection_basis": "objective_order_fallback"}
            for item in sorted(records, key=ordering)
            if item["coding_sequence_sha256"] not in selected_ids
        )
    return selected[:count]


def _validate_constraints(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("mRNA constraints must be an object")
    numeric_fields = (
        raw.get("minimum_gc_fraction"),
        raw.get("maximum_gc_fraction"),
        raw.get("target_gc_fraction"),
    )
    if any(
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        for value in numeric_fields
    ):
        raise ValueError("mRNA GC constraints must be finite numbers")
    minimum, maximum, target = (float(value) for value in numeric_fields)
    homopolymer_raw = raw.get("maximum_homopolymer_length")
    if (
        not isinstance(homopolymer_raw, int)
        or isinstance(homopolymer_raw, bool)
    ):
        raise ValueError("maximum_homopolymer_length must be an integer")
    homopolymer = homopolymer_raw
    motifs = raw.get("forbidden_motifs")
    if not (0 <= minimum <= target <= maximum <= 1) or homopolymer < 1:
        raise ValueError("mRNA GC/homopolymer constraints are invalid")
    if not isinstance(motifs, list) or not all(isinstance(item, str) for item in motifs):
        raise ValueError("mRNA forbidden_motifs must be an array of strings")
    return {
        "minimum_gc_fraction": minimum,
        "maximum_gc_fraction": maximum,
        "target_gc_fraction": target,
        "maximum_homopolymer_length": homopolymer,
        "forbidden_motifs": motifs,
    }


def _mrna_analysis(
    config: ProjectConfig,
    spec: dict[str, Any],
    spec_path: Path,
    candidate_batch: dict[str, Any],
    codon_usage: dict[str, Any] | None,
    input_paths: dict[str, Path],
) -> dict[str, Any]:
    candidate_by_id = {
        candidate["candidate_id"]: candidate for candidate in candidate_batch["candidates"]
    }
    selected = _validate_bindings(spec, candidate_by_id, "mrna_product")
    policy = spec.get("policy", {})
    stop_codon = policy.get("terminal_stop_codon", "TAA")
    if CODON_TABLE.get(stop_codon) != "*":
        raise ValueError("mRNA terminal_stop_codon must encode stop")
    constraints = _validate_constraints(spec.get("constraints"))
    generation = spec.get("generation", {})
    if not isinstance(generation, dict):
        raise ValueError("mRNA generation must be an object")
    count = generation.get("designs_per_candidate", 4)
    multiplier = generation.get("search_multiplier", 32)
    seed = generation.get("seed", 42)
    if any(
        not isinstance(value, int) or isinstance(value, bool)
        for value in (count, multiplier, seed)
    ):
        raise ValueError("mRNA generation seed and counts must be integers")
    if count < 1 or multiplier < 1:
        raise ValueError("mRNA generation counts must be positive")
    requirements: list[dict[str, Any]] = []
    if spec.get("selection", {}).get("status") != "approved":
        requirements.append({
            "requirement_id": "approve-mrna-product-selection",
            "status": "missing",
            "description": "Approve the antigen candidates entering mRNA design.",
        })
    target_context = spec.get("target_context", {})
    target_context_complete = all(
        isinstance(target_context.get(field), str)
        and target_context[field].strip()
        and target_context[field].strip().lower() != "unspecified"
        for field in ("species", "cell_context", "delivery_platform")
    )
    if target_context.get("status") != "approved" or not target_context_complete:
        requirements.append({
            "requirement_id": "approve-mrna-target-and-delivery-context",
            "status": "missing",
            "description": "Approve target species/cell context and delivery platform assumptions.",
        })
    if generation.get("status") != "enabled" or codon_usage is None:
        requirements.append({
            "requirement_id": "provide-and-enable-mrna-codon-design",
            "status": "missing",
            "description": "Provide a versioned target-context codon table and enable synonymous design.",
        })
    noncoding = spec.get("noncoding_elements", {})
    if not isinstance(noncoding, dict) or noncoding.get("status") != "approved":
        requirements.append({
            "requirement_id": "approve-mrna-noncoding-elements",
            "status": "missing",
            "description": "Approve 5' UTR, 3' UTR, poly(A), cap, and nucleotide assumptions.",
        })
    if policy.get("status") != "approved":
        requirements.append({
            "requirement_id": "approve-mrna-product-policy",
            "status": "missing",
            "description": "Approve mRNA constraints, Pareto policy, and release use.",
        })
    designs: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    synonymous = _synonymous_codons(codon_usage) if codon_usage is not None else None
    for candidate in selected:
        candidate_id = candidate["candidate_id"]
        amino_acid_sequence = candidate["amino_acid_sequence"]
        source_cds = candidate.get("nucleotide_sequence")
        if source_cds and _translation_matches(source_cds, amino_acid_sequence, candidate_id):
            source_metrics = (
                _coding_metrics(source_cds, amino_acid_sequence, codon_usage, constraints)
                if codon_usage is not None
                else {
                    "cai_proxy": None,
                    "gc_fraction": round(sum(base in "GC" for base in source_cds) / len(source_cds), 6),
                    "gc_target_deviation": None,
                    "longest_homopolymer": _longest_homopolymer(source_cds),
                    "forbidden_motif_hits": [
                        motif.upper().replace("U", "T")
                        for motif in constraints["forbidden_motifs"]
                        if motif.upper().replace("U", "T") in source_cds
                    ],
                }
            )
            designs.append(
                _mrna_design_record(
                    candidate,
                    source_cds,
                    "source_cds_control",
                    source_metrics,
                    noncoding,
                    selection_basis="source_control",
                )
            )
        if generation.get("status") != "enabled" or synonymous is None or codon_usage is None:
            continue
        trial_records: list[dict[str, Any]] = []
        seen_sequences: set[str] = set()
        for trial in range(count * multiplier):
            cds = _deterministic_cds(
                amino_acid_sequence,
                synonymous,
                seed=seed,
                trial=trial,
                candidate_id=candidate_id,
                stop_codon=stop_codon,
            )
            digest = _sha256_text(cds)
            if digest in seen_sequences:
                continue
            seen_sequences.add(digest)
            metrics = _coding_metrics(cds, amino_acid_sequence, codon_usage, constraints)
            if not _passes_coding_constraints(metrics, constraints):
                rejected.append({
                    "candidate_id": candidate_id,
                    "coding_sequence_sha256": digest,
                    "reason": "hard_constraint_violation",
                    "metrics": metrics,
                })
                continue
            trial_records.append({
                "coding_sequence_dna": cds,
                "coding_sequence_sha256": digest,
                "metrics": metrics,
            })
        selected_records = _pareto_designs(trial_records, count)
        if len(selected_records) < count:
            requirements.append({
                "requirement_id": f"insufficient-feasible-mrna-designs-{candidate_id}",
                "status": "missing",
                "description": f"Only {len(selected_records)} feasible synonymous designs were found for {candidate_id}.",
            })
        for record in selected_records:
            designs.append(
                _mrna_design_record(
                    candidate,
                    record["coding_sequence_dna"],
                    (
                        "synonymous_pareto"
                        if record["selection_basis"] == "pareto_frontier"
                        else "synonymous_tradeoff_fallback"
                    ),
                    record["metrics"],
                    noncoding,
                    selection_basis=record["selection_basis"],
                )
            )
    design_batch_sha = _canonical_json_sha256(
        [{key: design[key] for key in ("design_id", "coding_sequence_sha256")} for design in designs]
    )
    adapter_states = {}
    design_ids = {design["design_id"] for design in designs}
    for adapter_id in MRNA_ADAPTER_IDS:
        state = _adapter_state(
            config,
            spec.get("external_adapters", {}).get(adapter_id),
            adapter_id=adapter_id,
            schema=MRNA_EVIDENCE_SCHEMA,
            binding_field="mrna_design_batch_sha256",
            binding_sha256=design_batch_sha,
            valid_ids=design_ids,
            input_paths=input_paths,
            input_key=f"mrna_adapter:{adapter_id}",
        )
        adapter_states[adapter_id] = state
        if state["status"] != "evaluated":
            requirements.append({
                "requirement_id": f"provide-mrna-{adapter_id.replace('_', '-')}",
                "status": "missing",
                "description": f"Provide checksum-bound {adapter_id} evidence for the mRNA design batch.",
            })
    return {
        "schema_version": 1,
        "stage_id": MRNA_PRODUCT_STAGE_ID,
        "mode": "exploratory",
        "ruleset_id": MRNA_RULESET_ID,
        "specification_id": spec["specification_id"],
        "specification_sha256": sha256_file(spec_path),
        "status": "needs_data" if requirements else "evaluated",
        "release_gate_enabled": False,
        "mrna_design_batch_sha256": design_batch_sha,
        "target_context": spec["target_context"],
        "constraints": constraints,
        "noncoding_elements": noncoding,
        "requirements": requirements,
        "adapter_states": adapter_states,
        "designs": designs,
        "rejected_designs": rejected,
        "limitations": [
            "Codon adaptation is a table-based proxy and does not establish expression.",
            "RNA structure and sequence-model evidence remains not_evaluated until supplied.",
            "Non-coding elements are not released until exact licensed sequences and assumptions are approved.",
            "Every coding design is accepted only after exact translation-identity verification.",
        ],
    }


def _mrna_design_record(
    candidate: dict[str, Any],
    cds: str,
    design_type: str,
    metrics: dict[str, Any],
    noncoding: dict[str, Any],
    *,
    selection_basis: str,
) -> dict[str, Any]:
    if not _translation_matches(cds, candidate["amino_acid_sequence"], candidate["candidate_id"]):
        raise ValueError(f"mRNA coding design translation mismatch: {candidate['candidate_id']}")
    full_mrna = None
    if noncoding.get("status") == "approved":
        five = str(noncoding.get("five_prime_utr", "")).upper().replace("T", "U")
        three = str(noncoding.get("three_prime_utr", "")).upper().replace("T", "U")
        poly_a = noncoding.get("poly_a_length")
        if set(five + three) - set("ACGU") or not isinstance(poly_a, int) or poly_a < 0:
            raise ValueError("Approved mRNA noncoding elements are invalid")
        full_mrna = five + cds.replace("T", "U") + three + "A" * poly_a
    identity = {
        "candidate_id": candidate["candidate_id"],
        "coding_sequence_sha256": _sha256_text(cds),
        "design_type": design_type,
        "selection_basis": selection_basis,
        "full_mrna_sha256": _sha256_text(full_mrna) if full_mrna else None,
    }
    return {
        "design_id": f"mrna-{_canonical_json_sha256(identity)[:16]}",
        "candidate_id": candidate["candidate_id"],
        "candidate_key": candidate["candidate_key"],
        "design_type": design_type,
        "antigen_sequence_sha256": candidate["amino_acid_sha256"],
        "coding_sequence_dna": cds,
        "coding_sequence_sha256": _sha256_text(cds),
        "coding_sequence_rna": cds.replace("T", "U"),
        "translation_verified": True,
        "metrics": metrics,
        "full_mrna_sequence": full_mrna,
        "full_mrna_sha256": _sha256_text(full_mrna) if full_mrna else None,
        "status": "full_construct_audited" if full_mrna else "coding_only",
    }


def analyze_product_designs(
    project_config: str | Path,
    *,
    source_run_dir: str | Path | None = None,
) -> ProductDesignAnalysis:
    config = load_project_config(Path(project_config))
    source = _resolve_stage5_run(
        config, Path(source_run_dir) if source_run_dir is not None else None
    )
    source_manifest = _load_json(source / "manifest.json")
    candidate_batch = _load_json(
        source / "nodes/candidate_specification/candidate_batch.json"
    )
    protein_spec, protein_path, mrna_spec, mrna_path = load_product_specifications(config)
    input_paths = {
        "protein_specification": protein_path,
        "mrna_specification": mrna_path,
    }
    protein_codon_path = resolve_runtime_input(
        config, protein_spec.get("codon_usage_table_path"), "protein.codon_usage_table_path"
    )
    mrna_codon_path = resolve_runtime_input(
        config, mrna_spec.get("codon_usage_table_path"), "mrna.codon_usage_table_path"
    )
    protein_codon = None
    if protein_codon_path is not None:
        if not protein_codon_path.is_file():
            raise ValueError(f"Protein codon usage table not found: {protein_codon_path}")
        protein_codon = _load_codon_usage(protein_codon_path)
        input_paths["protein_codon_usage"] = protein_codon_path
    mrna_codon = None
    if mrna_codon_path is not None:
        if not mrna_codon_path.is_file():
            raise ValueError(f"mRNA codon usage table not found: {mrna_codon_path}")
        mrna_codon = _load_codon_usage(mrna_codon_path)
        input_paths["mrna_codon_usage"] = mrna_codon_path
    protein_result = _protein_analysis(
        config, protein_spec, protein_path, candidate_batch, protein_codon, input_paths
    )
    mrna_result = _mrna_analysis(
        config, mrna_spec, mrna_path, candidate_batch, mrna_codon, input_paths
    )
    _apply_upstream_developability_requirement(
        source, protein_result, mrna_result
    )
    return ProductDesignAnalysis(
        config=config,
        source_run_dir=source,
        source_manifest=source_manifest,
        candidate_batch=candidate_batch,
        protein_specification=protein_spec,
        protein_specification_path=protein_path,
        mrna_specification=mrna_spec,
        mrna_specification_path=mrna_path,
        input_paths=input_paths,
        protein_result=protein_result,
        mrna_result=mrna_result,
    )


def _apply_upstream_developability_requirement(
    source: Path,
    protein_result: dict[str, Any],
    mrna_result: dict[str, Any],
) -> None:
    upstream_developability = _load_json(
        source / "nodes/developability_assessment/developability_assessments.json"
    )
    if upstream_developability.get("status") == "needs_data":
        upstream_requirement = {
            "requirement_id": "resolve-upstream-developability-gaps",
            "status": "missing",
            "description": "Resolve or explicitly waive Stage 5 developability data gaps before product release.",
        }
        for result in (protein_result, mrna_result):
            if not any(
                item["requirement_id"] == upstream_requirement["requirement_id"]
                for item in result["requirements"]
            ):
                result["requirements"].append(dict(upstream_requirement))
            result["status"] = "needs_data"
