"""Build auditable codon-usage tables from versioned RefSeq CDS FASTA files."""

from __future__ import annotations

from collections import Counter
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Iterator, TextIO

from .config import load_project_config
from .product_specs import MRNA_PRODUCT_STAGE_ID, MRNA_SPEC_RELATIVE
from .product_specs import CODON_USAGE_SCHEMA
from .qc import CANONICAL_AMINO_ACIDS, CODON_TABLE
from .stage6_routing import archive_runtime_file
from .structure_job import _load_json
from .verification import sha256_file


SENSE_CODONS = tuple(sorted(codon for codon, residue in CODON_TABLE.items() if residue != "*"))
STOP_CODONS = frozenset(codon for codon, residue in CODON_TABLE.items() if residue == "*")
HEADER_FIELD_PATTERN = re.compile(r"\[([^=\]]+)=([^\]]*)\]")
GENE_ID_PATTERN = re.compile(r"(?:^|,)GeneID:(\d+)(?:,|$)")
SELECTION_METHODS = frozenset({"all-valid-cds", "longest-valid-cds-per-gene"})
BENIGN_ANNOTATION_EXCEPTIONS = frozenset({"annotated by transcript or proteomic data"})


def _open_text(path: Path) -> TextIO:
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="ascii")
    return path.open("r", encoding="ascii")


def _iter_fasta(path: Path) -> Iterator[tuple[str, str]]:
    header: str | None = None
    sequence: list[str] = []
    with _open_text(path) as handle:
        for line_number, raw_line in enumerate(handle, 1):
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith(">"):
                if header is not None:
                    yield header, "".join(sequence).upper().replace("U", "T")
                header = line[1:]
                sequence = []
                continue
            if header is None:
                raise ValueError(f"FASTA sequence precedes first header at line {line_number}")
            sequence.append(line)
    if header is not None:
        yield header, "".join(sequence).upper().replace("U", "T")


def _header_fields(header: str) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {}
    for key, value in HEADER_FIELD_PATTERN.findall(header):
        fields.setdefault(key, []).append(value)
    return fields


def _gene_id(fields: dict[str, list[str]]) -> str | None:
    for cross_reference in fields.get("db_xref", []):
        match = GENE_ID_PATTERN.search(cross_reference)
        if match:
            return match.group(1)
    return None


def _file_digests(path: Path) -> tuple[str, str]:
    sha256 = hashlib.sha256()
    md5 = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha256.update(chunk)
            md5.update(chunk)
    return sha256.hexdigest(), md5.hexdigest()


def _validated_codons(
    header: str,
    sequence: str,
) -> tuple[str | None, list[str] | None, str | None]:
    fields = _header_fields(header)
    if fields.get("gbkey") != ["CDS"]:
        return None, None, "not_cds"
    if any(value.lower() in {"true", "yes", "1"} for value in fields.get("pseudo", [])):
        return None, None, "pseudo"
    if any(value.lower() not in {"false", "no", "0"} for value in fields.get("partial", [])):
        return None, None, "partial"
    annotation_exceptions = set(fields.get("exception", []))
    if "transl_except" in fields or annotation_exceptions - BENIGN_ANNOTATION_EXCEPTIONS:
        return None, None, "translation_exception"
    if any(value != "1" for value in fields.get("transl_table", ["1"])):
        return None, None, "nonstandard_translation_table"
    gene_id = _gene_id(fields)
    if gene_id is None:
        return None, None, "missing_gene_id"
    if not sequence:
        return None, None, "empty_sequence"
    if set(sequence) - set("ACGT"):
        return None, None, "ambiguous_or_invalid_base"
    if len(sequence) % 3:
        return None, None, "length_not_multiple_of_three"
    codons = [sequence[index : index + 3] for index in range(0, len(sequence), 3)]
    if codons and codons[-1] in STOP_CODONS:
        codons.pop()
    if not codons:
        return None, None, "no_sense_codons"
    if any(codon in STOP_CODONS for codon in codons):
        return None, None, "internal_stop"
    if any(codon not in CODON_TABLE for codon in codons):
        return None, None, "unknown_codon"
    return gene_id, codons, None


def _select_records(
    source_path: Path,
    selection_method: str,
) -> tuple[list[tuple[str, list[str]]], dict[str, object]]:
    if selection_method not in SELECTION_METHODS:
        raise ValueError(
            f"Unsupported selection method {selection_method!r}; "
            f"choose one of {sorted(SELECTION_METHODS)}"
        )
    total_records = 0
    valid_records: list[tuple[str, str, list[str]]] = []
    rejected = Counter()
    terminal_stop_records = 0
    for header, sequence in _iter_fasta(source_path):
        total_records += 1
        gene_id, codons, reason = _validated_codons(header, sequence)
        if reason is not None:
            rejected[reason] += 1
            continue
        assert gene_id is not None and codons is not None
        if sequence[-3:] in STOP_CODONS:
            terminal_stop_records += 1
        record_id = header.split(maxsplit=1)[0]
        valid_records.append((gene_id, record_id, codons))

    if selection_method == "all-valid-cds":
        selected = [(gene_id, codons) for gene_id, _record_id, codons in valid_records]
    else:
        per_gene: dict[str, tuple[str, list[str]]] = {}
        for gene_id, record_id, codons in valid_records:
            current = per_gene.get(gene_id)
            candidate_key = (-len(codons), record_id)
            if current is None or candidate_key < (-len(current[1]), current[0]):
                per_gene[gene_id] = (record_id, codons)
        selected = [(gene_id, per_gene[gene_id][1]) for gene_id in sorted(per_gene, key=int)]

    audit = {
        "total_fasta_records": total_records,
        "valid_standard_cds_records": len(valid_records),
        "selected_cds_records": len(selected),
        "selected_unique_gene_ids": len({gene_id for gene_id, _codons in selected}),
        "valid_records_with_terminal_stop": terminal_stop_records,
        "rejected_records_by_reason": dict(sorted(rejected.items())),
    }
    return selected, audit


def build_codon_usage(
    source_path: Path,
    *,
    species: str,
    taxon_id: int,
    assembly: str,
    annotation_release: str,
    source_url: str,
    expected_md5: str,
    selection_method: str = "longest-valid-cds-per-gene",
) -> tuple[dict[str, object], dict[str, object]]:
    """Return a Stage 6 codon table and a detailed source/filter audit."""

    source = source_path.expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"RefSeq CDS FASTA not found: {source}")
    if not species.strip() or not assembly.strip() or not annotation_release.strip():
        raise ValueError("species, assembly, and annotation_release must be non-empty")
    if taxon_id <= 0:
        raise ValueError("taxon_id must be positive")
    if not source_url.startswith("https://"):
        raise ValueError("source_url must be an HTTPS URL")
    source_sha256, source_md5 = _file_digests(source)
    if source_md5.lower() != expected_md5.lower():
        raise ValueError(
            f"RefSeq source MD5 mismatch: expected={expected_md5.lower()} "
            f"observed={source_md5.lower()}"
        )

    selected, record_audit = _select_records(source, selection_method)
    if not selected:
        raise ValueError("No valid RefSeq CDS records remain after filtering")
    counts = Counter({codon: 0 for codon in SENSE_CODONS})
    for _gene_id, codons in selected:
        counts.update(codons)
    missing_amino_acids = sorted(
        residue
        for residue in CANONICAL_AMINO_ACIDS
        if not any(counts[codon] for codon in SENSE_CODONS if CODON_TABLE[codon] == residue)
    )
    if missing_amino_acids:
        raise ValueError(
            "Selected CDS corpus has no observations for amino acid(s): "
            + ", ".join(missing_amino_acids)
        )
    total_codons = sum(counts.values())
    frequencies = {codon: counts[codon] / total_codons for codon in SENSE_CODONS}
    provenance = {
        "source": "NCBI RefSeq cds_from_genomic",
        "version": assembly,
        "revision": annotation_release,
        "source_url": source_url,
        "source_filename": source.name,
        "source_md5": source_md5,
        "source_sha256": source_sha256,
    }
    table: dict[str, object] = {
        "schema_version": CODON_USAGE_SCHEMA,
        "species": species,
        "taxon_id": taxon_id,
        "provenance": provenance,
        "derivation": {
            "genetic_code": "NCBI translation table 1",
            "selection_method": selection_method,
            "frequency_denominator": "all selected sense codons",
            "terminal_stop_codons_counted": False,
        },
        "codon_counts": dict(sorted(counts.items())),
        "codon_frequencies": frequencies,
    }
    audit: dict[str, object] = {
        "schema_version": "vaxflow.codon-usage-audit.v1",
        "species": species,
        "taxon_id": taxon_id,
        "provenance": provenance,
        "derivation": table["derivation"],
        "records": record_audit,
        "selected_sense_codons": total_codons,
        "codon_frequency_sum": sum(frequencies.values()),
        "codon_table_sha256": hashlib.sha256(
            json.dumps(table, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
                "utf-8"
            )
        ).hexdigest(),
    }
    return table, audit


def _atomic_json(path: Path, document: dict[str, object]) -> None:
    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, sort_keys=True, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def write_codon_usage(
    source_path: Path,
    output_path: Path,
    audit_path: Path,
    **metadata: object,
) -> dict[str, object]:
    table, audit = build_codon_usage(source_path, **metadata)
    _atomic_json(output_path, table)
    _atomic_json(audit_path, audit)
    return {
        "output_path": str(output_path.expanduser().resolve()),
        "audit_path": str(audit_path.expanduser().resolve()),
        "selected_cds_records": audit["records"]["selected_cds_records"],
        "selected_sense_codons": audit["selected_sense_codons"],
        "codon_table_sha256": audit["codon_table_sha256"],
    }


def configure_mrna_codon_generation(
    project_config: Path,
    codon_usage_path: Path,
    *,
    designs_per_candidate: int,
    search_multiplier: int,
    seed: int,
) -> dict[str, object]:
    """Bind a validated target-species table and enable exploratory CDS generation."""

    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 1
        for value in (designs_per_candidate, search_multiplier)
    ) or not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise ValueError("CDS generation counts must be positive integers and seed an integer")
    config = load_project_config(project_config.expanduser().resolve())
    table_path = codon_usage_path.expanduser().resolve()
    if not table_path.is_file():
        raise ValueError(f"Codon usage table not found: {table_path}")
    if not table_path.is_relative_to(config.runtime_root):
        raise ValueError("Codon usage table must be stored inside runtime_root")

    # Import lazily so the table contract has one validator without a module cycle.
    from .product_design import _load_codon_usage

    table = _load_codon_usage(table_path)
    specification_path = config.runtime_root / MRNA_SPEC_RELATIVE
    if not specification_path.is_file():
        raise ValueError(f"mRNA Stage 6 specification not found: {specification_path}")
    specification = _load_json(specification_path)
    if (
        specification.get("schema_version") != 2
        or specification.get("stage_id") != MRNA_PRODUCT_STAGE_ID
        or specification.get("mode") != "exploratory"
        or specification.get("policy", {}).get("allow_as_release_gate") is not False
    ):
        raise ValueError("Unsupported exploratory mRNA Stage 6 specification")
    target_species = str(specification.get("target_context", {}).get("species", ""))
    table_species = str(table.get("species", ""))
    normalized_target = re.sub(r"[^a-z0-9]", "", target_species.lower())
    normalized_table = re.sub(r"[^a-z0-9]", "", table_species.lower())
    if not normalized_table or normalized_table not in normalized_target:
        raise ValueError(
            "Codon table species does not match the declared mRNA target: "
            f"table={table_species!r} target={target_species!r}"
        )

    history_path = archive_runtime_file(
        specification_path,
        config.runtime_root / "input/stage6/history",
    )
    relative_table = table_path.relative_to(config.runtime_root).as_posix()
    specification["codon_usage_table_path"] = relative_table
    generation = specification.get("generation")
    if not isinstance(generation, dict):
        raise ValueError("mRNA generation specification must be an object")
    generation.update(
        {
            "status": "enabled",
            "seed": seed,
            "designs_per_candidate": designs_per_candidate,
            "search_multiplier": search_multiplier,
            "configuration_mode": "exploratory_mock",
            "codon_usage_file_sha256": sha256_file(table_path),
        }
    )
    _atomic_json(specification_path, specification)
    return {
        "specification_path": str(specification_path),
        "history_path": str(history_path),
        "codon_usage_path": str(table_path),
        "codon_usage_relative_path": relative_table,
        "codon_usage_file_sha256": sha256_file(table_path),
        "designs_per_candidate": designs_per_candidate,
        "search_multiplier": search_multiplier,
        "seed": seed,
    }
