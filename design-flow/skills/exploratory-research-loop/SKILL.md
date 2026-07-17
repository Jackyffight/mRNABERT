---
name: exploratory-research-loop
description: Runs a lightweight, evidence-traceable research loop from incomplete project context through public-prior retrieval, claim extraction, analogy transfer, hypothesis generation, and candidate-impact review. Use when an exploratory Design Flow project needs research-driven hypotheses without a heavy agent framework.
---

# Exploratory Research Loop

## Quick Start

1. Freeze the minimum project context and exact input hashes.
2. Declare hidden evaluation material and source-exclusion rules before retrieval.
3. Execute each workflow step as an append-only event.
4. Store raw queries, source identities, extracted claims, and rejected claims.
5. Return proposals only; never mutate canonical candidates or stage evidence.

## Workflow

### 1. Context Audit

- Record target, host, modalities, source identities, hard constraints, and known gaps.
- Separate supplied facts, Mock declarations, hidden gold standards, and unresolved context.
- Define the research question and comparison arms before searching.

### 2. Evidence-Gap Decomposition

- Convert each missing decision into one or more answerable research questions.
- Label direct evidence, transferred prior, mechanistic prior, model prediction,
  and exploratory hypothesis separately.

### 3. Public-Prior Retrieval

- Preserve every query, date, endpoint, filter, and failed request.
- Prefer primary papers and authoritative databases.
- Snapshot identifiers and metadata; do not rely on an LLM citation alone.

### 4. Claim Extraction

- Store one scoped claim per record with source, quoted evidence location, context,
  polarity, and limitations.
- Reject claims whose source does not support the asserted design implication.

### 5. Analogy Transfer

- Map source pathogen, protein, host, modality, construct operation, and assay to the
  target project.
- Record both transferable dimensions and mismatches.
- Treat transferred evidence as a prior, never as target-specific proof.

### 6. Hypothesis Generation

- Generate a reference-mapped hypothesis, local-neighborhood variants,
  counterfactuals, controls, and at least one diversity branch.
- Bind every hypothesis to claims or mark it explicitly unsupported.

### 7. Candidate Impact

- Compare hypotheses with the current candidate grammar and active pool.
- Classify each as already covered, under-covered, absent, contradicted, or blocked.
- Propose a versioned grammar patch; do not apply it automatically.

### 8. Readiness Review

- Measure recovered hidden decisions, new supported hypotheses, unsupported edge
  rate, human input used, and expected downstream decision impact.
- Finish as `ready_for_review`, `needs_more_research`, or `not_supported`.

## Required Artifacts

- `manifest.json`
- `events.jsonl`
- `00-intake/context.json`
- `00-intake/hidden-gold-standard.json`
- `01-gaps/research-questions.json`
- `02-retrieval/queries.jsonl` and `sources.json`
- `03-claims/claims.json`
- `04-hypotheses/hypotheses.json`
- `05-impact/candidate-impact.json`
- `report.md` and `report.html`

Every artifact records the Skill version, input hashes, provenance class, and
status. Runtime evidence stays outside Git; only reusable Skill instructions and
system documentation are tracked.

Use `scripts/extract_pubmed.py` to convert a saved PubMed EFetch XML response into
deterministic JSON before claim extraction.

Use `scripts/extract_pmc.py` to convert a saved PMC JATS XML article into
deterministic, section-addressable JSON before citing methods or results.

Use `scripts/build_source_inventory.py` after retrieval to freeze source identities,
access levels, snapshot hashes, evidence arms, and source limitations.

Use `scripts/evaluate_candidate_impact.py` only after hypotheses and their explicit
coverage rules are frozen. Its counts describe syntactic search/routing coverage,
not biological equivalence or efficacy.
