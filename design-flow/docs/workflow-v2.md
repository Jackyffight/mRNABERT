# Vaccine Design Workflow v2

Status: frozen round-based optimization contract

- System architecture version: `2`
- Workflow ID: `vaccine-design-build-test-learn`
- Workflow version: `2`
- Entry stage: `program_and_source_intake`
- Contract SHA-256: `a5e858dba9ae2c4d480f9c2b1661ed79138211f4e5e99157dce3b1f6aef30b0c`
- Executable source: `src/design_flow/workflow.py`
- Frozen machine contract: `docs/workflow-v2.json`
- Decision record: `docs/adr/0002-round-based-design-optimization.md`

## Change From V1

The DAG order is retained, but candidate innovation is no longer deferred to product
design. Stage 1 freezes each round's objectives and searchable variables. Stage 2
forms a proposal pool with explicit lineage. Stages 3-6 evaluate proposals and emit
reviewable feedback. Accepted feedback starts a new immutable round at Stage 1.

## Canonical DAG

The graph remains acyclic within one run:

`program_and_source_intake` -> `candidate_specification` ->
`protein_structure_assessment`, `immune_evidence_assessment`, and
`developability_assessment` -> `protein_product_design` and
`mrna_product_design` -> `integrated_ranking` -> `experiment_release` ->
`assay_ingestion` -> `learning_and_iteration`.

The exact stage capabilities, dependencies, and audit contracts are frozen in
`workflow-v2.json`. Historical workflow-v1 runs continue to verify against the v1
hash and are not reinterpreted as v2 runs.

## Round Contract

Every v2 Stage 1 run requires a design brief, variable registry, and objective policy.
Every v2 Stage 2 candidate carries proposal provenance. Evaluation findings may create
`redesign_requests.json`; missing data alone does not imply a sequence edit. A request
is consumed only after review and only by a later immutable round.

## Authority

Models generate or score through pinned adapters. The deterministic core records
identity and applies declared rules. LLM output is attributed review material. Human
owners approve objectives, scientific assumptions, release gates, and experiments.

The workflow emits computational evidence, not proof of manufacturability, safety,
immunogenicity, or efficacy.
