#!/usr/bin/env python3
"""Build a deterministic source inventory from saved research snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def relative_snapshot(run_dir: Path, path: Path) -> dict[str, str]:
    return {
        "path": path.relative_to(run_dir).as_posix(),
        "sha256": sha256_file(path),
    }


def pmc_identifier(document: dict[str, Any]) -> str:
    identifiers = document.get("identifiers", {})
    if not isinstance(identifiers, dict):
        return ""
    value = identifiers.get("pmcid") or identifiers.get("pmc") or ""
    return str(value).upper()


def build_inventory(run_dir: Path) -> dict[str, Any]:
    retrieval = run_dir / "02-retrieval"
    pubmed_inputs = [
        ("independent-prior", retrieval / "independent-selected-pubmed.json"),
        ("direct-prior", retrieval / "direct-gold-pubmed.json"),
    ]

    direct_snapshot = load_json(pubmed_inputs[1][1])
    direct_records = direct_snapshot.get("records")
    if not isinstance(direct_records, list):
        raise ValueError(
            f"PubMed snapshot has no records array: {pubmed_inputs[1][1]}"
        )
    direct_dois = {
        str(record.get("doi", "")).lower()
        for record in direct_records
        if isinstance(record, dict) and record.get("doi")
    }

    pmc_documents: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path in sorted(retrieval.glob("PMC*.json")):
        document = load_json(path)
        pmcid = pmc_identifier(document)
        if pmcid:
            pmc_documents[pmcid] = (path, document)

    sources: list[dict[str, Any]] = []
    for arm_id, pubmed_path in pubmed_inputs:
        pubmed = load_json(pubmed_path)
        records = pubmed.get("records")
        if not isinstance(records, list):
            raise ValueError(f"PubMed snapshot has no records array: {pubmed_path}")
        for record in records:
            if not isinstance(record, dict):
                raise ValueError(f"Invalid PubMed record in {pubmed_path}")
            pmid = str(record.get("pmid", ""))
            doi = str(record.get("doi", "")).lower()
            pmcid = str(record.get("pmc", "")).upper()
            if not pmid:
                raise ValueError(f"PubMed record has no PMID in {pubmed_path}")
            if arm_id == "independent-prior" and doi and doi in direct_dois:
                raise ValueError(f"Direct-paper DOI leaked into independent arm: {doi}")

            snapshots = [relative_snapshot(run_dir, pubmed_path)]
            access_level = "abstract_only" if record.get("abstract") else "metadata_only"
            limitations = [
                "Evidence applies to the source study context and is not target-specific proof."
            ]
            pmc_entry = pmc_documents.get(pmcid)
            if pmc_entry is not None:
                pmc_path, pmc_document = pmc_entry
                snapshots.append(relative_snapshot(run_dir, pmc_path))
                raw_path = retrieval / "raw" / f"{pmcid}.xml"
                if raw_path.is_file():
                    snapshots.append(relative_snapshot(run_dir, raw_path))
                if int(pmc_document.get("section_count", 0)) > 0:
                    access_level = "full_text"
                else:
                    limitations.append(
                        "The saved PMC XML has no article body; claims are limited to PubMed abstract content."
                    )

            sources.append(
                {
                    "source_id": f"pubmed-{pmid}",
                    "arm_id": arm_id,
                    "source_type": "primary_publication",
                    "status": "retrieved",
                    "access_level": access_level,
                    "evidence_tier": (
                        "primary_full_text_snapshot"
                        if access_level == "full_text"
                        else "primary_abstract_snapshot"
                        if access_level == "abstract_only"
                        else "primary_metadata_only"
                    ),
                    "identifiers": {
                        "pmid": pmid,
                        "pmcid": pmcid,
                        "doi": doi,
                    },
                    "title": str(record.get("title", "")),
                    "journal": str(record.get("journal", "")),
                    "publication_date": str(record.get("publication_date", "")),
                    "authoritative_urls": [
                        f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                        *([f"https://doi.org/{doi}"] if doi else []),
                        *(
                            [f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/"]
                            if pmcid
                            else []
                        ),
                    ],
                    "snapshots": snapshots,
                    "limitations": limitations,
                }
            )

    genbank_path = retrieval / "raw" / "OQ555660.1.gb"
    if genbank_path.is_file():
        sources.append(
            {
                "source_id": "genbank-OQ555660.1",
                "arm_id": "independent-prior",
                "source_type": "authoritative_database_record",
                "status": "retrieved",
                "access_level": "database_record",
                "evidence_tier": "authoritative_sequence_record",
                "identifiers": {"accession": "OQ555660.1"},
                "title": "Lumpy skin disease virus isolate Kubash/KAZ/16 LSDV-KZ-Kubash",
                "journal": "NCBI GenBank",
                "publication_date": "",
                "authoritative_urls": [
                    "https://www.ncbi.nlm.nih.gov/nuccore/OQ555660.1"
                ],
                "snapshots": [relative_snapshot(run_dir, genbank_path)],
                "limitations": [
                    "This reference genome does not establish the provenance or isolate identity of the supplied Mock sequences."
                ],
            }
        )

    sources.sort(key=lambda item: str(item["source_id"]))
    access_counts: dict[str, int] = {}
    arm_counts: dict[str, int] = {}
    for source in sources:
        access = str(source["access_level"])
        arm = str(source["arm_id"])
        access_counts[access] = access_counts.get(access, 0) + 1
        arm_counts[arm] = arm_counts.get(arm, 0) + 1

    return {
        "schema_version": "vaxflow.research-source-inventory.v1",
        "skill": "exploratory-research-loop/source-inventory@v0.1",
        "status": "complete",
        "source_count": len(sources),
        "counts": {
            "by_arm": dict(sorted(arm_counts.items())),
            "by_access_level": dict(sorted(access_counts.items())),
        },
        "source_exclusion_policy": {
            "independent_prior_excluded_dois": sorted(direct_dois),
        },
        "sources": sources,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    output = args.output or run_dir / "02-retrieval" / "sources.json"
    document = build_inventory(run_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
