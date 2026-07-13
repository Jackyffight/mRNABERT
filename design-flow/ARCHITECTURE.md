# Design-Flow Architecture

Status: frozen architecture baseline v1

Normative companion documents:

- [Workflow v1](docs/workflow-v1.md) and its frozen machine contract;
- [Audit Automation and LLM Governance](docs/audit-automation-and-llm-governance.md);
- [ADR 0001](docs/adr/0001-hybrid-audited-workflow.md).

The executable workflow is versioned in `src/design_flow/workflow.py`. A semantic
architecture or workflow change requires a superseding ADR, explicit version
increment, a new frozen workflow document, migration notes, and updated tests.

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

`src/design_flow/workflow.py` is the canonical route. It defines one entry, one
terminal node, and two modality branches that rejoin before experiment release:

1. **Program definition and source intake (implemented)**
2. **Candidate specification and generation**
3. **Protein structure assessment**
4. **Immune evidence assessment**
5. **Developability and manufacturability assessment**
6. **Recombinant protein product design (6A)**
7. **mRNA product design (6B)**
8. **Integrated ranking and portfolio selection**
9. **Experiment design and release**
10. **Assay ingestion and quality control**
11. **Learning, calibration, and next-round design**

The validator rejects duplicate IDs or orders, unknown dependencies, cycles,
additional entry nodes, unreachable nodes, multiple terminal nodes, and empty
audit contracts. Iteration starts a new immutable run rather than adding a cycle
inside one run DAG.

## Deterministic Execution

The execution core is a deterministic state machine. Stage dependencies, status
transitions, hard gates, artifact identities, and canonical records are computed
from structured evidence. Scientific models run through pinned adapters; they do
not schedule the workflow or reinterpret provenance.

The complete product also includes an LLM audit plane for open-ended review of
unstructured evidence and previously unseen failure modes. It is a first-class
auditor but not a release authority: its findings remain proposals until evidence
is attached and they are confirmed, rejected, or waived. Repeated confirmed
findings should become versioned deterministic rules with regression tests.

The deterministic core can run offline without an LLM and must mark the LLM audit
as `not_evaluated`. A full offline deployment uses an approved local LLM. See
[Audit Automation and LLM Governance](docs/audit-automation-and-llm-governance.md)
for roles, provenance, deployment modes, and the rule-promotion lifecycle.

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
  artifact_index.json
  manifest.json
  workflow.json
  inputs/
    project.json
    proteins_aa.fasta
    proteins_cds.fasta
  nodes/
    <stage-id>/
      summary.json
      report.html
      input_audit.json
      process_record.json
      output_audit.json
      human_actions.json
      handoff.json
```

`summary.json` is the compact future UI node card. `report.html` is that node's
self-contained bilingual detail view, not a repetition of the whole workflow. The
three audit records preserve what entered the node, what actually happened, and what was released. Human actions have
owners, statuses, blocking stages, and resolutions. `handoff.json` carries candidate
IDs, hashes, findings, and unresolved actions into the next node.

Each run snapshots its exact source configuration and FASTA inputs. The artifact
index records SHA-256 and byte size for every snapshot and generated artifact. A
cross-file verifier checks run/stage identity, DAG contents, input digests,
candidate IDs and statuses, QC counts, CSV/JSON equality, human gates, and handoff
provenance. `latest.json` is updated only after this verification passes. A
biologically invalid input may still produce a valid `blocked` audit run; artifact
integrity and scientific acceptance are deliberately separate states.

As implementation and experiments progress, the run gains additional node folders
and therefore increasingly complete evidence. Previous node reports remain
immutable; a correction creates a new run or an explicitly versioned node attempt.

## Initial Milestones

- **M0:** sequence intake and reproducible audit, now implemented.
- **M1:** candidate schema plus manually supplied original and fusion constructs;
  provisional reconciliation is implemented, while grammar and batch approval remain open.
- **M2:** one structure-prediction adapter and a structure comparison report.
- **M3:** developability adapters and transparent multi-objective ranking.
- **M4:** protein-expression and mRNA-design branches.
- **M5:** experiment manifest, assay schema, and first closed learning loop.

The immediate next input is the three original proteins. M1 should begin only
after M0 confirms exactly which nucleotide sequence encodes each supplied protein.
