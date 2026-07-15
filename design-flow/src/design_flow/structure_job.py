"""Prepare checksum-bound ESMFold2-Fast jobs from a verified Stage 2 run."""

from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
from typing import Any

from .config import ProjectConfig, load_project_config
from .verification import ARTIFACT_INDEX_FILENAME, sha256_file, verify_run


JOB_SCHEMA = "vaxflow.esmfold2-job.v1"
SELECTION_SCHEMA = "vaxflow.stage3-selection.v1"
CANDIDATE_STAGE_ID = "candidate_specification"
STRUCTURE_STAGE_ID = "protein_structure_assessment"
BIOHUB_TRANSFORMERS_COMMIT = "ef32577f55da19a4989cd7b22e004dc43a4998cb"
ESMFOLD2_FAST_REVISION = "b28d8ace5e05e61e5bec1e6820cfd3e221819d12"
ESMC_6B_REVISION = "45b0fa5d7fb06faefbd5e3b89bdcef35d564e79a"
DEFAULT_PARAMETERS = {
    "chunk_size": 64,
    "num_diffusion_samples": 1,
    "num_loops": 3,
    "num_sampling_steps": 50,
}
CANONICAL_AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError(f"value is not canonical JSON: {error}") from error


def _document_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _identity(document: dict[str, Any], identity_field: str) -> str:
    payload = dict(document)
    payload.pop(identity_field, None)
    payload.pop("created_at_utc", None)
    return _document_sha256(payload)


def _json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def _wrap_fasta(sequence: str, width: int = 80) -> str:
    return "\n".join(
        sequence[offset : offset + width]
        for offset in range(0, len(sequence), width)
    )


def _fasta_bytes(records: list[dict[str, Any]]) -> bytes:
    return "".join(
        f">{record['candidate_id']} key={record['candidate_key']} "
        f"length={record['length']}\n{_wrap_fasta(record['sequence'])}\n"
        for record in records
    ).encode("ascii")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read JSON from {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _resolve_stage2_run(config: ProjectConfig, source_run_dir: Path | None) -> Path:
    if source_run_dir is None:
        latest_path = config.run_root / "latest.json"
        latest = _load_json(latest_path)
        source_run_dir = Path(str(latest.get("run_path", "")))
    source = source_run_dir.expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f"Stage 2 run directory not found: {source}")
    verification = verify_run(source)
    if verification["status"] != "pass":
        raise ValueError(
            "Stage 2 run verification failed: "
            + "; ".join(verification["errors"][:5])
        )
    manifest = _load_json(source / "manifest.json")
    if manifest.get("project_id") != config.project_id:
        raise ValueError("Stage 2 run belongs to another project")
    if manifest.get("current_stage") != CANDIDATE_STAGE_ID:
        raise ValueError(
            f"Stage 3 preparation requires a {CANDIDATE_STAGE_ID} run, got "
            f"{manifest.get('current_stage')}"
        )
    return source


def _validate_selection(
    selection: dict[str, Any],
    candidate_batch: dict[str, Any],
    project_id: str,
) -> list[dict[str, Any]]:
    records = selection.get("records")
    budget = selection.get("budget")
    if (
        selection.get("schema_version") != SELECTION_SCHEMA
        or selection.get("project_id") != project_id
        or selection.get("design_round_id") != candidate_batch.get("design_round_id")
        or not isinstance(records, list)
        or not records
        or not isinstance(budget, int)
        or isinstance(budget, bool)
        or budget < len(records)
    ):
        raise ValueError("Stage 3 selection manifest is invalid or mismatched")
    expected_identity = _document_sha256(
        {
            "search_identity": selection.get("search_identity"),
            "records": records,
            "budget": budget,
        }
    )
    if selection.get("selection_id") != expected_identity:
        raise ValueError("Stage 3 selection identity mismatch")
    candidates = candidate_batch.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("Stage 2 candidate batch has no candidate array")
    by_key = {
        candidate.get("candidate_key"): candidate
        for candidate in candidates
        if isinstance(candidate, dict)
    }
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"Stage 3 selection record {index} must be an object")
        key = record.get("candidate_key")
        candidate = by_key.get(key)
        if not isinstance(key, str) or candidate is None or key in seen:
            raise ValueError(f"Stage 3 selection record {index} has an invalid candidate key")
        seen.add(key)
        sequence = candidate.get("amino_acid_sequence")
        if (
            not isinstance(sequence, str)
            or record.get("amino_acid_sha256") != candidate.get("amino_acid_sha256")
            or record.get("aa_length") != len(sequence)
            or not candidate.get("exploratory_structure_ready")
            or candidate.get("duplicate_of") is not None
        ):
            raise ValueError(f"Stage 3 selection record {key} differs from candidate batch")
        selected.append(candidate)
    return selected


def build_structure_job(
    project_config: str | Path,
    *,
    source_run_dir: str | Path | None = None,
    created_at: datetime | None = None,
    maximum_sequence_length: int = 1024,
    selection_manifest: str | Path | None = None,
) -> tuple[ProjectConfig, Path, dict[str, Any], bytes]:
    if maximum_sequence_length < 1:
        raise ValueError("maximum_sequence_length must be positive")
    config = load_project_config(Path(project_config))
    source = _resolve_stage2_run(
        config,
        Path(source_run_dir) if source_run_dir is not None else None,
    )
    node_dir = source / "nodes" / CANDIDATE_STAGE_ID
    manifest = _load_json(source / "manifest.json")
    batch_path = node_dir / "candidate_batch.json"
    handoff_path = node_dir / "handoff.json"
    model_inputs_path = node_dir / "model_inputs.json"
    batch = _load_json(batch_path)
    handoff = _load_json(handoff_path)
    model_inputs = _load_json(model_inputs_path)
    requested_ids = model_inputs.get("models", {}).get("ESMFold2", {}).get("candidate_ids")
    handoff_ids = handoff.get("carried_forward", {}).get(
        "exploratory_structure_candidate_ids"
    )
    if not isinstance(requested_ids, list) or requested_ids != handoff_ids or not requested_ids:
        raise ValueError("Stage 2 ESMFold2 candidate IDs and handoff are inconsistent")
    if len(requested_ids) != len(set(requested_ids)):
        raise ValueError("Stage 2 ESMFold2 candidate IDs contain duplicates")

    candidates = batch.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("Stage 2 candidate batch has no candidate array")
    by_id = {
        candidate.get("candidate_id"): candidate
        for candidate in candidates
        if isinstance(candidate, dict)
    }
    if set(requested_ids) - set(by_id):
        raise ValueError("Stage 2 model input references an unknown candidate")
    selected_candidates = [by_id[candidate_id] for candidate_id in requested_ids]
    selection_descriptor = None
    if selection_manifest is not None:
        selection_path = Path(selection_manifest).expanduser().resolve()
        selection = _load_json(selection_path)
        selected_candidates = _validate_selection(
            selection,
            batch,
            config.project_id,
        )
        requested_set = set(requested_ids)
        if any(
            candidate["candidate_id"] not in requested_set
            for candidate in selected_candidates
        ):
            raise ValueError("Stage 3 selection includes a candidate outside Stage 2 model inputs")
        selection_descriptor = {
            "schema_version": SELECTION_SCHEMA,
            "selection_id": selection["selection_id"],
            "search_identity": selection["search_identity"],
            "sha256": sha256_file(selection_path),
            "records": len(selection["records"]),
        }

    records: list[dict[str, Any]] = []
    for candidate in selected_candidates:
        candidate_id = candidate["candidate_id"]
        sequence = candidate.get("amino_acid_sequence")
        if (
            not isinstance(sequence, str)
            or not sequence
            or not set(sequence) <= CANONICAL_AMINO_ACIDS
        ):
            raise ValueError(f"Candidate {candidate_id} has a non-canonical AA sequence")
        if len(sequence) > maximum_sequence_length:
            raise ValueError(
                f"Candidate {candidate_id} length {len(sequence)} exceeds "
                f"ESMFold2 limit {maximum_sequence_length}"
            )
        observed_hash = hashlib.sha256(sequence.encode("ascii")).hexdigest()
        if candidate.get("amino_acid_sha256") != observed_hash:
            raise ValueError(f"Candidate {candidate_id} AA SHA256 mismatch")
        if not candidate.get("exploratory_structure_ready"):
            raise ValueError(f"Candidate {candidate_id} is not exploratory-structure ready")
        if candidate.get("duplicate_of") is not None:
            raise ValueError(f"Candidate {candidate_id} is a duplicate model input")
        records.append(
            {
                "candidate_id": candidate_id,
                "candidate_key": candidate["candidate_key"],
                "display_name": candidate["display_name"],
                "candidate_type": candidate["candidate_type"],
                "sequence": sequence,
                "sequence_sha256": observed_hash,
                "length": len(sequence),
                "release_status": candidate["release_status"],
                "inferred_components": candidate["inferred_components"],
            }
        )

    created = created_at or datetime.now(timezone.utc)
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    fasta = _fasta_bytes(records)
    job: dict[str, Any] = {
        "schema_version": JOB_SCHEMA,
        "job_identity": "pending",
        "created_at_utc": created.astimezone(timezone.utc).isoformat(),
        "source": {
            "project_id": config.project_id,
            "stage2_run_id": manifest["run_id"],
            "stage2_artifact_index_sha256": sha256_file(
                source / ARTIFACT_INDEX_FILENAME
            ),
            "candidate_batch_sha256": sha256_file(batch_path),
        },
        "model": {
            "name": "ESMFold2-Fast",
            "source_revision": BIOHUB_TRANSFORMERS_COMMIT,
            "structure_revision": ESMFOLD2_FAST_REVISION,
            "language_model_revision": ESMC_6B_REVISION,
        },
        "execution": {"seed": 42, "parameters": dict(DEFAULT_PARAMETERS)},
        "maximum_sequence_length": maximum_sequence_length,
        "records": records,
        "fasta": {
            "path": "sequences.fasta",
            "sha256": hashlib.sha256(fasta).hexdigest(),
            "bytes": len(fasta),
            "records": len(records),
        },
    }
    if selection_descriptor is not None:
        job["selection"] = selection_descriptor
    job["job_identity"] = _identity(job, "job_identity")
    return config, source, job, fasta


def _validate_existing_job(
    directory: Path,
    job: dict[str, Any],
    fasta: bytes,
    selection_bytes: bytes | None,
) -> None:
    expected_files = {"job-manifest.json", "sequences.fasta"}
    if selection_bytes is not None:
        expected_files.add("selection.json")
    actual_files = {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file()
    }
    if actual_files != expected_files:
        raise ValueError(f"Existing Stage 3 job has unexpected files: {sorted(actual_files)}")
    existing = _load_json(directory / "job-manifest.json")
    if (
        existing.get("job_identity") != job["job_identity"]
        or existing.get("job_identity") != _identity(existing, "job_identity")
    ):
        raise ValueError("Existing Stage 3 job manifest differs from requested job")
    if (directory / "sequences.fasta").read_bytes() != fasta:
        raise ValueError("Existing Stage 3 job FASTA differs from requested job")
    if (
        selection_bytes is not None
        and (directory / "selection.json").read_bytes() != selection_bytes
    ):
        raise ValueError("Existing Stage 3 job selection differs from requested selection")


def _write_deterministic_archive(
    archive_path: Path,
    job_dir: Path,
    names: list[str],
) -> None:
    temporary_archive = archive_path.with_name(
        f".{archive_path.name}.tmp-{os.getpid()}"
    )
    try:
        with temporary_archive.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
                with tarfile.open(
                    fileobj=compressed,
                    mode="w",
                    format=tarfile.PAX_FORMAT,
                ) as archive:
                    for name in names:
                        content = (job_dir / name).read_bytes()
                        member = tarfile.TarInfo(name)
                        member.size = len(content)
                        member.mode = 0o644
                        member.mtime = 0
                        member.uid = 0
                        member.gid = 0
                        member.uname = ""
                        member.gname = ""
                        archive.addfile(member, BytesIO(content))
        os.replace(temporary_archive, archive_path)
    finally:
        temporary_archive.unlink(missing_ok=True)


def write_structure_job(
    project_config: str | Path,
    *,
    source_run_dir: str | Path | None = None,
    output_root: str | Path | None = None,
    created_at: datetime | None = None,
    maximum_sequence_length: int = 1024,
    selection_manifest: str | Path | None = None,
) -> dict[str, Any]:
    config, source, job, fasta = build_structure_job(
        project_config,
        source_run_dir=source_run_dir,
        created_at=created_at,
        maximum_sequence_length=maximum_sequence_length,
        selection_manifest=selection_manifest,
    )
    selection_bytes = None
    if selection_manifest is not None:
        selection_bytes = Path(selection_manifest).expanduser().resolve().read_bytes()
        if hashlib.sha256(selection_bytes).hexdigest() != job["selection"]["sha256"]:
            raise ValueError("Stage 3 selection changed while the job was being prepared")
    root = (
        Path(output_root).expanduser().resolve()
        if output_root is not None
        else config.runtime_root / "transfer" / "stage3-esmfold2"
    )
    root.mkdir(parents=True, exist_ok=True)
    job_dir = root / job["job_identity"]
    archive_path = root / f"{job['job_identity']}.tar.gz"
    if job_dir.exists():
        _validate_existing_job(job_dir, job, fasta, selection_bytes)
    else:
        temporary = Path(tempfile.mkdtemp(prefix=f".{job['job_identity']}.", dir=root))
        try:
            (temporary / "job-manifest.json").write_text(
                _json_text(job), encoding="utf-8"
            )
            (temporary / "sequences.fasta").write_bytes(fasta)
            if selection_bytes is not None:
                (temporary / "selection.json").write_bytes(selection_bytes)
            os.replace(temporary, job_dir)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
    names = ["job-manifest.json", "sequences.fasta"]
    if selection_bytes is not None:
        names.append("selection.json")
    _write_deterministic_archive(archive_path, job_dir, names)
    return {
        "schema_version": 1,
        "stage_id": STRUCTURE_STAGE_ID,
        "mode": "exploratory",
        "project_id": config.project_id,
        "source_run_id": source.name,
        "job_identity": job["job_identity"],
        "records": len(job["records"]),
        "minimum_length": min(record["length"] for record in job["records"]),
        "maximum_length": max(record["length"] for record in job["records"]),
        "job_dir": str(job_dir),
        "job_manifest": str(job_dir / "job-manifest.json"),
        "job_manifest_sha256": sha256_file(job_dir / "job-manifest.json"),
        "fasta": str(job_dir / "sequences.fasta"),
        "archive": str(archive_path),
        "archive_bytes": archive_path.stat().st_size,
        "archive_sha256": sha256_file(archive_path),
    }
