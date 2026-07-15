"""Validate constrained external-model proposals and return them to Stage 2."""

from __future__ import annotations

import csv
from html import escape
from io import StringIO
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from .config import load_project_config
from .proposal_generation import (
    _artifact_index_valid,
    _document_sha256,
    _json_text,
    _load_object,
    _sequence_sha256,
    _text,
    _wrap_fasta,
)
from .qc import CANONICAL_AMINO_ACIDS
from .stage2_search import MODEL_JOBS_SCHEMA, POOL_SCHEMA, verify_stage2_search
from .verification import ARTIFACT_INDEX_FILENAME, build_artifact_index, sha256_file


RESULT_SCHEMA = "vaxflow.stage2-external-proposals.v1"
CONTEXT_SCHEMA = "vaxflow.stage2-model-import-context.v1"
PROPOSAL_SCHEMA = "vaxflow.stage2-model-proposal-batch.v1"
SUMMARY_SCHEMA = "vaxflow.stage2-model-import-summary.v1"
IMPORTER_ID = "constrained-external-proposal-importer"
IMPORTER_VERSION = "1"
EXPECTED_FILES = {
    "inputs/base_candidate_specification.json",
    "inputs/context.json",
    "inputs/external_model_jobs.json",
    "inputs/results.json",
    "inputs/search_candidate_pool.json",
    "inputs/search_seed_candidate_batch.json",
    "candidate_specification.generated.json",
    "proposal_batch.json",
    "proposals.csv",
    "proposals.fasta",
    "report.html",
    "summary.json",
    ARTIFACT_INDEX_FILENAME,
}


def _strict_identifier(value: Any, field: str) -> str:
    identifier = _text(value, field)
    if not all(character.isalnum() or character in "._-" for character in identifier):
        raise ValueError(f"{field} contains unsupported characters: {identifier!r}")
    return identifier


def _load_search(directory: str | Path) -> tuple[Path, dict[str, Any]]:
    root = Path(directory).expanduser().resolve()
    verification = verify_stage2_search(root)
    if verification["status"] != "pass":
        raise ValueError(
            "Stage 2 search verification failed: "
            + "; ".join(verification["errors"][:5])
        )
    summary = _load_object(root / "search_summary.json", "Stage 2 search summary")
    if summary.get("search_identity") != root.name:
        raise ValueError("Stage 2 search identity differs from its directory")
    return root, summary


def _select_job(jobs: dict[str, Any], job_id: str, search_identity: str) -> dict[str, Any]:
    if (
        jobs.get("schema_version") != MODEL_JOBS_SCHEMA
        or jobs.get("search_identity") != search_identity
        or not isinstance(jobs.get("jobs"), list)
    ):
        raise ValueError("External-model job document is invalid or mismatched")
    matches = [job for job in jobs["jobs"] if job.get("job_id") == job_id]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one external-model job named {job_id!r}")
    job = matches[0]
    if job.get("job_identity") != _document_sha256(
        {key: value for key, value in job.items() if key != "job_identity"}
    ):
        raise ValueError("External-model job identity mismatch")
    if job.get("status") != "ready_for_external_execution":
        raise ValueError(
            f"External-model job {job_id} is still {job.get('status')}; "
            "its required upstream evidence has not been attached"
        )
    return job


def _sequence_inventory(
    seed: dict[str, Any],
    pool: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str]]:
    if not isinstance(seed.get("candidates"), list):
        raise ValueError("Search seed candidate batch has no candidate array")
    if pool.get("schema_version") != POOL_SCHEMA or not isinstance(pool.get("records"), list):
        raise ValueError("Search candidate pool is invalid")
    by_key: dict[str, str] = {}
    by_hash: dict[str, str] = {}
    for record in [*seed["candidates"], *pool["records"]]:
        key = record.get("candidate_key")
        sequence = record.get("amino_acid_sequence")
        sequence_sha = record.get("amino_acid_sha256")
        if (
            not isinstance(key, str)
            or not key
            or not isinstance(sequence, str)
            or not sequence
            or set(sequence) - CANONICAL_AMINO_ACIDS
            or sequence_sha != _sequence_sha256(sequence)
        ):
            raise ValueError("Search sequence inventory contains an invalid candidate")
        previous = by_key.get(key)
        if previous is not None and previous != sequence:
            raise ValueError(f"Search candidate key {key!r} maps to multiple sequences")
        by_key[key] = sequence
        by_hash.setdefault(sequence_sha, key)
    return by_key, by_hash


def _finite_optional(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{field} must be numeric when supplied")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{field} must be finite")
    return numeric


def _validate_results(
    results: dict[str, Any],
    job: dict[str, Any],
    sequence_by_key: dict[str, str],
    existing_by_hash: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    expected_header = {
        "schema_version": RESULT_SCHEMA,
        "search_identity": job["search_identity"],
        "job_id": job["job_id"],
        "job_identity": job["job_identity"],
        "adapter_id": job["adapter_id"],
        "model": job["model"],
    }
    for field, expected in expected_header.items():
        if results.get(field) != expected:
            raise ValueError(f"External-model result {field} differs from its job")
    records = results.get("records")
    if not isinstance(records, list):
        raise ValueError("External-model results.records must be an array")
    job_by_parent = {
        record["parent_candidate_key"]: record for record in job["records"]
    }
    counts: dict[str, int] = {}
    accepted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    observed_hashes = dict(existing_by_hash)
    for index, record in enumerate(records):
        field = f"results.records[{index}]"
        if not isinstance(record, dict):
            raise ValueError(f"{field} must be an object")
        parent_key = _strict_identifier(
            record.get("parent_candidate_key"), f"{field}.parent_candidate_key"
        )
        parent_job = job_by_parent.get(parent_key)
        if parent_job is None:
            raise ValueError(f"{field} references a parent outside the job")
        counts[parent_key] = counts.get(parent_key, 0) + 1
        if counts[parent_key] > int(job["variants_per_parent"]):
            raise ValueError(f"{field} exceeds variants_per_parent for {parent_key}")
        parent_sequence = sequence_by_key.get(parent_key)
        if (
            parent_sequence is None
            or parent_sequence != parent_job.get("sequence")
            or parent_job.get("sequence_sha256") != _sequence_sha256(parent_sequence)
        ):
            raise ValueError(f"{field} parent sequence differs from the search pool")
        sequence = _text(record.get("amino_acid_sequence"), f"{field}.amino_acid_sequence")
        if set(sequence) - CANONICAL_AMINO_ACIDS:
            raise ValueError(f"{field} contains non-canonical amino acids")
        if len(sequence) != len(parent_sequence):
            raise ValueError(f"{field} changed sequence length")
        mutable = set(parent_job.get("mutable_positions", []))
        protected = set(parent_job.get("protected_positions", []))
        changed = [
            position
            for position, (before, after) in enumerate(
                zip(parent_sequence, sequence, strict=True), 1
            )
            if before != after
        ]
        if not changed:
            skipped.append(
                {
                    "record_index": index,
                    "parent_candidate_key": parent_key,
                    "reason": "unchanged_parent_sequence",
                }
            )
            continue
        if set(changed) - mutable:
            raise ValueError(f"{field} mutates positions outside the declared mask")
        if set(changed) & protected:
            raise ValueError(f"{field} mutates protected positions")
        if len(changed) > int(parent_job.get("maximum_substitutions", 0)):
            raise ValueError(f"{field} exceeds maximum_substitutions")
        sequence_sha = _sequence_sha256(sequence)
        duplicate = observed_hashes.get(sequence_sha)
        if duplicate is not None:
            skipped.append(
                {
                    "record_index": index,
                    "parent_candidate_key": parent_key,
                    "reason": "duplicate_amino_acid_sequence",
                    "duplicate_of": duplicate,
                    "amino_acid_sha256": sequence_sha,
                }
            )
            continue
        metadata = record.get("model_metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError(f"{field}.model_metadata must be an object")
        model_score = _finite_optional(record.get("model_score"), f"{field}.model_score")
        proposal_identity = _document_sha256(
            {
                "job_identity": job["job_identity"],
                "parent_candidate_key": parent_key,
                "amino_acid_sha256": sequence_sha,
            }
        )
        candidate_key = f"model-{job['adapter_id']}-{proposal_identity[:14]}"
        mutations = [
            {
                "position": position,
                "from": parent_sequence[position - 1],
                "to": sequence[position - 1],
            }
            for position in changed
        ]
        accepted.append(
            {
                "candidate_key": candidate_key,
                "parent_candidate_key": parent_key,
                "amino_acid_sequence": sequence,
                "amino_acid_sha256": sequence_sha,
                "aa_length": len(sequence),
                "mutations": mutations,
                "model_score": model_score,
                "model_metadata": metadata,
                "source_record_index": index,
            }
        )
        observed_hashes[sequence_sha] = candidate_key
    accepted.sort(key=lambda item: item["candidate_key"])
    skipped.sort(key=lambda item: item["record_index"])
    return accepted, skipped


def _expanded_specification(
    base: dict[str, Any],
    accepted: list[dict[str, Any]],
    job: dict[str, Any],
    import_identity: str,
) -> dict[str, Any]:
    specification = json.loads(json.dumps(base))
    manual = specification.get("manual_candidates")
    if not isinstance(manual, list):
        raise ValueError("Base candidate specification has no manual_candidates array")
    known_keys = {record.get("candidate_key") for record in manual}
    for record in accepted:
        if record["parent_candidate_key"] not in known_keys:
            raise ValueError(
                f"Model proposal parent {record['parent_candidate_key']} is not materialized"
            )
        manual.append(
            {
                "candidate_key": record["candidate_key"],
                "display_name": (
                    f"{job['adapter_id']} redesign of {record['parent_candidate_key']}"
                ),
                "candidate_type": "fusion",
                "amino_acid_sequence": record["amino_acid_sequence"],
                "claimed_component_keys": [record["parent_candidate_key"]],
                "annotation_status": "unreviewed",
                "proposal": {
                    "generator": {
                        "id": job["adapter_id"],
                        "version": job["model"]["revision"],
                        "parameters": {
                            "import_identity": import_identity,
                            "job_id": job["job_id"],
                            "job_identity": job["job_identity"],
                            "model_name": job["model"]["name"],
                            "model_score": record["model_score"],
                            "model_metadata": record["model_metadata"],
                            "mutations": record["mutations"],
                        },
                    },
                    "parent_candidate_keys": [record["parent_candidate_key"]],
                    "transformation": "constrained_substitution",
                    "rationale": (
                        "External model proposal accepted only after deterministic "
                        "parent, residue-mask, mutation-count, sequence, and model-pin checks."
                    ),
                    "feedback_request_ids": [],
                },
            }
        )
        known_keys.add(record["candidate_key"])
    specification["specification_id"] = (
        f"{base['specification_id']}-model-{import_identity[:12]}"
    )
    specification["batch_label"] = (
        f"{base.get('batch_label', base['specification_id'])} + "
        f"{job['adapter_id']} proposals"
    )
    return specification


def _build_import(
    context: dict[str, Any],
    base_specification: dict[str, Any],
    seed: dict[str, Any],
    pool: dict[str, Any],
    jobs: dict[str, Any],
    results: dict[str, Any],
) -> dict[str, Any]:
    if context.get("schema_version") != CONTEXT_SCHEMA:
        raise ValueError("Unsupported model-import context")
    if (
        jobs.get("project_id") != context.get("project_id")
        or jobs.get("design_round_id") != context.get("design_round_id")
        or jobs.get("search_identity") != context.get("search_identity")
        or seed.get("design_round_id") != context.get("design_round_id")
        or pool.get("search_identity") != context.get("search_identity")
        or base_specification.get("design_round_id") != context.get("design_round_id")
    ):
        raise ValueError("Model-import snapshots have inconsistent search lineage")
    job = _select_job(jobs, context["job_id"], context["search_identity"])
    sequence_by_key, existing_by_hash = _sequence_inventory(seed, pool)
    accepted, skipped = _validate_results(
        results,
        job,
        sequence_by_key,
        existing_by_hash,
    )
    import_identity = _document_sha256(
        {
            "context": context,
            "accepted": [
                {key: value for key, value in record.items() if key != "amino_acid_sequence"}
                for record in accepted
            ],
            "skipped": skipped,
        }
    )
    specification = _expanded_specification(
        base_specification,
        accepted,
        job,
        import_identity,
    )
    proposal_batch = {
        "schema_version": PROPOSAL_SCHEMA,
        "import_identity": import_identity,
        "search_identity": context["search_identity"],
        "job_id": job["job_id"],
        "job_identity": job["job_identity"],
        "adapter_id": job["adapter_id"],
        "model": job["model"],
        "accepted": accepted,
        "skipped": skipped,
    }
    summary = {
        "schema_version": SUMMARY_SCHEMA,
        "import_identity": import_identity,
        "project_id": context["project_id"],
        "design_round_id": context["design_round_id"],
        "search_identity": context["search_identity"],
        "job_id": job["job_id"],
        "adapter_id": job["adapter_id"],
        "model": job["model"],
        "submitted_records": len(results["records"]),
        "accepted_records": len(accepted),
        "skipped_records": len(skipped),
        "status": "materialized_for_stage2_validation",
        "limitations": [
            "Acceptance proves contract compliance, not biological superiority.",
            "Every imported sequence remains unreviewed until downstream evidence is attached.",
        ],
    }
    return {
        "identity": import_identity,
        "job": job,
        "accepted": accepted,
        "skipped": skipped,
        "specification": specification,
        "proposal_batch": proposal_batch,
        "summary": summary,
    }


def _proposal_csv(records: list[dict[str, Any]]) -> str:
    fields = [
        "candidate_key",
        "parent_candidate_key",
        "aa_length",
        "mutation_count",
        "mutations",
        "model_score",
        "amino_acid_sha256",
    ]
    handle = StringIO(newline="")
    writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for record in records:
        writer.writerow(
            {
                **{key: record.get(key, "") for key in fields},
                "mutation_count": len(record["mutations"]),
                "mutations": "|".join(
                    f"{item['from']}{item['position']}{item['to']}"
                    for item in record["mutations"]
                ),
            }
        )
    return handle.getvalue()


def _proposal_fasta(records: list[dict[str, Any]]) -> str:
    return "".join(
        f">{record['candidate_key']} parent={record['parent_candidate_key']} "
        f"mutations={len(record['mutations'])}\n"
        f"{_wrap_fasta(record['amino_acid_sequence'])}\n"
        for record in records
    )


def _report(result: dict[str, Any]) -> str:
    summary = result["summary"]
    rows = "".join(
        "<tr>"
        f"<td><code>{escape(record['candidate_key'])}</code></td>"
        f"<td><code>{escape(record['parent_candidate_key'])}</code></td>"
        f"<td>{len(record['mutations'])}</td>"
        f"<td>{escape(str(record['model_score']))}</td>"
        "</tr>"
        for record in result["accepted"]
    )
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Stage 2 Model Import</title>
<style>body{{margin:0;background:#f5f7f5;color:#17211b;font:15px/1.55 Arial,sans-serif}}header{{padding:32px max(24px,calc((100vw - 1080px)/2));background:#153d2c;color:white}}main{{max-width:1080px;margin:auto;padding:28px 24px}}.metrics{{display:flex;background:white;border:1px solid #d7dfd9}}.metric{{padding:16px 24px;border-right:1px solid #d7dfd9}}.metric b{{display:block;font-size:28px;color:#176b45}}table{{width:100%;border-collapse:collapse;background:white}}th,td{{padding:10px;border-bottom:1px solid #d7dfd9;text-align:left}}.notice{{margin-top:24px;padding:14px;border-left:4px solid #9a5d00;background:#fff8e8}}code{{font-size:12px}}</style></head>
<body><header><h1>模型提案受约束导入 / Constrained model import</h1><p>{escape(summary['adapter_id'])} · {escape(summary['model']['name'])}</p></header><main>
<div class="metrics"><div class="metric"><b>{summary['submitted_records']}</b>submitted</div><div class="metric"><b>{summary['accepted_records']}</b>accepted</div><div class="metric"><b>{summary['skipped_records']}</b>deduplicated/skipped</div></div>
<h2>通过合同的候选 / Contract-valid proposals</h2><table><thead><tr><th>Candidate</th><th>Parent</th><th>Mutations</th><th>Model score</th></tr></thead><tbody>{rows}</tbody></table>
<div class="notice"><strong>结论边界：</strong>此节点只证明模型输出遵守父序列、允许位点和版本合同，不证明疫苗有效性。</div>
</main></body></html>"""


def _documents(result: dict[str, Any]) -> dict[str, str]:
    return {
        "candidate_specification.generated.json": _json_text(result["specification"]),
        "proposal_batch.json": _json_text(result["proposal_batch"]),
        "proposals.csv": _proposal_csv(result["accepted"]),
        "proposals.fasta": _proposal_fasta(result["accepted"]),
        "report.html": _report(result),
        "summary.json": _json_text(result["summary"]),
    }


def verify_stage2_model_import(directory: str | Path) -> dict[str, Any]:
    root = Path(directory).expanduser().resolve()
    errors: list[str] = []
    if not root.is_dir():
        return {"status": "fail", "identity": root.name, "errors": [f"Missing directory: {root}"]}
    actual_files = {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    }
    if actual_files != EXPECTED_FILES:
        errors.append(
            f"Artifact set differs: missing={sorted(EXPECTED_FILES - actual_files)} "
            f"unexpected={sorted(actual_files - EXPECTED_FILES)}"
        )
    if any(path.is_symlink() for path in root.rglob("*")):
        errors.append("Model-import directory may not contain symlinks")
    try:
        context = _load_object(root / "inputs/context.json", "model-import context")
        snapshots = {
            "base_specification": _load_object(root / "inputs/base_candidate_specification.json", "base specification"),
            "seed": _load_object(root / "inputs/search_seed_candidate_batch.json", "search seed"),
            "pool": _load_object(root / "inputs/search_candidate_pool.json", "search pool"),
            "jobs": _load_object(root / "inputs/external_model_jobs.json", "model jobs"),
            "results": _load_object(root / "inputs/results.json", "model results"),
        }
        input_sha256 = context.get("input_sha256")
        expected_inputs = {
            "inputs/base_candidate_specification.json",
            "inputs/external_model_jobs.json",
            "inputs/results.json",
            "inputs/search_candidate_pool.json",
            "inputs/search_seed_candidate_batch.json",
        }
        if not isinstance(input_sha256, dict) or set(input_sha256) != expected_inputs:
            errors.append("Model-import input hash inventory is incomplete")
            input_sha256 = {}
        for relative, expected_sha in input_sha256.items():
            if sha256_file(root / relative) != expected_sha:
                errors.append(f"Input snapshot hash mismatch: {relative}")
        rebuilt = _build_import(context, **snapshots)
        if rebuilt["identity"] != root.name:
            errors.append("Model-import directory name differs from recomputed identity")
        for relative, expected in _documents(rebuilt).items():
            if (root / relative).read_text(encoding="utf-8") != expected:
                errors.append(f"{relative} differs from deterministic recomputation")
        index = _load_object(root / ARTIFACT_INDEX_FILENAME, "artifact index")
        if index.get("run_id") != root.name or not _artifact_index_valid(root, index):
            errors.append("Artifact index differs from files on disk")
    except (KeyError, OSError, ValueError) as error:
        errors.append(str(error))
    return {
        "schema_version": 1,
        "identity": root.name,
        "path": str(root),
        "status": "fail" if errors else "pass",
        "errors": errors,
    }


def write_stage2_model_import(
    project_config: str | Path,
    *,
    search_dir: str | Path,
    results_path: str | Path,
    job_id: str,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    config = load_project_config(Path(project_config))
    search, search_summary = _load_search(search_dir)
    if search_summary.get("project_id") != config.project_id:
        raise ValueError("Stage 2 search belongs to another project")
    results_source = Path(results_path).expanduser().resolve()
    results = _load_object(results_source, "external-model results")
    sources = {
        "base_candidate_specification.json": search / "candidate_specification.generated.json",
        "search_seed_candidate_batch.json": search / "inputs/seed_candidate_batch.json",
        "search_candidate_pool.json": search / "candidate_pool.json",
        "external_model_jobs.json": search / "external_model_jobs.json",
        "results.json": results_source,
    }
    input_sha256 = {
        f"inputs/{name}": sha256_file(path)
        for name, path in sorted(sources.items())
    }
    context = {
        "schema_version": CONTEXT_SCHEMA,
        "project_id": config.project_id,
        "design_round_id": search_summary["design_round_id"],
        "search_identity": search_summary["search_identity"],
        "search_path": str(search),
        "search_artifact_index_sha256": sha256_file(search / ARTIFACT_INDEX_FILENAME),
        "job_id": _strict_identifier(job_id, "job_id"),
        "input_sha256": input_sha256,
        "importer": {"id": IMPORTER_ID, "version": IMPORTER_VERSION},
    }
    result = _build_import(
        context,
        _load_object(sources["base_candidate_specification.json"], "base specification"),
        _load_object(sources["search_seed_candidate_batch.json"], "search seed"),
        _load_object(sources["search_candidate_pool.json"], "search pool"),
        _load_object(sources["external_model_jobs.json"], "model jobs"),
        results,
    )
    root = (
        Path(output_root).expanduser().resolve()
        if output_root is not None
        else config.runtime_root / "input" / "stage2" / "model-imports"
    )
    root.mkdir(parents=True, exist_ok=True)
    output = root / result["identity"]
    if output.exists():
        verification = verify_stage2_model_import(output)
        if verification["status"] != "pass":
            raise ValueError("Existing model import is invalid: " + "; ".join(verification["errors"][:5]))
    else:
        temporary = Path(tempfile.mkdtemp(prefix=f".{result['identity']}.", dir=root))
        try:
            (temporary / "inputs").mkdir()
            (temporary / "inputs/context.json").write_text(_json_text(context), encoding="utf-8")
            for name, source in sources.items():
                shutil.copyfile(source, temporary / "inputs" / name)
            for relative, content in _documents(result).items():
                (temporary / relative).write_text(content, encoding="utf-8")
            index = build_artifact_index(temporary, config.project_id, result["identity"])
            (temporary / ARTIFACT_INDEX_FILENAME).write_text(_json_text(index), encoding="utf-8")
            os.replace(temporary, output)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
    verification = verify_stage2_model_import(output)
    if verification["status"] != "pass":
        raise ValueError("Model-import verification failed: " + "; ".join(verification["errors"][:5]))
    return {
        "schema_version": 1,
        "project_id": config.project_id,
        "identity": result["identity"],
        "output_dir": str(output),
        "job_id": job_id,
        "accepted_records": len(result["accepted"]),
        "skipped_records": len(result["skipped"]),
        "candidate_specification": str(output / "candidate_specification.generated.json"),
        "report": str(output / "report.html"),
        "verification_status": verification["status"],
    }
