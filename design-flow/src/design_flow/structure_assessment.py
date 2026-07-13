"""Import and deterministically assess a Stage 3 ESMFold2 result archive."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import shutil
import tarfile
from typing import Any

from .config import ProjectConfig, load_project_config
from .structure_job import (
    BIOHUB_TRANSFORMERS_COMMIT,
    CANDIDATE_STAGE_ID,
    ESMC_6B_REVISION,
    ESMFOLD2_FAST_REVISION,
    JOB_SCHEMA,
    _document_sha256,
    _fasta_bytes,
    _identity,
    _load_json,
    _resolve_stage2_run,
)
from .structure_metrics import (
    ParsedStructure,
    add_source_geometry_comparisons,
    assess_candidate_structure,
    parse_ca_pdb,
)
from .verification import ARTIFACT_INDEX_FILENAME, sha256_file


RUN_SCHEMA = "vaxflow.esmfold2-run.v1"
RESULT_SCHEMA = "vaxflow.esmfold2-result.v1"
SUMMARY_SCHEMA = "vaxflow.esmfold2-summary.v1"
STRUCTURE_STAGE_ID = "protein_structure_assessment"


@dataclass
class StructureAssessmentAnalysis:
    config: ProjectConfig
    source_run_dir: Path
    source_manifest: dict[str, Any]
    source_candidate_batch: dict[str, Any]
    job_dir: Path
    job_manifest: dict[str, Any]
    result_archive: Path
    result_archive_sha256: str
    result_dir: Path
    result_run_manifest: dict[str, Any]
    result_summary: dict[str, Any]
    result_documents: dict[str, dict[str, Any]]
    pdb_paths: dict[str, Path]
    parsed_structures: dict[str, ParsedStructure]
    assessments: list[dict[str, Any]]
    findings: list[dict[str, Any]]

    @property
    def computational_status(self) -> str:
        return "pass"


def _read_tar_json(archive: Path, name: str) -> dict[str, Any]:
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            members = [member for member in bundle.getmembers() if member.name == name]
            if len(members) != 1 or not members[0].isfile():
                raise ValueError(f"Result archive must contain exactly one {name}")
            handle = bundle.extractfile(members[0])
            if handle is None:
                raise ValueError(f"Cannot read {name} from result archive")
            with handle:
                value = json.loads(handle.read().decode("utf-8"))
    except (OSError, tarfile.TarError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read {name} from {archive}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"Result archive {name} root must be an object")
    return value


def _expected_result_files(job: dict[str, Any]) -> set[str]:
    files = {"run-manifest.json", "summary.json"}
    for record in job["records"]:
        candidate_id = record["candidate_id"]
        files.add(f"records/{candidate_id}/result.json")
        files.add(f"records/{candidate_id}/prediction.pdb")
    return files


def _safe_extract_result_archive(
    archive: Path,
    destination: Path,
    expected_files: set[str],
) -> Path:
    if destination.exists():
        actual = {
            path.relative_to(destination).as_posix()
            for path in destination.rglob("*")
            if path.is_file()
        }
        if actual != expected_files or any(path.is_symlink() for path in destination.rglob("*")):
            raise ValueError("Existing Stage 3 import cache has unexpected files")
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    if temporary.exists():
        raise ValueError(f"Temporary Stage 3 import path already exists: {temporary}")
    temporary.mkdir()
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            members = bundle.getmembers()
            names = [member.name for member in members if member.isfile()]
            if len(names) != len(set(names)) or set(names) != expected_files:
                raise ValueError(
                    "Result archive file set differs from the checksum-bound job: "
                    f"expected={sorted(expected_files)} observed={sorted(names)}"
                )
            for member in members:
                member_path = Path(member.name)
                if (
                    member.issym()
                    or member.islnk()
                    or member.isdev()
                    or member_path.is_absolute()
                    or ".." in member_path.parts
                ):
                    raise ValueError(f"Unsafe result archive member: {member.name}")
                if member.isdir():
                    continue
                handle = bundle.extractfile(member)
                if handle is None:
                    raise ValueError(f"Cannot extract result member: {member.name}")
                target = temporary.joinpath(*member_path.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with handle, target.open("wb") as output:
                    shutil.copyfileobj(handle, output)
        os.replace(temporary, destination)
        return destination
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _validate_job_against_stage2(
    job_dir: Path,
    job: dict[str, Any],
    source_run: Path,
    source_manifest: dict[str, Any],
    candidate_batch: dict[str, Any],
) -> None:
    if job.get("schema_version") != JOB_SCHEMA:
        raise ValueError(f"Unexpected Stage 3 job schema: {job.get('schema_version')}")
    if job.get("job_identity") != _identity(job, "job_identity"):
        raise ValueError("Stage 3 job identity mismatch")
    expected_model = {
        "name": "ESMFold2-Fast",
        "source_revision": BIOHUB_TRANSFORMERS_COMMIT,
        "structure_revision": ESMFOLD2_FAST_REVISION,
        "language_model_revision": ESMC_6B_REVISION,
    }
    if job.get("model") != expected_model:
        raise ValueError("Stage 3 job model identity differs from the approved pin")
    source = job.get("source", {})
    batch_path = source_run / "nodes" / CANDIDATE_STAGE_ID / "candidate_batch.json"
    if source != {
        "project_id": source_manifest["project_id"],
        "stage2_run_id": source_manifest["run_id"],
        "stage2_artifact_index_sha256": sha256_file(
            source_run / ARTIFACT_INDEX_FILENAME
        ),
        "candidate_batch_sha256": sha256_file(batch_path),
    }:
        raise ValueError("Stage 3 job lineage differs from the verified Stage 2 run")
    selected = [
        candidate
        for candidate in candidate_batch["candidates"]
        if candidate["exploratory_structure_ready"] and candidate["duplicate_of"] is None
    ]
    expected_records = [
        {
            "candidate_id": candidate["candidate_id"],
            "candidate_key": candidate["candidate_key"],
            "display_name": candidate["display_name"],
            "candidate_type": candidate["candidate_type"],
            "sequence": candidate["amino_acid_sequence"],
            "sequence_sha256": candidate["amino_acid_sha256"],
            "length": len(candidate["amino_acid_sequence"]),
            "release_status": candidate["release_status"],
            "inferred_components": candidate["inferred_components"],
        }
        for candidate in selected
    ]
    if job.get("records") != expected_records:
        raise ValueError("Stage 3 job records differ from the verified candidate batch")
    fasta = _fasta_bytes(expected_records)
    expected_fasta = {
        "path": "sequences.fasta",
        "sha256": __import__("hashlib").sha256(fasta).hexdigest(),
        "bytes": len(fasta),
        "records": len(expected_records),
    }
    if job.get("fasta") != expected_fasta or (job_dir / "sequences.fasta").read_bytes() != fasta:
        raise ValueError("Stage 3 job FASTA differs from its records")


def _validate_run_manifest(job: dict[str, Any], manifest: dict[str, Any]) -> None:
    if (
        manifest.get("schema_version") != RUN_SCHEMA
        or manifest.get("run_identity") != _identity(manifest, "run_identity")
        or manifest.get("job_identity") != job["job_identity"]
        or manifest.get("job_manifest_sha256") != _document_sha256(job)
        or manifest.get("source") != job["source"]
        or manifest.get("model") != job["model"]
        or manifest.get("candidate_ids")
        != [record["candidate_id"] for record in job["records"]]
        or not isinstance(manifest.get("runtime_identity"), str)
        or not manifest["runtime_identity"]
    ):
        raise ValueError("ESMFold2 run manifest does not match the Stage 3 job")


def _validate_summary(job: dict[str, Any], manifest: dict[str, Any], summary: dict[str, Any]) -> None:
    expected_count = len(job["records"])
    records = summary.get("records", {})
    if (
        summary.get("schema_version") != SUMMARY_SCHEMA
        or summary.get("run_identity") != manifest["run_identity"]
        or summary.get("status") != "passed"
        or records
        != {
            "selected": expected_count,
            "succeeded": expected_count,
            "failed": 0,
            "pending": 0,
        }
    ):
        raise ValueError("ESMFold2 result summary is incomplete or inconsistent")


def _validate_result(
    record: dict[str, Any],
    manifest: dict[str, Any],
    result_path: Path,
    pdb_path: Path,
) -> dict[str, Any]:
    result = _load_json(result_path)
    artifact = result.get("artifact", {})
    expected_relative = f"records/{record['candidate_id']}/prediction.pdb"
    metrics = result.get("metrics", {})
    if (
        result.get("schema_version") != RESULT_SCHEMA
        or result.get("run_identity") != manifest["run_identity"]
        or result.get("candidate_id") != record["candidate_id"]
        or result.get("candidate_key") != record["candidate_key"]
        or result.get("sequence_sha256") != record["sequence_sha256"]
        or result.get("length") != record["length"]
        or result.get("status") != "succeeded"
        or artifact.get("path") != expected_relative
        or artifact.get("bytes") != pdb_path.stat().st_size
        or artifact.get("sha256") != sha256_file(pdb_path)
        or not isinstance(metrics, dict)
    ):
        raise ValueError(f"Invalid ESMFold2 result for {record['candidate_id']}")
    for metric in ("mean_plddt", "ptm"):
        value = metrics.get(metric)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
        ):
            raise ValueError(f"Invalid {metric} for {record['candidate_id']}")
    return result


def analyze_structure_results(
    project_config: str | Path,
    *,
    result_archive: str | Path,
    source_run_dir: str | Path | None = None,
    job_dir: str | Path | None = None,
) -> StructureAssessmentAnalysis:
    config = load_project_config(Path(project_config))
    source_run = _resolve_stage2_run(
        config,
        Path(source_run_dir) if source_run_dir is not None else None,
    )
    source_manifest = _load_json(source_run / "manifest.json")
    candidate_batch = _load_json(
        source_run / "nodes" / CANDIDATE_STAGE_ID / "candidate_batch.json"
    )
    archive = Path(result_archive).expanduser().resolve()
    if not archive.is_file():
        raise ValueError(f"Stage 3 result archive not found: {archive}")
    peek_manifest = _read_tar_json(archive, "run-manifest.json")
    job_identity = peek_manifest.get("job_identity")
    if not isinstance(job_identity, str) or not job_identity:
        raise ValueError("Result archive run manifest has no job identity")
    resolved_job_dir = (
        Path(job_dir).expanduser().resolve()
        if job_dir is not None
        else config.runtime_root / "transfer" / "stage3-esmfold2" / job_identity
    )
    job = _load_json(resolved_job_dir / "job-manifest.json")
    _validate_job_against_stage2(
        resolved_job_dir,
        job,
        source_run,
        source_manifest,
        candidate_batch,
    )
    archive_sha = sha256_file(archive)
    import_dir = (
        config.runtime_root
        / "imports"
        / "stage3-esmfold2"
        / archive_sha
    )
    result_dir = _safe_extract_result_archive(
        archive,
        import_dir,
        _expected_result_files(job),
    )
    run_manifest = _load_json(result_dir / "run-manifest.json")
    summary = _load_json(result_dir / "summary.json")
    _validate_run_manifest(job, run_manifest)
    _validate_summary(job, run_manifest, summary)

    candidate_by_id = {
        candidate["candidate_id"]: candidate
        for candidate in candidate_batch["candidates"]
    }
    result_documents: dict[str, dict[str, Any]] = {}
    pdb_paths: dict[str, Path] = {}
    parsed_structures: dict[str, ParsedStructure] = {}
    assessments = []
    for record in job["records"]:
        candidate_id = record["candidate_id"]
        record_dir = result_dir / "records" / candidate_id
        pdb_path = record_dir / "prediction.pdb"
        result = _validate_result(
            record,
            run_manifest,
            record_dir / "result.json",
            pdb_path,
        )
        parsed = parse_ca_pdb(pdb_path)
        candidate = candidate_by_id[candidate_id]
        assessment = assess_candidate_structure(candidate, parsed, result)
        assessment["pdb_sha256"] = sha256_file(pdb_path)
        assessment["pdb_bytes"] = pdb_path.stat().st_size
        assessment["runtime_seconds"] = round(float(result["runtime_seconds"]), 6)
        result_documents[candidate_id] = result
        pdb_paths[candidate_id] = pdb_path
        parsed_structures[candidate_id] = parsed
        assessments.append(assessment)
    selected_candidates = [candidate_by_id[record["candidate_id"]] for record in job["records"]]
    add_source_geometry_comparisons(
        selected_candidates,
        assessments,
        parsed_structures,
    )
    findings = []
    for assessment in assessments:
        for flag in assessment["review_flags"]:
            findings.append(
                {
                    "severity": "warning",
                    "code": flag["code"],
                    "candidate_id": assessment["candidate_id"],
                    "candidate_key": assessment["candidate_key"],
                    "component_index": flag.get("component_index"),
                    "message": (
                        "Deterministic exploratory structure rule requested review; "
                        "this is not an experimental failure classification."
                    ),
                }
            )
    return StructureAssessmentAnalysis(
        config=config,
        source_run_dir=source_run,
        source_manifest=source_manifest,
        source_candidate_batch=candidate_batch,
        job_dir=resolved_job_dir,
        job_manifest=job,
        result_archive=archive,
        result_archive_sha256=archive_sha,
        result_dir=result_dir,
        result_run_manifest=run_manifest,
        result_summary=summary,
        result_documents=result_documents,
        pdb_paths=pdb_paths,
        parsed_structures=parsed_structures,
        assessments=assessments,
        findings=findings,
    )
