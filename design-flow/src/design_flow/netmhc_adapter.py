"""Deterministic NetMHCpan/NetMHCIIpan adapter for Stage 4 evidence."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Callable, Iterable

from .assessment_specs import (
    EVIDENCE_SCHEMA,
    IMMUNE_SPEC_RELATIVE,
    _atomic_json,
    _resolve_structure_run,
    initialize_assessment_specifications,
    load_residue_evidence,
)
from .config import ProjectConfig, load_project_config
from .structure_job import _load_json
from .verification import sha256_file


ADAPTER_SCHEMA = "vaxflow.netmhc-adapter.v1"
NETMHCPAN_BINARY = Path("Linux_x86_64/bin/netMHCpan-4.2")
NETMHCIIPAN_BINARY = Path("Linux_x86_64/bin/NetMHCIIpan-4.3")
NETMHCPAN_VERSION = "4.2e"
NETMHCIIPAN_VERSION = "4.3k"
CLASS_I_LENGTHS = (8, 9, 10, 11)
CLASS_II_LENGTHS = (15,)
CLASS_I_STRONG_RANK = 0.5
CLASS_I_WEAK_RANK = 2.0
CLASS_II_STRONG_RANK = 1.0
CLASS_II_WEAK_RANK = 5.0


@dataclass(frozen=True)
class NetMHCPrediction:
    mhc_class: str
    allele: str
    record_id: str
    position: int
    peptide: str
    core: str
    el_score: float
    el_rank: float
    ba_score: float | None
    ba_rank: float | None
    affinity_nm: float | None


@dataclass(frozen=True)
class ToolIdentity:
    name: str
    version: str
    package_root: str
    binary_path: str
    binary_sha256: str
    model_sha256: str
    version_sha256: str


def _float(value: str, field_name: str) -> float:
    try:
        return float(value)
    except ValueError as error:
        raise ValueError(f"Invalid numeric value for {field_name}: {value!r}") from error


def _optional_float(row: dict[str, str], field_name: str) -> float | None:
    value = row.get(field_name, "").strip()
    return _float(value, field_name) if value else None


def _read_single_allele_xls(path: Path, required: set[str]) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    header_index = next(
        (index for index, row in enumerate(rows) if row and row[0].strip() == "Pos"),
        None,
    )
    if header_index is None:
        raise ValueError(f"Prediction table has no Pos header: {path}")
    header = [value.strip() for value in rows[header_index]]
    if len(header) != len(set(header)):
        raise ValueError(
            f"Prediction table contains repeated columns; run one allele per file: {path}"
        )
    missing = sorted(required - set(header))
    if missing:
        raise ValueError(f"Prediction table is missing columns {missing}: {path}")

    parsed = []
    for values in rows[header_index + 1 :]:
        if not values or not any(value.strip() for value in values):
            continue
        if len(values) != len(header):
            raise ValueError(
                f"Prediction row has {len(values)} fields, expected {len(header)}: {path}"
            )
        parsed.append(dict(zip(header, (value.strip() for value in values), strict=True)))
    if not parsed:
        raise ValueError(f"Prediction table contains no peptide rows: {path}")
    return parsed


def parse_netmhcpan_xls(path: Path, allele: str) -> list[NetMHCPrediction]:
    """Parse a one-allele NetMHCpan 4.2e BA table."""

    rows = _read_single_allele_xls(
        path,
        {
            "Pos",
            "Peptide",
            "ID",
            "core",
            "EL_score",
            "EL_rank",
            "BA_score",
            "BA_rank",
        },
    )
    return [
        NetMHCPrediction(
            mhc_class="I",
            allele=allele,
            record_id=row["ID"],
            position=int(row["Pos"]),
            peptide=row["Peptide"],
            core=row["core"],
            el_score=_float(row["EL_score"], "EL_score"),
            el_rank=_float(row["EL_rank"], "EL_rank"),
            ba_score=_optional_float(row, "BA_score"),
            ba_rank=_optional_float(row, "BA_rank"),
            affinity_nm=None,
        )
        for row in rows
    ]


def parse_netmhciipan_xls(path: Path, allele: str) -> list[NetMHCPrediction]:
    """Parse a one-allele NetMHCIIpan 4.3k BA table."""

    rows = _read_single_allele_xls(
        path,
        {
            "Pos",
            "Peptide",
            "ID",
            "Core",
            "Score_EL",
            "Rank_EL",
            "Score_BA",
            "nM",
            "Rank_BA",
        },
    )
    return [
        NetMHCPrediction(
            mhc_class="II",
            allele=allele,
            record_id=row["ID"],
            position=int(row["Pos"]),
            peptide=row["Peptide"],
            core=row["Core"],
            el_score=_float(row["Score_EL"], "Score_EL"),
            el_rank=_float(row["Rank_EL"], "Rank_EL"),
            ba_score=_optional_float(row, "Score_BA"),
            ba_rank=_optional_float(row, "Rank_BA"),
            affinity_nm=_optional_float(row, "nM"),
        )
        for row in rows
    ]


def _validate_tool(
    package_root: Path,
    *,
    name: str,
    expected_version: str,
    binary_relative: Path,
) -> ToolIdentity:
    root = package_root.expanduser().resolve()
    binary = root / binary_relative
    version_path = root / "data/version"
    model_path = root / "data/synlist_nocontext.bin"
    for label, path in (
        ("binary", binary),
        ("version file", version_path),
        ("model weights", model_path),
    ):
        if not path.is_file():
            raise ValueError(f"{name} {label} not found: {path}")
    if not os.access(binary, os.X_OK):
        raise ValueError(f"{name} binary is not executable: {binary}")
    version_text = version_path.read_text(encoding="utf-8", errors="replace")
    if expected_version not in version_text:
        raise ValueError(
            f"{name} version mismatch: expected {expected_version!r} in {version_path}"
        )
    return ToolIdentity(
        name=name,
        version=expected_version,
        package_root=str(root),
        binary_path=str(binary),
        binary_sha256=sha256_file(binary),
        model_sha256=sha256_file(model_path),
        version_sha256=sha256_file(version_path),
    )


def _candidate_records(candidate_batch: dict[str, Any]) -> tuple[str, dict[str, dict[str, Any]]]:
    lines = []
    record_map: dict[str, dict[str, Any]] = {}
    for index, candidate in enumerate(candidate_batch.get("candidates", [])):
        record_id = f"c{index:03d}"
        sequence = candidate.get("amino_acid_sequence")
        candidate_id = candidate.get("candidate_id")
        if not isinstance(sequence, str) or not sequence:
            raise ValueError(f"Candidate {candidate_id} has no amino-acid sequence")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ValueError(f"Candidate at index {index} has no candidate_id")
        record_map[record_id] = candidate
        lines.extend((f">{record_id}", sequence))
    if not record_map:
        raise ValueError("Candidate batch contains no candidates")
    return "\n".join(lines) + "\n", record_map


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_") or "allele"


def _run_prediction(
    *,
    tool: ToolIdentity,
    package_variable: str,
    arguments: list[str],
    output_log: Path,
    temporary_root: Path,
) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "NMHOME": tool.package_root,
            package_variable: str(Path(tool.package_root) / "Linux_x86_64"),
            "TMPDIR": str(temporary_root),
        }
    )
    temporary_root.mkdir(parents=True, exist_ok=True)
    with output_log.open("w", encoding="utf-8") as handle:
        try:
            subprocess.run(
                [tool.binary_path, *arguments],
                cwd=tool.package_root,
                env=environment,
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as error:
            handle.flush()
            log_lines = output_log.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
            log_tail = "\n".join(log_lines[-40:]) or "<empty predictor log>"
            raise ValueError(
                f"{tool.name} failed with exit code {error.returncode}.\n"
                f"Predictor log tail:\n{log_tail}"
            ) from error


def _binding_level(prediction: NetMHCPrediction) -> str:
    if prediction.mhc_class == "I":
        strong, weak = CLASS_I_STRONG_RANK, CLASS_I_WEAK_RANK
    else:
        strong, weak = CLASS_II_STRONG_RANK, CLASS_II_WEAK_RANK
    if prediction.el_rank <= strong:
        return "strong"
    if prediction.el_rank <= weak:
        return "weak"
    return "not_supported"


def build_mhc_observations(
    predictions: Iterable[NetMHCPrediction],
    record_map: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Bind parsed peptide predictions to immutable candidate sequence identities."""

    observations = []
    for prediction in predictions:
        candidate = record_map.get(prediction.record_id)
        if candidate is None:
            raise ValueError(
                f"Prediction references unknown short record ID {prediction.record_id!r}"
            )
        sequence = candidate["amino_acid_sequence"]
        start = prediction.position
        end = start + len(prediction.peptide) - 1
        if start < 1 or end > len(sequence) or sequence[start - 1 : end] != prediction.peptide:
            raise ValueError(
                "Prediction peptide does not match its candidate sequence: "
                f"record={prediction.record_id} position={start} peptide={prediction.peptide}"
            )
        level = _binding_level(prediction)
        identity_text = "|".join(
            (
                prediction.mhc_class,
                prediction.allele,
                candidate["candidate_id"],
                str(start),
                prediction.peptide,
            )
        )
        observations.append(
            {
                "evidence_id": "mhc-" + hashlib.sha256(identity_text.encode("utf-8")).hexdigest()[:24],
                "candidate_id": candidate["candidate_id"],
                "sequence_sha256": candidate["amino_acid_sha256"],
                "residue_start": start,
                "residue_end": end,
                "status": "supported" if level in {"strong", "weak"} else "not_supported",
                "mhc_class": prediction.mhc_class,
                "allele": prediction.allele,
                "peptide": prediction.peptide,
                "core": prediction.core,
                "binding_level": level,
                "classification_metric": "EL_rank",
                "el_score": prediction.el_score,
                "el_rank": prediction.el_rank,
                "ba_score": prediction.ba_score,
                "ba_rank": prediction.ba_rank,
                "affinity_nm": prediction.affinity_nm,
            }
        )
    observations.sort(
        key=lambda item: (
            item["candidate_id"],
            item["mhc_class"],
            item["allele"],
            item["residue_start"],
            item["peptide"],
        )
    )
    return observations


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode(
        "utf-8"
    )


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(value))


def _identity_payload(
    *,
    project_id: str,
    candidate_batch_sha256: str,
    netmhcpan: ToolIdentity,
    netmhciipan: ToolIdentity,
    class_i_alleles: tuple[str, ...],
    class_ii_alleles: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "schema_version": ADAPTER_SCHEMA,
        "project_id": project_id,
        "candidate_batch_sha256": candidate_batch_sha256,
        "tools": {
            "netmhcpan": asdict(netmhcpan),
            "netmhciipan": asdict(netmhciipan),
        },
        "parameters": {
            "class_i_alleles": list(class_i_alleles),
            "class_ii_alleles": list(class_ii_alleles),
            "class_i_lengths": list(CLASS_I_LENGTHS),
            "class_ii_lengths": list(CLASS_II_LENGTHS),
            "class_i_strong_el_rank": CLASS_I_STRONG_RANK,
            "class_i_weak_el_rank": CLASS_I_WEAK_RANK,
            "class_ii_strong_el_rank": CLASS_II_STRONG_RANK,
            "class_ii_weak_el_rank": CLASS_II_WEAK_RANK,
        },
    }


def _verify_existing_output(output_dir: Path, identity: str) -> dict[str, Any]:
    manifest = _load_json(output_dir / "manifest.json")
    if manifest.get("identity") != identity:
        raise ValueError(f"Existing NetMHC output has a different identity: {output_dir}")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError(f"Existing NetMHC manifest has no artifact index: {output_dir}")
    for relative, expected_sha256 in artifacts.items():
        path = output_dir / relative
        if not path.is_file() or sha256_file(path) != expected_sha256:
            raise ValueError(f"Existing NetMHC artifact failed integrity check: {path}")
    return manifest


def _preserve_failed_output(
    partial: Path,
    failed: Path,
    *,
    identity: str,
    error: Exception,
) -> Path:
    if failed.exists():
        shutil.rmtree(failed)
    if partial.exists():
        partial.rename(failed)
    else:
        failed.mkdir(parents=True)
    _write_json(
        failed / "failure.json",
        {
            "schema_version": ADAPTER_SCHEMA,
            "identity": identity,
            "error_type": type(error).__name__,
            "error": str(error),
        },
    )
    return failed


def _update_immune_specification(
    config: ProjectConfig,
    *,
    panel_path: Path,
    evidence_path: Path,
) -> Path:
    specification_path = config.runtime_root / IMMUNE_SPEC_RELATIVE
    specification = _load_json(specification_path)
    specification["host"]["mhc_panel_path"] = panel_path.relative_to(
        config.runtime_root
    ).as_posix()
    specification["adapters"]["mhc_binding"] = {
        "status": "provided",
        "result_path": evidence_path.relative_to(config.runtime_root).as_posix(),
    }
    _atomic_json(specification_path, specification)
    return specification_path


def prepare_stage4_mhc_evidence(
    project_config: str | Path,
    *,
    source_run_dir: str | Path | None,
    netmhcpan_root: str | Path,
    netmhciipan_root: str | Path,
    class_i_alleles: Iterable[str],
    class_ii_alleles: Iterable[str],
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run both predictors and register checksum-bound Stage 4 MHC evidence."""

    config = load_project_config(Path(project_config))
    source = _resolve_structure_run(
        config,
        Path(source_run_dir) if source_run_dir is not None else None,
    )
    initialized = initialize_assessment_specifications(
        project_config,
        source_run_dir=source,
    )
    del initialized

    class_i = tuple(dict.fromkeys(allele.strip() for allele in class_i_alleles if allele.strip()))
    class_ii = tuple(
        dict.fromkeys(allele.strip() for allele in class_ii_alleles if allele.strip())
    )
    if not class_i or not class_ii:
        raise ValueError("At least one class I and one class II allele are required")

    netmhcpan = _validate_tool(
        Path(netmhcpan_root),
        name="NetMHCpan",
        expected_version=NETMHCPAN_VERSION,
        binary_relative=NETMHCPAN_BINARY,
    )
    netmhciipan = _validate_tool(
        Path(netmhciipan_root),
        name="NetMHCIIpan",
        expected_version=NETMHCIIPAN_VERSION,
        binary_relative=NETMHCIIPAN_BINARY,
    )
    candidate_batch_path = source / "nodes/candidate_specification/candidate_batch.json"
    candidate_batch = _load_json(candidate_batch_path)
    candidate_batch_sha256 = sha256_file(candidate_batch_path)
    fasta_text, record_map = _candidate_records(candidate_batch)
    identity_payload = _identity_payload(
        project_id=config.project_id,
        candidate_batch_sha256=candidate_batch_sha256,
        netmhcpan=netmhcpan,
        netmhciipan=netmhciipan,
        class_i_alleles=class_i,
        class_ii_alleles=class_ii,
    )
    identity = hashlib.sha256(
        json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    output_parent = config.runtime_root / "input/stage4/netmhc"
    output_dir = output_parent / identity
    output_parent.mkdir(parents=True, exist_ok=True)

    if output_dir.is_dir():
        manifest = _verify_existing_output(output_dir, identity)
    else:
        partial = output_parent / f".{identity}.partial"
        failed = output_parent / f"{identity}.failed"
        if partial.exists():
            shutil.rmtree(partial)
        if failed.exists():
            shutil.rmtree(failed)
        raw_root = partial / "raw"
        temporary_root = partial / "tmp"
        raw_root.mkdir(parents=True)
        fasta_path = partial / "candidates.fasta"
        fasta_path.write_text(fasta_text, encoding="utf-8")
        predictions: list[NetMHCPrediction] = []
        try:
            for index, allele in enumerate(class_i):
                stem = f"class-i-{index:03d}-{_safe_name(allele)}"
                table = raw_root / f"{stem}.xls"
                log = raw_root / f"{stem}.log"
                if progress is not None:
                    progress(
                        f"Running NetMHCpan {NETMHCPAN_VERSION}: "
                        f"allele={allele} candidates={len(record_map)}"
                    )
                _run_prediction(
                    tool=netmhcpan,
                    package_variable="NETMHCpan",
                    arguments=[
                        "-BA",
                        "-a",
                        allele,
                        "-l",
                        ",".join(str(length) for length in CLASS_I_LENGTHS),
                        "-t",
                        "-99.9",
                        "-xls",
                        "-xlsfile",
                        str(table),
                        "-f",
                        str(fasta_path),
                    ],
                    output_log=log,
                    temporary_root=temporary_root,
                )
                parsed = parse_netmhcpan_xls(table, allele)
                predictions.extend(parsed)
                if progress is not None:
                    progress(f"NetMHCpan allele complete: {allele} rows={len(parsed)}")
            for index, allele in enumerate(class_ii):
                stem = f"class-ii-{index:03d}-{_safe_name(allele)}"
                table = raw_root / f"{stem}.xls"
                log = raw_root / f"{stem}.log"
                if progress is not None:
                    progress(
                        f"Running NetMHCIIpan {NETMHCIIPAN_VERSION}: "
                        f"allele={allele} candidates={len(record_map)}"
                    )
                _run_prediction(
                    tool=netmhciipan,
                    package_variable="NETMHCIIpan",
                    arguments=[
                        "-BA",
                        "-a",
                        allele,
                        "-length",
                        ",".join(str(length) for length in CLASS_II_LENGTHS),
                        "-xls",
                        "-xlsfile",
                        str(table),
                        "-f",
                        str(fasta_path),
                    ],
                    output_log=log,
                    temporary_root=temporary_root,
                )
                parsed = parse_netmhciipan_xls(table, allele)
                predictions.extend(parsed)
                if progress is not None:
                    progress(f"NetMHCIIpan allele complete: {allele} rows={len(parsed)}")

            observations = build_mhc_observations(predictions, record_map)
            evidence = {
                "schema_version": EVIDENCE_SCHEMA,
                "adapter_id": "mhc_binding",
                "candidate_batch_sha256": candidate_batch_sha256,
                "tool": {
                    "name": "NetMHCpan+NetMHCIIpan",
                    "version": f"{NETMHCPAN_VERSION}+{NETMHCIIPAN_VERSION}",
                    "revision": identity,
                },
                "classification_policy": {
                    "metric": "EL_rank",
                    "class_i": {
                        "strong_max": CLASS_I_STRONG_RANK,
                        "weak_max": CLASS_I_WEAK_RANK,
                    },
                    "class_ii": {
                        "strong_max": CLASS_II_STRONG_RANK,
                        "weak_max": CLASS_II_WEAK_RANK,
                    },
                    "source": "predictor defaults",
                },
                "observations": observations,
            }
            evidence_path = partial / "mhc_binding.json"
            _write_json(evidence_path, evidence)
            panel = {
                "schema_version": 1,
                "panel_id": f"technical-smoke-{identity[:12]}",
                "scope": "technical_smoke_test",
                "host_species": config.intended_host_species,
                "population_status": "not_approved",
                "population_coverage_claim_allowed": False,
                "alleles": [
                    {"mhc_class": "I", "name": allele, "predictor": "NetMHCpan"}
                    for allele in class_i
                ]
                + [
                    {"mhc_class": "II", "name": allele, "predictor": "NetMHCIIpan"}
                    for allele in class_ii
                ],
                "limitations": [
                    "Alleles were selected only to verify the executable adapter path.",
                    "This panel is not a cattle population or breed coverage panel.",
                    "A human-approved population panel is required before scientific use.",
                ],
            }
            panel_path = partial / "bola-panel.json"
            _write_json(panel_path, panel)
            shutil.rmtree(temporary_root, ignore_errors=True)

            artifacts = {
                path.relative_to(partial).as_posix(): sha256_file(path)
                for path in sorted(partial.rglob("*"))
                if path.is_file()
            }
            counts_by_class = {
                mhc_class: sum(item["mhc_class"] == mhc_class for item in observations)
                for mhc_class in ("I", "II")
            }
            supported_by_class = {
                mhc_class: sum(
                    item["mhc_class"] == mhc_class and item["status"] == "supported"
                    for item in observations
                )
                for mhc_class in ("I", "II")
            }
            manifest = {
                **identity_payload,
                "identity": identity,
                "source_run": str(source),
                "source_run_id": _load_json(source / "manifest.json")["run_id"],
                "record_map": {
                    record_id: candidate["candidate_id"]
                    for record_id, candidate in record_map.items()
                },
                "summary": {
                    "candidate_count": len(record_map),
                    "observation_count": len(observations),
                    "observation_count_by_class": counts_by_class,
                    "supported_count_by_class": supported_by_class,
                    "population_claim_allowed": False,
                },
                "artifacts": artifacts,
            }
            _write_json(partial / "manifest.json", manifest)
            partial.rename(output_dir)
        except Exception as error:
            failed = _preserve_failed_output(
                partial,
                failed,
                identity=identity,
                error=error,
            )
            raise ValueError(
                f"{error}\nFailed adapter artifacts preserved at: {failed}"
            ) from error

    evidence_path = output_dir / "mhc_binding.json"
    panel_path = output_dir / "bola-panel.json"
    candidate_by_id = {
        candidate["candidate_id"]: candidate for candidate in candidate_batch["candidates"]
    }
    load_residue_evidence(
        evidence_path,
        adapter_id="mhc_binding",
        candidate_by_id=candidate_by_id,
        candidate_batch_sha256=candidate_batch_sha256,
    )
    specification_path = _update_immune_specification(
        config,
        panel_path=panel_path,
        evidence_path=evidence_path,
    )
    return {
        "identity": identity,
        "output_dir": str(output_dir),
        "manifest": str(output_dir / "manifest.json"),
        "panel": str(panel_path),
        "evidence": str(evidence_path),
        "immune_specification": str(specification_path),
        "candidate_count": manifest["summary"]["candidate_count"],
        "observation_count": manifest["summary"]["observation_count"],
        "supported_count_by_class": manifest["summary"]["supported_count_by_class"],
    }
