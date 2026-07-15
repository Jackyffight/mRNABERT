"""GPU-capable sequence-model adapters for Stage 5 evidence."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Callable

from .assessment_specs import (
    DEVELOPABILITY_SPEC_RELATIVE,
    EVIDENCE_SCHEMA,
    _atomic_json,
    _resolve_structure_run,
    initialize_assessment_specifications,
    load_residue_evidence,
)
from .config import ProjectConfig, load_project_config
from .structure_job import _load_json
from .verification import sha256_file


ADAPTER_SCHEMA = "vaxflow.stage5-sequence-model-adapter.v1"
TOOLCHAIN_SCHEMA = "vaxflow.stage5-sequence-toolchain.v1"
TMBED_VERSION = "1.0.2"
TMBED_REVISION = "8cee893523eb655bc9485c00c65336d27a236191"
METAPREDICT_MODEL_VERSION = "V3"
METAPREDICT_REVISION = "34ddeefba8285c57fb5307792ce5f6789f860bef"
DISORDER_THRESHOLD = 0.5
MINIMUM_IDR_LENGTH = 12
MINIMUM_FOLDED_DOMAIN_LENGTH = 50
GAP_CLOSURE = 10


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode("utf-8")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(value))


def _candidate_records(
    candidate_batch: dict[str, Any],
) -> tuple[str, dict[str, dict[str, Any]]]:
    lines: list[str] = []
    records: dict[str, dict[str, Any]] = {}
    for index, candidate in enumerate(candidate_batch.get("candidates", [])):
        record_id = f"c{index:03d}"
        candidate_id = candidate.get("candidate_id")
        sequence = candidate.get("amino_acid_sequence")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ValueError(f"Candidate at index {index} has no candidate_id")
        if not isinstance(sequence, str) or not sequence:
            raise ValueError(f"Candidate {candidate_id} has no amino-acid sequence")
        records[record_id] = candidate
        lines.extend((f">{record_id}", sequence))
    if not records:
        raise ValueError("Candidate batch contains no candidates")
    return "\n".join(lines) + "\n", records


def _run_checked(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    log_path: Path,
    tool_name: str,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        try:
            subprocess.run(
                command,
                cwd=cwd,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as error:
            log.flush()
            tail = "\n".join(
                log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
            ) or "<empty predictor log>"
            raise ValueError(
                f"{tool_name} failed with exit code {error.returncode}.\n"
                f"Predictor log tail:\n{tail}"
            ) from error


def _git_revision(source_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=source_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError(f"Unable to read source revision: {source_root}") from error
    revision = result.stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=source_root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if dirty:
        raise ValueError(
            f"Pinned source checkout contains tracked modifications: {source_root}"
        )
    return revision


def _source_revision(source_root: Path) -> str:
    if (source_root / ".git").is_dir():
        return _git_revision(source_root)

    provenance_path = source_root / ".source-provenance.json"
    if not provenance_path.is_file():
        raise ValueError(f"Unable to read source revision: {source_root}")
    provenance = _load_json(provenance_path)
    if provenance.get("schema_version") != "vaxflow.source-archive.v1":
        raise ValueError(f"Unsupported source provenance: {provenance_path}")
    revision = provenance.get("revision")
    if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError(f"Invalid archive source revision: {provenance_path}")
    files = provenance.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError(f"Archive source inventory is empty: {provenance_path}")

    resolved_root = source_root.resolve()
    for relative_path, expected_sha256 in files.items():
        if not isinstance(relative_path, str) or not isinstance(expected_sha256, str):
            raise ValueError(f"Invalid archive source inventory: {provenance_path}")
        source_file = (resolved_root / relative_path).resolve()
        if not source_file.is_relative_to(resolved_root) or not source_file.is_file():
            raise ValueError(f"Archive source file is missing: {relative_path}")
        if sha256_file(source_file) != expected_sha256:
            raise ValueError(f"Archive source checksum mismatch: {relative_path}")
    return revision


def _directory_inventory(root: Path) -> tuple[str, dict[str, str]]:
    files = {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
    if not files:
        raise ValueError(f"Model directory contains no files: {root}")
    digest = hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return digest, files


def _load_toolchain(toolchain_root: Path) -> dict[str, Any]:
    root = toolchain_root.expanduser().resolve()
    manifest_path = root / "toolchain.json"
    manifest = _load_json(manifest_path)
    if manifest.get("schema_version") != TOOLCHAIN_SCHEMA:
        raise ValueError(f"Unsupported Stage 5 toolchain manifest: {manifest_path}")
    expected = {
        "tmbed": TMBED_REVISION,
        "metapredict": METAPREDICT_REVISION,
    }
    if manifest.get("source_revisions") != expected:
        raise ValueError("Stage 5 toolchain source revisions do not match the code profile")
    freeze_path = root / "requirements.freeze.txt"
    if (
        not freeze_path.is_file()
        or manifest.get("requirements_freeze_sha256") != sha256_file(freeze_path)
    ):
        raise ValueError("Stage 5 toolchain dependency freeze failed integrity checks")

    paths: dict[str, Path] = {}
    for name in (
        "python_executable",
        "tmbed_source_root",
        "metapredict_source_root",
        "tmbed_model_dir",
    ):
        value = manifest.get(name)
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError(f"Stage 5 toolchain {name} must be an absolute path")
        path = Path(value).expanduser()
        paths[name] = path if name == "python_executable" else path.resolve()
    if not paths["python_executable"].is_file() or not os.access(
        paths["python_executable"], os.X_OK
    ):
        raise ValueError(
            f"Stage 5 Python executable is unavailable: {paths['python_executable']}"
        )
    for name in ("tmbed_source_root", "metapredict_source_root", "tmbed_model_dir"):
        if not paths[name].is_dir():
            raise ValueError(f"Stage 5 toolchain path is unavailable: {paths[name]}")
    if _source_revision(paths["tmbed_source_root"]) != TMBED_REVISION:
        raise ValueError("TMbed source checkout has the wrong revision")
    if _source_revision(paths["metapredict_source_root"]) != METAPREDICT_REVISION:
        raise ValueError("metapredict source checkout has the wrong revision")

    probe = subprocess.run(
        [
            str(paths["python_executable"]),
            "-c",
            (
                "import importlib.metadata as m,json,torch;"
                "import metapredict,tmbed;"
                "print(json.dumps({'torch':torch.__version__,"
                "'cuda_available':torch.cuda.is_available(),"
                "'tmbed':m.version('tmbed'),"
                "'tmbed_file':tmbed.__file__,"
                "'metapredict':m.version('metapredict'),"
                "'metapredict_file':metapredict.__file__},sort_keys=True))"
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    try:
        environment_probe = json.loads(probe.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as error:
        raise ValueError("Stage 5 toolchain probe returned invalid JSON") from error
    if environment_probe.get("tmbed") != TMBED_VERSION:
        raise ValueError(
            f"TMbed version mismatch: expected {TMBED_VERSION}, "
            f"got {environment_probe.get('tmbed')}"
        )
    for package_name, source_name in (
        ("tmbed", "tmbed_source_root"),
        ("metapredict", "metapredict_source_root"),
    ):
        package_file = Path(str(environment_probe.get(f"{package_name}_file", ""))).resolve()
        if not package_file.is_relative_to(paths[source_name]):
            raise ValueError(
                f"{package_name} import does not resolve to its pinned source checkout"
            )
    model_digest, model_files = _directory_inventory(paths["tmbed_model_dir"])
    return {
        "root": root,
        "manifest_path": manifest_path,
        "manifest_sha256": sha256_file(manifest_path),
        "paths": paths,
        "environment": environment_probe,
        "tmbed_model_sha256": model_digest,
        "tmbed_model_files": model_files,
    }


def parse_tmbed_three_line(path: Path) -> dict[str, dict[str, str]]:
    """Parse TMbed's directed three-line output with strict sequence checks."""

    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines or len(lines) % 3:
        raise ValueError(f"TMbed output is not a three-line record file: {path}")
    records: dict[str, dict[str, str]] = {}
    for offset in range(0, len(lines), 3):
        header, sequence, labels = lines[offset : offset + 3]
        if not header.startswith(">"):
            raise ValueError(f"TMbed record has no FASTA header: {header!r}")
        record_id = header[1:].split(maxsplit=1)[0]
        if not record_id or record_id in records:
            raise ValueError(f"TMbed record ID is empty or duplicated: {record_id!r}")
        if len(sequence) != len(labels):
            raise ValueError(f"TMbed sequence/label length mismatch: {record_id}")
        invalid = set(labels) - set("BbHhS.")
        if invalid:
            raise ValueError(f"TMbed record contains invalid labels {sorted(invalid)}")
        records[record_id] = {"sequence": sequence, "labels": labels}
    return records


def _label_segments(labels: str, accepted: set[str]) -> list[tuple[int, int, str]]:
    segments: list[tuple[int, int, str]] = []
    index = 0
    while index < len(labels):
        label = labels[index]
        if label not in accepted:
            index += 1
            continue
        end = index + 1
        while end < len(labels) and labels[end] == label:
            end += 1
        segments.append((index + 1, end, label))
        index = end
    return segments


def build_tmbed_observations(
    predictions: dict[str, dict[str, str]],
    record_map: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if set(predictions) != set(record_map):
        missing = sorted(set(record_map) - set(predictions))
        extra = sorted(set(predictions) - set(record_map))
        raise ValueError(f"TMbed record set mismatch: missing={missing} extra={extra}")
    signal_peptides: list[dict[str, Any]] = []
    topology: list[dict[str, Any]] = []
    for record_id, candidate in record_map.items():
        prediction = predictions[record_id]
        sequence = candidate["amino_acid_sequence"]
        if prediction["sequence"] != sequence:
            raise ValueError(f"TMbed sequence differs from candidate: {record_id}")
        for start, end, label in _label_segments(prediction["labels"], {"S"}):
            identity = f"signal|{candidate['candidate_id']}|{start}|{end}|{label}"
            signal_peptides.append(
                {
                    "evidence_id": "tmbed-sp-"
                    + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
                    "candidate_id": candidate["candidate_id"],
                    "sequence_sha256": candidate["amino_acid_sha256"],
                    "residue_start": start,
                    "residue_end": end,
                    "status": "context",
                    "prediction": "signal_peptide",
                    "tmbed_label": label,
                }
            )
        topology_names = {
            "H": "alpha_helix",
            "h": "alpha_helix",
            "B": "beta_strand",
            "b": "beta_strand",
        }
        orientations = {
            "H": "inside_to_outside",
            "B": "inside_to_outside",
            "h": "outside_to_inside",
            "b": "outside_to_inside",
        }
        for start, end, label in _label_segments(prediction["labels"], set(topology_names)):
            identity = f"topology|{candidate['candidate_id']}|{start}|{end}|{label}"
            topology.append(
                {
                    "evidence_id": "tmbed-tm-"
                    + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
                    "candidate_id": candidate["candidate_id"],
                    "sequence_sha256": candidate["amino_acid_sha256"],
                    "residue_start": start,
                    "residue_end": end,
                    "status": "context",
                    "prediction": "transmembrane_segment",
                    "segment_type": topology_names[label],
                    "orientation": orientations[label],
                    "tmbed_label": label,
                }
            )
    signal_peptides.sort(key=lambda item: (item["candidate_id"], item["residue_start"]))
    topology.sort(key=lambda item: (item["candidate_id"], item["residue_start"]))
    return signal_peptides, topology


def build_disorder_observations(
    raw_result: dict[str, Any],
    record_map: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if raw_result.get("model_version") != METAPREDICT_MODEL_VERSION:
        raise ValueError("metapredict worker used the wrong model version")
    records = raw_result.get("records")
    if not isinstance(records, dict) or set(records) != set(record_map):
        raise ValueError("metapredict record set differs from the candidate batch")
    observations: list[dict[str, Any]] = []
    for record_id, candidate in record_map.items():
        record = records[record_id]
        sequence = candidate["amino_acid_sequence"]
        if record.get("sequence") != sequence:
            raise ValueError(f"metapredict sequence differs from candidate: {record_id}")
        scores = record.get("scores")
        boundaries = record.get("disordered_domain_boundaries")
        if not isinstance(scores, list) or len(scores) != len(sequence):
            raise ValueError(f"metapredict score length mismatch: {record_id}")
        if not all(
            isinstance(score, (int, float))
            and not isinstance(score, bool)
            and math.isfinite(score)
            and 0.0 <= score <= 1.0
            for score in scores
        ):
            raise ValueError(f"metapredict returned invalid scores: {record_id}")
        if not isinstance(boundaries, list):
            raise ValueError(f"metapredict returned invalid IDR boundaries: {record_id}")
        for boundary in boundaries:
            if (
                not isinstance(boundary, list)
                or len(boundary) != 2
                or not all(isinstance(value, int) and not isinstance(value, bool) for value in boundary)
            ):
                raise ValueError(f"metapredict returned an invalid IDR boundary: {record_id}")
            start_zero, end_exclusive = boundary
            if start_zero < 0 or end_exclusive <= start_zero or end_exclusive > len(sequence):
                raise ValueError(f"metapredict IDR boundary is out of range: {record_id}")
            region_scores = scores[start_zero:end_exclusive]
            identity = (
                f"disorder|{candidate['candidate_id']}|{start_zero + 1}|{end_exclusive}"
            )
            observations.append(
                {
                    "evidence_id": "metapredict-idr-"
                    + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
                    "candidate_id": candidate["candidate_id"],
                    "sequence_sha256": candidate["amino_acid_sha256"],
                    "residue_start": start_zero + 1,
                    "residue_end": end_exclusive,
                    "status": "context",
                    "prediction": "intrinsically_disordered_region",
                    "mean_disorder_score": round(sum(region_scores) / len(region_scores), 6),
                    "maximum_disorder_score": round(max(region_scores), 6),
                    "threshold": DISORDER_THRESHOLD,
                }
            )
    observations.sort(key=lambda item: (item["candidate_id"], item["residue_start"]))
    return observations


def _evidence_document(
    *,
    adapter_id: str,
    candidate_batch_sha256: str,
    tool: dict[str, str],
    policy: dict[str, Any],
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": EVIDENCE_SCHEMA,
        "adapter_id": adapter_id,
        "candidate_batch_sha256": candidate_batch_sha256,
        "tool": tool,
        "classification_policy": policy,
        "observations": observations,
    }


def _verify_existing_output(output_dir: Path, identity: str) -> dict[str, Any]:
    manifest = _load_json(output_dir / "manifest.json")
    if manifest.get("identity") != identity:
        raise ValueError(f"Existing Stage 5 output has a different identity: {output_dir}")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError(f"Existing Stage 5 manifest has no artifact index: {output_dir}")
    for relative, expected_sha256 in artifacts.items():
        path = output_dir / relative
        if not path.is_file() or sha256_file(path) != expected_sha256:
            raise ValueError(f"Existing Stage 5 artifact failed integrity check: {path}")
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


def _update_developability_specification(
    config: ProjectConfig,
    evidence_paths: dict[str, Path],
) -> Path:
    specification_path = config.runtime_root / DEVELOPABILITY_SPEC_RELATIVE
    specification = _load_json(specification_path)
    for adapter_id, evidence_path in evidence_paths.items():
        specification["external_adapters"][adapter_id] = {
            "status": "provided",
            "result_path": evidence_path.relative_to(config.runtime_root).as_posix(),
        }
    _atomic_json(specification_path, specification)
    return specification_path


def _device_environment(device: str) -> tuple[dict[str, str], str, bool]:
    environment = os.environ.copy()
    environment.pop("HF_HOME", None)
    environment["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
    environment["TOKENIZERS_PARALLELISM"] = "false"
    environment["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"
    if device == "cpu":
        return environment, "cpu", False
    match = re.fullmatch(r"cuda(?::([0-9]+))?", device)
    if not match:
        raise ValueError("--device must be cpu, cuda, or cuda:<non-negative index>")
    if match.group(1) is not None:
        environment["CUDA_VISIBLE_DEVICES"] = match.group(1)
    return environment, "cuda", True


def prepare_stage5_sequence_evidence(
    project_config: str | Path,
    *,
    source_run_dir: str | Path | None,
    toolchain_root: str | Path,
    device: str = "cuda:0",
    tmbed_batch_size: int = 4000,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run TMbed and metapredict and register three Stage 5 evidence adapters."""

    if tmbed_batch_size < 1:
        raise ValueError("TMbed batch size must be positive")
    config = load_project_config(Path(project_config))
    source = _resolve_structure_run(
        config,
        Path(source_run_dir) if source_run_dir is not None else None,
    )
    initialize_assessment_specifications(project_config, source_run_dir=source)
    toolchain = _load_toolchain(Path(toolchain_root))
    environment, worker_device, use_gpu = _device_environment(device)
    if use_gpu and not toolchain["environment"].get("cuda_available"):
        raise ValueError("Stage 5 toolchain cannot see a CUDA device")

    candidate_batch_path = source / "nodes/candidate_specification/candidate_batch.json"
    candidate_batch = _load_json(candidate_batch_path)
    candidate_batch_sha256 = sha256_file(candidate_batch_path)
    fasta_text, record_map = _candidate_records(candidate_batch)
    worker_path = Path(__file__).resolve().parents[2] / "scripts/stage5_metapredict_worker.py"
    if not worker_path.is_file():
        raise ValueError(f"metapredict worker is unavailable: {worker_path}")
    identity_payload = {
        "schema_version": ADAPTER_SCHEMA,
        "project_id": config.project_id,
        "candidate_batch_sha256": candidate_batch_sha256,
        "adapter_sha256": sha256_file(Path(__file__).resolve()),
        "toolchain_manifest_sha256": toolchain["manifest_sha256"],
        "tmbed_revision": TMBED_REVISION,
        "tmbed_model_sha256": toolchain["tmbed_model_sha256"],
        "metapredict_revision": METAPREDICT_REVISION,
        "worker_sha256": sha256_file(worker_path),
        "parameters": {
            "device": device,
            "tmbed_batch_size": tmbed_batch_size,
            "metapredict_model_version": METAPREDICT_MODEL_VERSION,
            "disorder_threshold": DISORDER_THRESHOLD,
            "minimum_idr_length": MINIMUM_IDR_LENGTH,
            "minimum_folded_domain_length": MINIMUM_FOLDED_DOMAIN_LENGTH,
            "gap_closure": GAP_CLOSURE,
        },
    }
    identity = hashlib.sha256(
        json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    output_parent = config.runtime_root / "input/stage5/sequence-models"
    output_dir = output_parent / identity
    output_parent.mkdir(parents=True, exist_ok=True)

    if output_dir.is_dir():
        manifest = _verify_existing_output(output_dir, identity)
    else:
        partial = output_parent / f".{identity}.partial"
        failed = output_parent / f"{identity}.failed"
        shutil.rmtree(partial, ignore_errors=True)
        shutil.rmtree(failed, ignore_errors=True)
        raw_root = partial / "raw"
        raw_root.mkdir(parents=True)
        fasta_path = partial / "candidates.fasta"
        fasta_path.write_text(fasta_text, encoding="utf-8")
        tmbed_output = raw_root / "tmbed.pred"
        metapredict_output = raw_root / "metapredict.json"
        try:
            if progress is not None:
                progress(
                    f"Running TMbed {TMBED_VERSION}: candidates={len(record_map)} device={device}"
                )
            tmbed_command = [
                str(toolchain["paths"]["python_executable"]),
                "-m",
                "tmbed",
                "predict",
                "--fasta",
                str(fasta_path),
                "--predictions",
                str(tmbed_output),
                "--out-format",
                "0",
                "--batch-size",
                str(tmbed_batch_size),
                "--model-dir",
                str(toolchain["paths"]["tmbed_model_dir"]),
                "--use-gpu" if use_gpu else "--no-use-gpu",
                "--no-cpu-fallback" if use_gpu else "--cpu-fallback",
            ]
            _run_checked(
                tmbed_command,
                cwd=partial,
                environment=environment,
                log_path=raw_root / "tmbed.log",
                tool_name="TMbed",
            )
            tmbed_predictions = parse_tmbed_three_line(tmbed_output)
            signal_observations, topology_observations = build_tmbed_observations(
                tmbed_predictions, record_map
            )
            if progress is not None:
                progress(
                    "TMbed complete: "
                    f"signal_regions={len(signal_observations)} "
                    f"transmembrane_regions={len(topology_observations)}"
                )

            if progress is not None:
                progress(
                    f"Running metapredict {METAPREDICT_MODEL_VERSION}: "
                    f"candidates={len(record_map)} device={device}"
                )
            _run_checked(
                [
                    str(toolchain["paths"]["python_executable"]),
                    str(worker_path),
                    "--input-fasta",
                    str(fasta_path),
                    "--output-json",
                    str(metapredict_output),
                    "--device",
                    worker_device,
                    "--model-version",
                    METAPREDICT_MODEL_VERSION,
                    "--minimum-idr-length",
                    str(MINIMUM_IDR_LENGTH),
                    "--minimum-folded-domain-length",
                    str(MINIMUM_FOLDED_DOMAIN_LENGTH),
                    "--gap-closure",
                    str(GAP_CLOSURE),
                ],
                cwd=partial,
                environment=environment,
                log_path=raw_root / "metapredict.log",
                tool_name="metapredict",
            )
            disorder_observations = build_disorder_observations(
                _load_json(metapredict_output), record_map
            )
            if progress is not None:
                progress(f"metapredict complete: idr_regions={len(disorder_observations)}")

            tmbed_tool = {
                "name": "TMbed",
                "version": TMBED_VERSION,
                "revision": f"{TMBED_REVISION}+model-{toolchain['tmbed_model_sha256'][:16]}",
            }
            metapredict_tool = {
                "name": "metapredict",
                "version": METAPREDICT_MODEL_VERSION,
                "revision": METAPREDICT_REVISION,
            }
            evidence_documents = {
                "signal_peptide": _evidence_document(
                    adapter_id="signal_peptide",
                    candidate_batch_sha256=candidate_batch_sha256,
                    tool=tmbed_tool,
                    policy={
                        "source": "TMbed directed segment decoder",
                        "status_semantics": "Predicted regions are context until expression policy is approved.",
                    },
                    observations=signal_observations,
                ),
                "transmembrane_topology": _evidence_document(
                    adapter_id="transmembrane_topology",
                    candidate_batch_sha256=candidate_batch_sha256,
                    tool=tmbed_tool,
                    policy={
                        "source": "TMbed directed segment decoder",
                        "status_semantics": "Predicted regions are context until product topology is approved.",
                    },
                    observations=topology_observations,
                ),
                "disorder": _evidence_document(
                    adapter_id="disorder",
                    candidate_batch_sha256=candidate_batch_sha256,
                    tool=metapredict_tool,
                    policy={
                        "model": METAPREDICT_MODEL_VERSION,
                        "threshold": DISORDER_THRESHOLD,
                        "minimum_idr_length": MINIMUM_IDR_LENGTH,
                        "minimum_folded_domain_length": MINIMUM_FOLDED_DOMAIN_LENGTH,
                        "gap_closure": GAP_CLOSURE,
                        "status_semantics": "Predicted IDRs are context until developability policy is approved.",
                    },
                    observations=disorder_observations,
                ),
            }
            for adapter_id, document in evidence_documents.items():
                _write_json(partial / f"{adapter_id}.json", document)

            artifacts = {
                path.relative_to(partial).as_posix(): sha256_file(path)
                for path in sorted(partial.rglob("*"))
                if path.is_file()
            }
            manifest = {
                **identity_payload,
                "identity": identity,
                "source_run": str(source),
                "source_run_id": _load_json(source / "manifest.json")["run_id"],
                "toolchain_environment": toolchain["environment"],
                "record_map": {
                    record_id: candidate["candidate_id"]
                    for record_id, candidate in record_map.items()
                },
                "summary": {
                    "candidate_count": len(record_map),
                    "observation_counts": {
                        adapter_id: len(document["observations"])
                        for adapter_id, document in evidence_documents.items()
                    },
                    "evaluated_adapters": sorted(evidence_documents),
                    "not_evaluated_adapters": ["solubility", "aggregation"],
                },
                "artifacts": artifacts,
            }
            _write_json(partial / "manifest.json", manifest)
            partial.rename(output_dir)
        except Exception as error:
            failed_path = _preserve_failed_output(
                partial, failed, identity=identity, error=error
            )
            raise ValueError(
                f"{error}\nFailed adapter artifacts preserved at: {failed_path}"
            ) from error

    candidate_by_id = {
        candidate["candidate_id"]: candidate for candidate in candidate_batch["candidates"]
    }
    evidence_paths = {
        adapter_id: output_dir / f"{adapter_id}.json"
        for adapter_id in ("signal_peptide", "transmembrane_topology", "disorder")
    }
    for adapter_id, evidence_path in evidence_paths.items():
        load_residue_evidence(
            evidence_path,
            adapter_id=adapter_id,
            candidate_by_id=candidate_by_id,
            candidate_batch_sha256=candidate_batch_sha256,
        )
    specification_path = _update_developability_specification(config, evidence_paths)
    return {
        "identity": identity,
        "output_dir": str(output_dir),
        "manifest": str(output_dir / "manifest.json"),
        "developability_specification": str(specification_path),
        "candidate_count": manifest["summary"]["candidate_count"],
        "observation_counts": manifest["summary"]["observation_counts"],
        "evaluated_adapters": manifest["summary"]["evaluated_adapters"],
        "not_evaluated_adapters": manifest["summary"]["not_evaluated_adapters"],
    }
