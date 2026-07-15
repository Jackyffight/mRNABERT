# Design-Flow Architecture

Status: frozen architecture baseline v2

Normative companion documents:

- [Workflow v2](docs/workflow-v2.md) and its frozen machine contract;
- [Audit Automation and LLM Governance](docs/audit-automation-and-llm-governance.md);
- [ADR 0002](docs/adr/0002-round-based-design-optimization.md).
- [ADR 0003](docs/adr/0003-multifidelity-stage2-search.md).

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

## Central-Kitchen Mental Model

> [!IMPORTANT]
> The system is a traceable central kitchen, not an autonomous scientist. The
> current nine records are antigen recipes that can be inspected and processed;
> they are not nine proven vaccines. A computationally complete recipe is not proof
> that the physical product can be manufactured, is safe, or is protective.
>
> 本系统是一套可追溯的中央厨房，而不是自主科学家。当前 9 条记录是可以审计和加工的
> 抗原菜谱，不是 9 个已经验证的疫苗。数字菜谱完整，不等于实物一定能生产、安全或有效。

The project owner is the restaurant owner: they define the product objective and
accept program assumptions. Domain scientists are the chefs: they approve antigen
boundaries, target populations, evidence policies, product details, and experiments.
The deterministic workflow is the kitchen line: it checks identities, follows exact
recipes, operates pinned instruments, records every result, and refuses to turn a
missing measurement into a favorable conclusion. The LLM is a review assistant; it
may explain or propose findings, but it cannot invent scientific evidence or release
a candidate.

| Stage | Kitchen question | What the system can establish | What still requires evidence or authority |
|---|---|---|---|
| 1 Source intake | Did the ordered ingredients arrive, and do package and contents agree? | AA/CDS identity, translation, syntax, hashes, and declared provenance gaps | Whether the biological source and program choice are scientifically appropriate |
| 2 Candidate specification | What exact recipes are on the menu? | Exact full-length, truncation, fusion, component, order, tag, and linker records | Whether boundaries, linkers, combinations, and design-space coverage are desirable |
| 3 Structure assessment | Does each trial dish appear to hold its intended shape? | Pinned predicted structures, confidence, geometry, boundary flags, and artifact identity | Actual folding, expression, stability, function, and acceptance of structural exceptions |
| 4 Immune evidence | Can pieces of a dish be presented on the selected customers' immune "plates"? | Reproducible predictions for declared pathogen, host, and MHC inputs | Representative cattle population, evidence policy, biological interpretation, and immune protection |

The Stage 4 smoke run used the complete recipe batch but only one BoLA-I and one
BoLA-II technical allele. It therefore proves that the kitchen can process every
recipe type and preserve evidence; it does not prove population coverage or immune
quality. Missing evidence is frozen as `needs_data` or `not_evaluated`. Later data
creates a new immutable run rather than rewriting the earlier record.

Stages 5-7 continue the analogy by checking whether recipes look developable,
turning antigens into exact recombinant-protein and mRNA product specifications,
and producing a provisional evidence-based ordering. Only controlled production
and wet-lab testing actually cook and evaluate the products.

## Progressive Project Dossier

> [!IMPORTANT]
> A project is not opened with one exhaustive questionnaire, and it is not steered
> by an LLM making hidden decisions. Design Flow maintains an executable dossier:
> a small required intake, versioned conditional profiles, stage-due questions,
> and traceable proposals for genuinely new issues.

Questions enter the dossier through four controlled routes:

1. **Core intake:** the minimum facts needed to identify the program, host,
   pathogen, modalities, source records, owners, and Mock or scientific-use policy.
2. **Conditional profiles:** versioned host, modality, expression, delivery, and
   assay templates activated only when their applicability conditions are met.
3. **Stage-triggered requirements:** questions created by deterministic contracts or
   model results when a downstream stage first needs an answer.
4. **Emergent audit proposals:** novel contradictions or missing assumptions raised
   by a human or LLM auditor, with evidence and provenance, but no automatic authority.

Every question has a stable ID, trigger and provenance, owner, current state,
`required_before_stage`, resolution or waiver, and linked evidence. A question may
remain open while unrelated work continues. It becomes blocking only at the stage
whose calculation or release decision actually depends on it. Resolved answers are
carried forward and are not asked again unless an input or applicable profile changes.

The deterministic system must remain executable without an LLM. In that mode,
open-ended audit is explicitly `not_evaluated`; known contracts and stage questions
still work. An LLM may propose and explain a new checkpoint, but a scientist or an
authorized deterministic rule must confirm, reject, or waive it. Recurring confirmed
questions should be promoted into the versioned questionnaire, profile registry, or
rule registry with regression tests.

The current implementation already stores project `human_actions`, stage deadlines,
states, owners, resolutions, and handoff propagation. The complete product still
needs action trigger/provenance fields, a conditional question catalog, reusable
profile registry, guided project UI, and a standard adapter that converts LLM
findings into reviewable proposed actions.

## Candidate Lineage

Every candidate receives an immutable biological construct ID. Proposal provenance is
stored separately so an identical construct does not change identity when proposed by
a different generator. Every proposal records:

- source proteins and residue ranges;
- domain order and orientation;
- linker identities and positions;
- additions or removals such as tags, signal peptides, or cleavage sites;
- the generator and parameters that proposed it.
- the design round, rationale, parent candidate IDs, and consumed feedback requests.

No downstream stage may silently rewrite a candidate. A changed sequence creates
a new child candidate in a later immutable round.

## Design Optimization Loop

Design begins at Stage 1. Each round freezes `design_brief.json`,
`design_variable_registry.json`, and `objective_policy.json`; Stage 2 forms a proposal
pool; Stages 3-6 evaluate it and emit structured redesign requests. Accepted requests
become inputs to a later round that restarts at Stage 1. The executable run DAG remains
acyclic and immutable. See [Round-Based Design Optimization](docs/round-based-design-optimization.md).

## Stages

`src/design_flow/workflow.py` is the canonical route. It defines one entry, one
terminal node, and two modality branches that rejoin before experiment release:

1. **Program definition and source intake (implemented)**
2. **Candidate specification and generation (manual import, grammar controls, evidence-guided multi-family search, model-job contracts, and constrained model-output import implemented)**
3. **Protein structure assessment (exploratory ESMFold2 path implemented)**
4. **Immune evidence assessment (deterministic partial-evidence path implemented)**
5. **Developability and manufacturability assessment (deterministic intrinsic and adapter path implemented)**
6. **Recombinant protein product design (6A, exploratory path implemented)**
7. **mRNA product design (6B, synonymous-design and adapter path implemented)**
8. **Integrated ranking and portfolio selection (provisional, unreleased path implemented)**
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
capabilities, and audit contracts. It says what the complete system will do while
implemented evidence accumulates through the first three nodes.

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
- **M1:** candidate schema, manual controls, grammar-bounded controls, evidence-guided
  boundary/linker/architecture search, multi-fidelity selection, constrained model
  proposal import, lineage, deduplication, and proposal reports are implemented;
  scientific batch approval remains open.
- **M2:** one structure-prediction adapter and a structure comparison report;
  exploratory ESMFold2-Fast execution, deterministic geometry, and audited import are implemented.
- **M3:** developability adapters and transparent multi-objective ranking.
- **M4:** protein-expression and mRNA-design branches.
- **M5:** experiment manifest, assay schema, and first closed learning loop.

The current Stage 2 search freezes the bounded eligible pool separately from the
materialized panel and Stage 3 compute selection. The next execution target is the
checksum-bound Stage 3 selection, followed by structure-backed ProteinMPNN jobs and
the first model-proposal import. Those adapters and the first `round-001` execution
must use the same lineage, protection masks, control retention, deduplication, and
immutable-round contracts as the deterministic generator.
