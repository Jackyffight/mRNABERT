"""Checksum-bound Evo 2 scoring jobs for Stage 6 mRNA designs."""

from __future__ import annotations

from io import BytesIO
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tarfile
import tempfile
from typing import Any

from .config import load_project_config
from .product_specs import MRNA_SPEC_RELATIVE, _atomic_json
from .stage6_routing import archive_runtime_file
from .verification import ARTIFACT_INDEX_FILENAME, sha256_file, verify_run


EVO2_JOB_SCHEMA = "vaxflow.evo2-sequence-score-job.v1"
EVO2_RESULT_SCHEMA = "vaxflow.evo2-sequence-score-result.v1"
MRNA_EVIDENCE_SCHEMA = "vaxflow.mrna-evidence.v1"
EVO2_ADAPTER_ID = "evo2_sequence_score"
EVO2_MODEL_NAME = "evo2_7b"
EVO2_MODEL_REVISION = "bda0089f92582d5baabf0f22d9fc85f3588f6b58"
EVO2_MODEL_SIZE = 13_766_621_200
EVO2_MODEL_SHA256 = "c66645929dc1b9c631f5be656da8726f38946315dc9167000a615dd626fcecf4"
EVO2_PACKAGE_VERSION = "0.6.0"
SCORING_PROTOCOL = "evo2-next-token-mean-log-likelihood-v1"
JOB_FILES = {"job-manifest.json", "sequences.fasta"}
RESULT_FILES = JOB_FILES | {"evo2-evidence.json", "run-manifest.json"}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
MAXIMUM_SEQUENCE_LENGTH = 1_000_000


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot load JSON document {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"JSON document root must be an object: {path}")
    return value


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode(
        "utf-8"
    )


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _document_identity(document: dict[str, Any], identity_field: str) -> str:
    return _canonical_sha256(
        {key: value for key, value in document.items() if key != identity_field}
    )


def _require_sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _fasta_bytes(records: list[dict[str, Any]]) -> bytes:
    lines: list[str] = []
    for record in records:
        lines.append(f">{record['design_id']}")
        sequence = record["coding_sequence_dna"]
        lines.extend(sequence[offset : offset + 80] for offset in range(0, len(sequence), 80))
    return ("\n".join(lines) + "\n").encode("ascii")


def _parse_fasta_bytes(content: bytes) -> dict[str, str]:
    try:
        text = content.decode("ascii")
    except UnicodeDecodeError as error:
        raise ValueError("Evo 2 FASTA must be ASCII") from error
    records: dict[str, str] = {}
    record_id: str | None = None
    parts: list[str] = []

    def finish() -> None:
        nonlocal record_id, parts
        if record_id is None:
            return
        sequence = "".join(parts).upper()
        if not sequence:
            raise ValueError(f"Evo 2 FASTA record is empty: {record_id}")
        records[record_id] = sequence
        record_id = None
        parts = []

    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith(">"):
            finish()
            header = line[1:].strip()
            candidate = header.split(maxsplit=1)[0] if header else ""
            if not candidate or candidate in records:
                raise ValueError(f"Invalid Evo 2 FASTA header at line {line_number}")
            record_id = candidate
        else:
            if record_id is None:
                raise ValueError(f"Sequence before Evo 2 FASTA header at line {line_number}")
            parts.append("".join(line.split()))
    finish()
    if not records:
        raise ValueError("Evo 2 FASTA contains no records")
    return records


def _write_deterministic_archive(path: Path, files: dict[str, bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as bundle:
                    for name in sorted(files):
                        content = files[name]
                        member = tarfile.TarInfo(name)
                        member.size = len(content)
                        member.mode = 0o644
                        member.mtime = 0
                        member.uid = 0
                        member.gid = 0
                        member.uname = ""
                        member.gname = ""
                        bundle.addfile(member, BytesIO(content))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_exact_archive(
    archive_path: Path,
    expected_files: set[str],
    *,
    maximum_total_bytes: int = 32 * 1024 * 1024,
) -> dict[str, bytes]:
    archive_path = archive_path.expanduser().resolve()
    if not archive_path.is_file():
        raise ValueError(f"Evo 2 archive not found: {archive_path}")
    try:
        with tarfile.open(archive_path, "r:gz") as bundle:
            members = bundle.getmembers()
            names = [member.name for member in members if member.isfile()]
            if len(names) != len(set(names)) or set(names) != expected_files:
                raise ValueError(
                    "Evo 2 archive file set mismatch: "
                    f"expected={sorted(expected_files)} observed={sorted(names)}"
                )
            result: dict[str, bytes] = {}
            total = 0
            for member in members:
                member_path = Path(member.name)
                if (
                    not member.isfile()
                    or member.issym()
                    or member.islnk()
                    or member.isdev()
                    or member_path.is_absolute()
                    or ".." in member_path.parts
                ):
                    raise ValueError(f"Unsafe Evo 2 archive member: {member.name}")
                total += member.size
                if total > maximum_total_bytes:
                    raise ValueError("Evo 2 archive exceeds the allowed uncompressed size")
                handle = bundle.extractfile(member)
                if handle is None:
                    raise ValueError(f"Cannot read Evo 2 archive member: {member.name}")
                with handle:
                    result[member.name] = handle.read()
            return result
    except (OSError, tarfile.TarError) as error:
        raise ValueError(f"Cannot read Evo 2 archive {archive_path}: {error}") from error


def _write_exact_directory(directory: Path, files: dict[str, bytes]) -> None:
    if directory.exists():
        actual = {
            path.relative_to(directory).as_posix()
            for path in directory.rglob("*")
            if path.is_file()
        }
        if actual != set(files) or any(path.is_symlink() for path in directory.rglob("*")):
            raise ValueError(f"Existing Evo 2 artifact directory has unexpected files: {directory}")
        for name, content in files.items():
            if (directory / name).read_bytes() != content:
                raise ValueError(f"Existing Evo 2 artifact differs: {directory / name}")
        return
    directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{directory.name}.", dir=directory.parent))
    try:
        for name, content in files.items():
            target = temporary / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        os.replace(temporary, directory)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def build_evo2_job_documents(
    *,
    project_id: str,
    stage6_manifest: dict[str, Any],
    stage6_artifact_index_sha256: str,
    mrna_products: dict[str, Any],
    mrna_products_sha256: str,
    followup_manifest: dict[str, Any],
    followup_manifest_sha256: str,
) -> tuple[dict[str, Any], bytes]:
    if (
        stage6_manifest.get("project_id") != project_id
        or stage6_manifest.get("current_stage") != "mrna_product_design"
        or stage6_manifest.get("executed_stages")
        != ["protein_product_design", "mrna_product_design"]
    ):
        raise ValueError("Evo 2 preparation requires a combined Stage 6 run")
    if (
        mrna_products.get("schema_version") != 1
        or mrna_products.get("stage_id") != "mrna_product_design"
    ):
        raise ValueError("Stage 6 mRNA product document is invalid")
    if (
        followup_manifest.get("schema_version") != "vaxflow.stage6-model-followup.v1"
        or followup_manifest.get("modality") != "mrna"
    ):
        raise ValueError("Stage 6 mRNA follow-up manifest is invalid")
    routing_id = mrna_products.get("routing", {}).get("routing_id")
    if not isinstance(routing_id, str) or followup_manifest.get("routing_id") != routing_id:
        raise ValueError("Stage 6 mRNA routing identities differ")

    designs = mrna_products.get("designs")
    followup_records = followup_manifest.get("records")
    if not isinstance(designs, list) or not isinstance(followup_records, list) or not followup_records:
        raise ValueError("Stage 6 has no mRNA designs eligible for Evo 2 follow-up")
    design_by_id = {
        design.get("design_id"): design
        for design in designs
        if isinstance(design, dict) and isinstance(design.get("design_id"), str)
    }
    if len(design_by_id) != len(designs):
        raise ValueError("Stage 6 mRNA design IDs are missing or duplicated")

    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for followup in followup_records:
        if not isinstance(followup, dict):
            raise ValueError("Stage 6 mRNA follow-up record must be an object")
        design_id = followup.get("design_id")
        design = design_by_id.get(design_id)
        if design is None or design_id in seen:
            raise ValueError(f"Unknown or duplicate Evo 2 follow-up design: {design_id}")
        sequence = design.get("coding_sequence_dna")
        sequence_sha256 = design.get("coding_sequence_sha256")
        if (
            not isinstance(sequence, str)
            or not sequence
            or set(sequence) - set("ACGT")
            or _require_sha256(sequence_sha256, "coding_sequence_sha256")
            != hashlib.sha256(sequence.encode("ascii")).hexdigest()
            or followup.get("coding_sequence_sha256") != sequence_sha256
            or followup.get("candidate_id") != design.get("candidate_id")
            or followup.get("routing_lane") != design.get("routing_lane")
            or design.get("translation_verified") is not True
            or design.get("expensive_followup_eligible") is not True
        ):
            raise ValueError(f"Evo 2 follow-up identity mismatch: {design_id}")
        records.append(
            {
                "design_id": design_id,
                "candidate_id": design["candidate_id"],
                "design_type": design["design_type"],
                "routing_lane": design["routing_lane"],
                "coding_sequence_dna": sequence,
                "coding_sequence_sha256": sequence_sha256,
                "sequence_length_nt": len(sequence),
            }
        )
        seen.add(design_id)

    fasta = _fasta_bytes(records)
    batch_sha256 = _require_sha256(
        mrna_products.get("mrna_design_batch_sha256"), "mrna_design_batch_sha256"
    )
    job: dict[str, Any] = {
        "schema_version": EVO2_JOB_SCHEMA,
        "job_identity": "pending",
        "source": {
            "project_id": project_id,
            "stage6_run_id": stage6_manifest["run_id"],
            "stage5_run_id": stage6_manifest.get("lineage", {}).get("parent_run_id"),
            "stage6_artifact_index_sha256": _require_sha256(
                stage6_artifact_index_sha256, "stage6_artifact_index_sha256"
            ),
            "mrna_products_sha256": _require_sha256(
                mrna_products_sha256, "mrna_products_sha256"
            ),
            "model_followup_manifest_sha256": _require_sha256(
                followup_manifest_sha256, "model_followup_manifest_sha256"
            ),
            "mrna_design_batch_sha256": batch_sha256,
            "routing_id": routing_id,
        },
        "model": {
            "name": EVO2_MODEL_NAME,
            "revision": EVO2_MODEL_REVISION,
            "package_version": EVO2_PACKAGE_VERSION,
            "checkpoint_size_bytes": EVO2_MODEL_SIZE,
            "checkpoint_sha256": EVO2_MODEL_SHA256,
        },
        "scoring": {
            "protocol": SCORING_PROTOCOL,
            "sequence": "coding_sequence_dna_5_to_3",
            "aggregation": "mean_log_likelihood_over_L_minus_1_next_tokens",
            "direction": "higher_is_more_likely",
            "reverse_complement": False,
        },
        "records": records,
        "fasta": {
            "path": "sequences.fasta",
            "sha256": hashlib.sha256(fasta).hexdigest(),
            "bytes": len(fasta),
            "records": len(records),
        },
    }
    job["job_identity"] = _document_identity(job, "job_identity")
    validate_evo2_job_documents(job, fasta)
    return job, fasta


def validate_evo2_job_documents(job: dict[str, Any], fasta: bytes) -> None:
    if job.get("schema_version") != EVO2_JOB_SCHEMA:
        raise ValueError("Unsupported Evo 2 job schema")
    identity = _require_sha256(job.get("job_identity"), "job_identity")
    if identity != _document_identity(job, "job_identity"):
        raise ValueError("Evo 2 job identity mismatch")
    model = job.get("model", {})
    if model != {
        "name": EVO2_MODEL_NAME,
        "revision": EVO2_MODEL_REVISION,
        "package_version": EVO2_PACKAGE_VERSION,
        "checkpoint_size_bytes": EVO2_MODEL_SIZE,
        "checkpoint_sha256": EVO2_MODEL_SHA256,
    }:
        raise ValueError("Evo 2 job model pin differs from the supported checkpoint")
    scoring = job.get("scoring", {})
    if (
        scoring.get("protocol") != SCORING_PROTOCOL
        or scoring.get("direction") != "higher_is_more_likely"
        or scoring.get("reverse_complement") is not False
    ):
        raise ValueError("Evo 2 scoring protocol is invalid")
    records = job.get("records")
    if not isinstance(records, list) or not records or len(records) > 10_000:
        raise ValueError("Evo 2 job has no records")
    ids: set[str] = set()
    fasta_records = _parse_fasta_bytes(fasta)
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Evo 2 job record must be an object")
        design_id = record.get("design_id")
        sequence = record.get("coding_sequence_dna")
        if (
            not isinstance(design_id, str)
            or IDENTIFIER_PATTERN.fullmatch(design_id) is None
            or design_id in ids
            or not isinstance(sequence, str)
            or not sequence
            or set(sequence) - set("ACGT")
            or len(sequence) > MAXIMUM_SEQUENCE_LENGTH
            or record.get("sequence_length_nt") != len(sequence)
            or _require_sha256(
                record.get("coding_sequence_sha256"), "coding_sequence_sha256"
            )
            != hashlib.sha256(sequence.encode("ascii")).hexdigest()
            or fasta_records.get(design_id) != sequence
        ):
            raise ValueError(f"Invalid Evo 2 job record: {design_id}")
        ids.add(design_id)
    if set(fasta_records) != ids:
        raise ValueError("Evo 2 job FASTA record set differs from the manifest")
    fasta_descriptor = job.get("fasta", {})
    if fasta_descriptor != {
        "path": "sequences.fasta",
        "sha256": hashlib.sha256(fasta).hexdigest(),
        "bytes": len(fasta),
        "records": len(records),
    }:
        raise ValueError("Evo 2 job FASTA descriptor mismatch")
    source = job.get("source", {})
    for field in ("project_id", "stage6_run_id", "stage5_run_id"):
        if (
            not isinstance(source.get(field), str)
            or IDENTIFIER_PATTERN.fullmatch(source[field]) is None
        ):
            raise ValueError(f"source.{field} must be a non-empty string")
    for field in (
        "stage6_artifact_index_sha256",
        "mrna_products_sha256",
        "model_followup_manifest_sha256",
        "mrna_design_batch_sha256",
        "routing_id",
    ):
        _require_sha256(source.get(field), f"source.{field}")


def prepare_evo2_job(
    project_config: str | Path,
    *,
    source_run_dir: str | Path,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    config = load_project_config(Path(project_config))
    source = Path(source_run_dir).expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f"Stage 6 run directory not found: {source}")
    verification = verify_run(source)
    if verification["status"] != "pass":
        raise ValueError("Stage 6 verification failed: " + "; ".join(verification["errors"][:5]))
    stage6_manifest_path = source / "manifest.json"
    artifact_index_path = source / ARTIFACT_INDEX_FILENAME
    mrna_node = source / "nodes" / "mrna_product_design"
    mrna_products_path = mrna_node / "mrna_products.json"
    followup_path = mrna_node / "model_followup_manifest.json"
    stage6_manifest = _load_json(stage6_manifest_path)
    mrna_products = _load_json(mrna_products_path)
    followup = _load_json(followup_path)
    job, fasta = build_evo2_job_documents(
        project_id=config.project_id,
        stage6_manifest=stage6_manifest,
        stage6_artifact_index_sha256=sha256_file(artifact_index_path),
        mrna_products=mrna_products,
        mrna_products_sha256=sha256_file(mrna_products_path),
        followup_manifest=followup,
        followup_manifest_sha256=sha256_file(followup_path),
    )
    root = (
        Path(output_root).expanduser().resolve()
        if output_root is not None
        else config.runtime_root / "transfer" / "stage6-evo2"
    )
    files = {
        "job-manifest.json": _json_bytes(job),
        "sequences.fasta": fasta,
    }
    job_dir = root / job["job_identity"]
    archive_path = root / f"{job['job_identity']}.tar.gz"
    _write_exact_directory(job_dir, files)
    _write_deterministic_archive(archive_path, files)
    return {
        "job_identity": job["job_identity"],
        "records": len(job["records"]),
        "job_dir": str(job_dir),
        "archive": str(archive_path),
        "archive_sha256": sha256_file(archive_path),
        "mrna_design_batch_sha256": job["source"]["mrna_design_batch_sha256"],
    }


def load_evo2_job_archive(archive_path: str | Path) -> tuple[dict[str, Any], bytes]:
    files = _read_exact_archive(Path(archive_path), JOB_FILES)
    try:
        job = json.loads(files["job-manifest.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Invalid Evo 2 job manifest: {error}") from error
    if not isinstance(job, dict):
        raise ValueError("Evo 2 job manifest root must be an object")
    fasta = files["sequences.fasta"]
    validate_evo2_job_documents(job, fasta)
    return job, fasta


def _evidence_id(job: dict[str, Any], record: dict[str, Any]) -> str:
    identity = _canonical_sha256(
        {
            "job_identity": job["job_identity"],
            "design_id": record["design_id"],
            "coding_sequence_sha256": record["coding_sequence_sha256"],
            "model_revision": job["model"]["revision"],
            "protocol": job["scoring"]["protocol"],
        }
    )
    return f"evo2-{identity[:20]}"


def build_evo2_evidence(
    job: dict[str, Any],
    scores: list[dict[str, Any]],
    *,
    tool_version: str,
) -> dict[str, Any]:
    if tool_version != job["model"]["package_version"]:
        raise ValueError("Evo 2 tool version differs from the checksum-bound job")
    score_by_id: dict[str, dict[str, Any]] = {}
    for score in scores:
        if not isinstance(score, dict) or not isinstance(score.get("design_id"), str):
            raise ValueError("Evo 2 score record is invalid")
        design_id = score["design_id"]
        if design_id in score_by_id:
            raise ValueError(f"Duplicate Evo 2 score: {design_id}")
        for field in ("mean_log_likelihood", "total_log_likelihood", "perplexity"):
            value = score.get(field)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
            ):
                raise ValueError(f"Evo 2 score {design_id} has invalid {field}")
        score_by_id[design_id] = score
    expected_ids = {record["design_id"] for record in job["records"]}
    if set(score_by_id) != expected_ids:
        raise ValueError("Evo 2 score set differs from the checksum-bound job")

    observations = []
    for record in job["records"]:
        score = score_by_id[record["design_id"]]
        if (
            score.get("coding_sequence_sha256") != record["coding_sequence_sha256"]
            or score.get("sequence_length_nt") != record["sequence_length_nt"]
            or score.get("predicted_token_count") != record["sequence_length_nt"] - 1
        ):
            raise ValueError(f"Evo 2 score identity mismatch: {record['design_id']}")
        observations.append(
            {
                "evidence_id": _evidence_id(job, record),
                "design_id": record["design_id"],
                "candidate_id": record["candidate_id"],
                "status": "context",
                "score": float(score["mean_log_likelihood"]),
                "score_semantics": "higher_is_more_likely_under_pinned_evo2",
                "coding_sequence_sha256": record["coding_sequence_sha256"],
                "sequence_length_nt": record["sequence_length_nt"],
                "predicted_token_count": score["predicted_token_count"],
                "total_log_likelihood": float(score["total_log_likelihood"]),
                "perplexity": float(score["perplexity"]),
            }
        )
    return {
        "schema_version": MRNA_EVIDENCE_SCHEMA,
        "adapter_id": EVO2_ADAPTER_ID,
        "mrna_design_batch_sha256": job["source"]["mrna_design_batch_sha256"],
        "tool": {
            "name": "Evo2",
            "version": tool_version,
            "revision": job["model"]["revision"],
        },
        "protocol": job["scoring"],
        "observations": observations,
        "limitations": [
            "Zero-shot DNA likelihood is context evidence, not expression or efficacy evidence.",
            "Cross-protein likelihoods are not treated as a calibrated biological ranking.",
            "Coding sequences are scored without UTR, cap, poly(A), delivery, or host-cell context.",
        ],
    }


def validate_evo2_result_documents(
    job: dict[str, Any],
    fasta: bytes,
    evidence: dict[str, Any],
    run_manifest: dict[str, Any],
) -> None:
    validate_evo2_job_documents(job, fasta)
    if (
        evidence.get("schema_version") != MRNA_EVIDENCE_SCHEMA
        or evidence.get("adapter_id") != EVO2_ADAPTER_ID
        or evidence.get("mrna_design_batch_sha256")
        != job["source"]["mrna_design_batch_sha256"]
    ):
        raise ValueError("Evo 2 evidence binding mismatch")
    tool = evidence.get("tool", {})
    if (
        tool.get("name") != "Evo2"
        or tool.get("version") != job["model"]["package_version"]
        or tool.get("revision") != job["model"]["revision"]
        or evidence.get("protocol") != job["scoring"]
    ):
        raise ValueError("Evo 2 evidence tool pin mismatch")
    observations = evidence.get("observations")
    if not isinstance(observations, list):
        raise ValueError("Evo 2 evidence observations must be an array")
    expected = {record["design_id"]: record for record in job["records"]}
    observed_ids: set[str] = set()
    for observation in observations:
        if not isinstance(observation, dict):
            raise ValueError("Evo 2 evidence observation must be an object")
        design_id = observation.get("design_id")
        record = expected.get(design_id)
        value = observation.get("score")
        total = observation.get("total_log_likelihood")
        perplexity = observation.get("perplexity")
        predicted_count = observation.get("predicted_token_count")
        numeric_values = (value, total, perplexity)
        if (
            record is None
            or design_id in observed_ids
            or observation.get("evidence_id") != _evidence_id(job, record)
            or observation.get("status") != "context"
            or observation.get("coding_sequence_sha256")
            != record["coding_sequence_sha256"]
            or observation.get("sequence_length_nt") != record["sequence_length_nt"]
            or observation.get("candidate_id") != record["candidate_id"]
            or observation.get("score_semantics")
            != "higher_is_more_likely_under_pinned_evo2"
            or predicted_count != record["sequence_length_nt"] - 1
            or any(
                not isinstance(numeric, (int, float))
                or isinstance(numeric, bool)
                or not math.isfinite(float(numeric))
                for numeric in numeric_values
            )
            or not math.isclose(
                float(total), float(value) * predicted_count, rel_tol=1e-6, abs_tol=1e-6
            )
            or not math.isclose(
                float(perplexity), math.exp(-float(value)), rel_tol=1e-6, abs_tol=1e-6
            )
        ):
            raise ValueError(f"Invalid Evo 2 evidence observation: {design_id}")
        observed_ids.add(design_id)
    if observed_ids != set(expected):
        raise ValueError("Evo 2 evidence does not cover the complete job")
    if (
        run_manifest.get("schema_version") != EVO2_RESULT_SCHEMA
        or run_manifest.get("job_identity") != job["job_identity"]
        or run_manifest.get("status") != "complete"
        or run_manifest.get("record_count") != len(job["records"])
        or run_manifest.get("job_manifest_sha256")
        != hashlib.sha256(_json_bytes(job)).hexdigest()
        or run_manifest.get("sequences_fasta_sha256") != hashlib.sha256(fasta).hexdigest()
        or run_manifest.get("evidence_sha256")
        != hashlib.sha256(_json_bytes(evidence)).hexdigest()
        or run_manifest.get("model") != job["model"]
    ):
        raise ValueError("Evo 2 result manifest binding mismatch")
    execution = run_manifest.get("execution")
    if (
        not isinstance(execution, dict)
        or execution.get("checkpoint_sha256") != job["model"]["checkpoint_sha256"]
        or execution.get("evo2_package_version") != job["model"]["package_version"]
        or not isinstance(execution.get("torch_version"), str)
        or not execution["torch_version"]
        or not isinstance(execution.get("device"), str)
        or not execution["device"].startswith("cuda")
        or not isinstance(execution.get("gpu_name"), str)
        or not execution["gpu_name"]
        or not isinstance(execution.get("use_kernels"), bool)
        or not isinstance(execution.get("scoring_dtype"), str)
        or not execution["scoring_dtype"]
        or not isinstance(execution.get("elapsed_seconds"), (int, float))
        or isinstance(execution.get("elapsed_seconds"), bool)
        or not math.isfinite(float(execution["elapsed_seconds"]))
        or float(execution["elapsed_seconds"]) < 0
    ):
        raise ValueError("Evo 2 GPU execution metadata is incomplete or unpinned")
    result_identity = _require_sha256(run_manifest.get("result_identity"), "result_identity")
    if result_identity != _document_identity(run_manifest, "result_identity"):
        raise ValueError("Evo 2 result identity mismatch")


def write_evo2_result_archive(
    job: dict[str, Any],
    fasta: bytes,
    scores: list[dict[str, Any]],
    *,
    tool_version: str,
    output_root: str | Path,
    execution: dict[str, Any],
) -> dict[str, Any]:
    validate_evo2_job_documents(job, fasta)
    evidence = build_evo2_evidence(job, scores, tool_version=tool_version)
    run_manifest: dict[str, Any] = {
        "schema_version": EVO2_RESULT_SCHEMA,
        "result_identity": "pending",
        "job_identity": job["job_identity"],
        "status": "complete",
        "record_count": len(job["records"]),
        "job_manifest_sha256": hashlib.sha256(_json_bytes(job)).hexdigest(),
        "sequences_fasta_sha256": hashlib.sha256(fasta).hexdigest(),
        "evidence_sha256": hashlib.sha256(_json_bytes(evidence)).hexdigest(),
        "model": job["model"],
        "execution": execution,
    }
    run_manifest["result_identity"] = _document_identity(run_manifest, "result_identity")
    validate_evo2_result_documents(job, fasta, evidence, run_manifest)
    files = {
        "job-manifest.json": _json_bytes(job),
        "sequences.fasta": fasta,
        "evo2-evidence.json": _json_bytes(evidence),
        "run-manifest.json": _json_bytes(run_manifest),
    }
    root = Path(output_root).expanduser().resolve()
    result_name = f"{job['job_identity']}-result-{run_manifest['result_identity'][:12]}"
    result_dir = root / result_name
    archive_path = root / f"{result_name}.tar.gz"
    _write_exact_directory(result_dir, files)
    _write_deterministic_archive(archive_path, files)
    return {
        "job_identity": job["job_identity"],
        "result_identity": run_manifest["result_identity"],
        "records": len(job["records"]),
        "result_dir": str(result_dir),
        "archive": str(archive_path),
        "archive_sha256": sha256_file(archive_path),
        "evidence": str(result_dir / "evo2-evidence.json"),
    }


def load_evo2_result_archive(
    archive_path: str | Path,
) -> tuple[dict[str, Any], bytes, dict[str, Any], dict[str, Any], bytes]:
    files = _read_exact_archive(Path(archive_path), RESULT_FILES)
    try:
        job = json.loads(files["job-manifest.json"].decode("utf-8"))
        evidence = json.loads(files["evo2-evidence.json"].decode("utf-8"))
        run_manifest = json.loads(files["run-manifest.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Invalid Evo 2 result JSON: {error}") from error
    if not all(isinstance(value, dict) for value in (job, evidence, run_manifest)):
        raise ValueError("Evo 2 result JSON roots must be objects")
    fasta = files["sequences.fasta"]
    validate_evo2_result_documents(job, fasta, evidence, run_manifest)
    return job, fasta, evidence, run_manifest, files["evo2-evidence.json"]


def import_evo2_results(
    project_config: str | Path,
    *,
    result_archive: str | Path,
) -> dict[str, Any]:
    config = load_project_config(Path(project_config))
    job, _, evidence, run_manifest, evidence_bytes = load_evo2_result_archive(result_archive)
    if job["source"]["project_id"] != config.project_id:
        raise ValueError("Evo 2 result belongs to another project")
    source_run = config.run_root / job["source"]["stage6_run_id"]
    verification = verify_run(source_run)
    if verification["status"] != "pass":
        raise ValueError("Bound Stage 6 run verification failed")
    stage6_manifest = _load_json(source_run / "manifest.json")
    mrna_node = source_run / "nodes" / "mrna_product_design"
    mrna_products_path = mrna_node / "mrna_products.json"
    followup_path = mrna_node / "model_followup_manifest.json"
    mrna_products = _load_json(mrna_products_path)
    source_binding = job["source"]
    if (
        stage6_manifest.get("project_id") != config.project_id
        or sha256_file(source_run / ARTIFACT_INDEX_FILENAME)
        != source_binding["stage6_artifact_index_sha256"]
        or sha256_file(mrna_products_path) != source_binding["mrna_products_sha256"]
        or sha256_file(followup_path) != source_binding["model_followup_manifest_sha256"]
        or mrna_products.get("mrna_design_batch_sha256")
        != source_binding["mrna_design_batch_sha256"]
        or mrna_products.get("routing", {}).get("routing_id") != source_binding["routing_id"]
    ):
        raise ValueError("Current Stage 6 artifacts differ from the Evo 2 job source")

    evidence_path = (
        config.runtime_root
        / "input"
        / "stage6"
        / "mrna-evidence"
        / EVO2_ADAPTER_ID
        / run_manifest["result_identity"]
        / "evo2-evidence.json"
    )
    if evidence_path.exists():
        if not evidence_path.is_file() or evidence_path.read_bytes() != evidence_bytes:
            raise ValueError(f"Existing Evo 2 evidence conflicts with result identity: {evidence_path}")
    else:
        _atomic_bytes(evidence_path, evidence_bytes)

    specification_path = config.runtime_root / MRNA_SPEC_RELATIVE
    specification = _load_json(specification_path)
    if specification.get("routing", {}).get("routing_id") != source_binding["routing_id"]:
        raise ValueError("Current Stage 6 mRNA specification has a different routing identity")
    relative_evidence = evidence_path.relative_to(config.runtime_root).as_posix()
    declaration = specification.get("external_adapters", {}).get(EVO2_ADAPTER_ID)
    archived: Path | None = None
    if declaration != {"status": "provided", "result_path": relative_evidence}:
        archived = archive_runtime_file(
            specification_path, config.runtime_root / "input" / "stage6" / "history"
        )
        specification["external_adapters"][EVO2_ADAPTER_ID] = {
            "status": "provided",
            "result_path": relative_evidence,
        }
        _atomic_json(specification_path, specification)
    return {
        "job_identity": job["job_identity"],
        "result_identity": run_manifest["result_identity"],
        "observations": len(evidence["observations"]),
        "evidence_path": str(evidence_path),
        "evidence_sha256": sha256_file(evidence_path),
        "specification_path": str(specification_path),
        "archived_specification": str(archived) if archived is not None else None,
        "stage5_run_path": str(config.run_root / source_binding["stage5_run_id"]),
    }
