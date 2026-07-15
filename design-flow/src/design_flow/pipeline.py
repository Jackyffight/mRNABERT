"""Project-level sequence audit pipeline."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .config import load_project_config
from .design_loop import load_design_dossier
from .domain import ProjectAnalysis
from .fasta import parse_fasta
from .qc import analyze_sequence_pairs
from .workflow import STAGE_BY_ID


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def analyze_project(config_path: Path) -> ProjectAnalysis:
    config = load_project_config(config_path)
    design_dossier = load_design_dossier(
        project_id=config.project_id,
        design_brief_path=config.design_brief,
        variable_registry_path=config.design_variable_registry,
        objective_policy_path=config.objective_policy,
        known_stage_ids=set(STAGE_BY_ID),
    )
    amino_acids = parse_fasta(config.amino_acid_fasta)
    nucleotides = parse_fasta(config.nucleotide_fasta)
    proteins, issues = analyze_sequence_pairs(
        amino_acids,
        nucleotides,
        config.expected_protein_count,
    )
    return ProjectAnalysis(
        config=config,
        proteins=proteins,
        issues=issues,
        input_digests={
            "project_config": sha256_file(config.config_path),
            "amino_acid_fasta": sha256_file(config.amino_acid_fasta),
            "nucleotide_fasta": sha256_file(config.nucleotide_fasta),
            **design_dossier.digests,
        },
        design_dossier=design_dossier,
    )
