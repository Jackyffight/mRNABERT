# Design-Flow Architecture

## Goal

Build a reproducible design-build-test-learn system for evaluating original
antigens, single-protein constructs, and multi-protein fusion constructs. Protein
and mRNA vaccines share the candidate protein definition and protein-level
assessment. They diverge only when a delivery-specific sequence and formulation
must be designed.

This repository is an engineering and evidence pipeline. A model score is one
piece of evidence, not proof of safety or efficacy. Advancement decisions require
predefined gates and experimental confirmation.

## Candidate Lineage

Every candidate receives an immutable ID derived from its normalized sequence and
parentage. A future fusion candidate must also record:

- source proteins and residue ranges;
- domain order and orientation;
- linker identities and positions;
- additions or removals such as tags, signal peptides, or cleavage sites;
- the generator and parameters that proposed it.

No downstream stage may silently rewrite a candidate. A changed sequence creates
a new candidate with an explicit parent.

## Stages

1. **Intake and sequence audit (implemented)**
   Pair AA and CDS records, translate CDS, detect hard input errors, calculate
   baseline descriptors, hash inputs, and write a manifest.
2. **Candidate generation**
   Create originals, manual controls, single-protein variants, and fusion variants
   under an explicit construct grammar. Keep generation separate from ranking.
3. **Protein structure assessment**
   Run pluggable structure predictors, confidence extraction, refolding checks,
   interface/domain preservation checks, and structural failure gates.
4. **Developability and sequence risk assessment**
   Add aggregation, solubility, disorder, topology, manufacturability, and other
   relevant predictors. Record model versions and calibrated uncertainty.
5. **Delivery-specific design**
   For recombinant protein, add expression and purification constraints. For mRNA,
   optimize synonymous CDS and non-coding design constraints while preserving the
   exact candidate protein sequence.
6. **Candidate ranking**
   Combine hard gates, calibrated task models, uncertainty, and diversity. Keep
   every component score visible; do not collapse evidence into an unexplained
   single number.
7. **Experiment design**
   Select candidates plus positive, negative, original, and manual controls. Emit
   a blinded sample sheet and freeze the candidate manifest before testing.
8. **Assay ingestion**
   Import raw and processed measurements with assay protocol, batch, replicate,
   units, and QC. Never overwrite observations.
9. **Learning**
   Fit task-specific heads and recalibrate rankings only after leakage-safe splits
   and baseline comparisons. Preserve old model versions and predictions.

## Stage Contract

Each stage adapter will receive a run manifest and candidate records, then write a
new immutable stage directory containing:

- `stage_manifest.json` with tool, model, revision, parameters, hardware, and time;
- `results.jsonl` keyed by candidate ID;
- optional large artifacts referenced by checksum and relative path;
- explicit `pass`, `fail`, `warning`, `not_evaluated`, or `error` state;
- logs sufficient to reproduce or diagnose the run.

Adapters must not depend on one storage backend. Local disk, NAS, and object-store
publication belong behind artifact-store interfaces, not inside model adapters.

## Source And Runtime Boundary

The Git project contains code, portable project specifications, schemas, tests,
and empty templates only. Every project specification names an absolute external
`runtime_root`. Real sequences, intermediate model files, logs, reports, manifests,
and published results live below that root. Configuration loading rejects a runtime
root inside the source project, preventing generated data from leaking into Git.

The current local root is `/data00/home/wangzhi.wit/models/design-flow-runtime`, a
sibling of `datasets`. A different machine must use its own explicit absolute path;
runtime locations are never inferred from environment variables.

## Workflow And Node Reports

The complete workflow definition is a system blueprint, not a monolithic report.
Every run writes `workflow.json` as the future UI graph: node IDs, dependencies,
capabilities, and audit contracts. It says what the complete system will do even
when only the first node has been implemented.

Executed evidence accumulates one node at a time:

```text
runs/<run-id>/
  manifest.json
  workflow.json
  nodes/
    <stage-id>/
      summary.json
      report.md
      input_audit.json
      process_record.json
      output_audit.json
      human_actions.json
      handoff.json
```

`summary.json` is the compact future UI node card. `report.md` is that node's detail
view, not a repetition of the whole workflow. The three audit records preserve what
entered the node, what actually happened, and what was released. Human actions have
owners, statuses, blocking stages, and resolutions. `handoff.json` carries candidate
IDs, hashes, findings, and unresolved actions into the next node.

As implementation and experiments progress, the run gains additional node folders
and therefore increasingly complete evidence. Previous node reports remain
immutable; a correction creates a new run or an explicitly versioned node attempt.

## Initial Milestones

- **M0:** sequence intake and reproducible audit, now implemented.
- **M1:** candidate schema plus manually supplied original and fusion constructs.
- **M2:** one structure-prediction adapter and a structure comparison report.
- **M3:** developability adapters and transparent multi-objective ranking.
- **M4:** protein-expression and mRNA-design branches.
- **M5:** experiment manifest, assay schema, and first closed learning loop.

The immediate next input is the three original proteins. M1 should begin only
after M0 confirms exactly which nucleotide sequence encodes each supplied protein.
