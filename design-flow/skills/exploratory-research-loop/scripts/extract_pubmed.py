#!/usr/bin/env python3
"""Convert a PubMed EFetch XML snapshot into deterministic JSON records."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET


def text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return "".join(element.itertext()).strip()


def record(article: ET.Element) -> dict[str, object]:
    citation = article.find("MedlineCitation")
    if citation is None:
        raise ValueError("PubmedArticle is missing MedlineCitation")
    article_node = citation.find("Article")
    if article_node is None:
        raise ValueError("MedlineCitation is missing Article")
    identifiers = {
        item.attrib.get("IdType", "unknown"): text(item)
        for item in article.findall("./PubmedData/ArticleIdList/ArticleId")
    }
    abstract = []
    for item in article_node.findall("./Abstract/AbstractText"):
        value = text(item)
        if value:
            abstract.append(
                {
                    "label": item.attrib.get("Label", ""),
                    "text": value,
                }
            )
    journal_issue = article_node.find("./Journal/JournalIssue")
    publication_date = ""
    if journal_issue is not None:
        date = journal_issue.find("PubDate")
        if date is not None:
            publication_date = " ".join(
                value
                for value in (
                    text(date.find("Year")),
                    text(date.find("Month")),
                    text(date.find("Day")),
                    text(date.find("MedlineDate")),
                )
                if value
            )
    return {
        "pmid": text(citation.find("PMID")),
        "doi": identifiers.get("doi", ""),
        "pmc": identifiers.get("pmc", ""),
        "title": text(article_node.find("ArticleTitle")),
        "journal": text(article_node.find("./Journal/Title")),
        "publication_date": publication_date,
        "publication_types": [
            text(item) for item in article_node.findall("./PublicationTypeList/PublicationType")
        ],
        "abstract": abstract,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_xml", type=Path)
    parser.add_argument("output_json", type=Path)
    args = parser.parse_args()
    payload = args.input_xml.read_bytes()
    root = ET.fromstring(payload)
    records = sorted(
        (record(article) for article in root.findall("PubmedArticle")),
        key=lambda item: str(item["pmid"]),
    )
    output = {
        "schema_version": "vaxflow.pubmed-snapshot.v1",
        "source": "NCBI PubMed EFetch",
        "input_sha256": hashlib.sha256(payload).hexdigest(),
        "record_count": len(records),
        "records": records,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(output, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
