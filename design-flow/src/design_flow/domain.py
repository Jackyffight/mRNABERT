"""Dependency-free domain objects shared by design-flow stages."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FastaRecord:
    record_id: str
    description: str
    sequence: str


@dataclass(frozen=True)
class ProjectConfig:
    schema_version: int
    project_id: str
    expected_protein_count: int
    runtime_root: Path
    amino_acid_fasta: Path
    nucleotide_fasta: Path
    run_root: Path
    target_indication: str
    intended_host_species: str
    product_modalities: tuple[str, ...]
    protein_expression_host: str
    mrna_target_species: str
    human_actions: tuple[HumanAction, ...]
    config_path: Path


@dataclass(frozen=True)
class HumanAction:
    action_id: str
    question: str
    required_before_stage: str
    status: str = "open"
    owner: str = "unassigned"
    resolution: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class QCIssue:
    severity: str
    code: str
    message: str
    protein_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProteinAnalysis:
    protein_id: str
    candidate_id: str
    amino_acid_sequence: str
    nucleotide_sequence: str
    translated_sequence: str | None
    metrics: dict[str, Any]
    issues: list[QCIssue] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "fail" if any(issue.severity == "error" for issue in self.issues) else "pass"

    def to_dict(self) -> dict[str, Any]:
        return {
            "protein_id": self.protein_id,
            "candidate_id": self.candidate_id,
            "status": self.status,
            "amino_acid_sequence": self.amino_acid_sequence,
            "nucleotide_sequence": self.nucleotide_sequence,
            "translated_sequence": self.translated_sequence,
            "metrics": self.metrics,
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass
class ProjectAnalysis:
    config: ProjectConfig
    proteins: list[ProteinAnalysis]
    issues: list[QCIssue]
    input_digests: dict[str, str]

    @property
    def all_issues(self) -> list[QCIssue]:
        return self.issues + [issue for protein in self.proteins for issue in protein.issues]

    @property
    def status(self) -> str:
        return "fail" if any(issue.severity == "error" for issue in self.all_issues) else "pass"
