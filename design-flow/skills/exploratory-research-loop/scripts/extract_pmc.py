#!/usr/bin/env python3
"""Convert a saved PMC JATS XML article into deterministic section records."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET


def text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return " ".join("".join(element.itertext()).split())


def section_records(section: ET.Element, parents: tuple[str, ...]) -> list[dict[str, object]]:
    title = text(section.find("title")) or "Untitled section"
    path = (*parents, title)
    paragraphs = [value for node in section.findall("./p") if (value := text(node))]
    records: list[dict[str, object]] = []
    if paragraphs:
        records.append({"section_path": list(path), "paragraphs": paragraphs})
    for child in section.findall("./sec"):
        records.extend(section_records(child, path))
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_xml", type=Path)
    parser.add_argument("output_json", type=Path)
    args = parser.parse_args()

    payload = args.input_xml.read_bytes()
    root = ET.fromstring(payload)
    article = root.find(".//article")
    if article is None:
        raise ValueError("PMC XML does not contain an article")

    identifiers = {
        node.attrib.get("pub-id-type", "unknown"): text(node)
        for node in article.findall(".//article-meta/article-id")
    }
    body = article.find("body")
    sections: list[dict[str, object]] = []
    if body is not None:
        direct_paragraphs = [value for node in body.findall("./p") if (value := text(node))]
        if direct_paragraphs:
            sections.append({"section_path": ["Body"], "paragraphs": direct_paragraphs})
        for section in body.findall("./sec"):
            sections.extend(section_records(section, ()))

    output = {
        "schema_version": "vaxflow.pmc-snapshot.v1",
        "source": "NCBI PubMed Central EFetch",
        "input_sha256": hashlib.sha256(payload).hexdigest(),
        "identifiers": identifiers,
        "title": text(article.find(".//article-meta/title-group/article-title")),
        "section_count": len(sections),
        "sections": sections,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(output, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
