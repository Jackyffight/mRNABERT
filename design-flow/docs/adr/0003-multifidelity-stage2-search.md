# ADR 0003: Separate Search Coverage From Expensive Evaluation

Status: accepted, 2026-07-15

Extends ADR 0002 without changing the workflow DAG or candidate authority model.

## Context

The first round-000 expansion preserved 9 supplied records and enumerated 183
order/linker controls. That batch was useful as a pipeline baseline, but it did not
search alternative truncation boundaries, independent junction linkers, or
model-generated local redesigns. Sending every newly conceivable sequence directly
to ESMFold2 would create a different failure mode: compute budget, rather than an
explicit policy, would silently define the experiment.

"All candidates" is not a finite scientific concept unless a design grammar and
budgets are frozen. Boundary coordinates, mutations, linker sequences, repeats,
orders, and model sampling seeds otherwise define an effectively unbounded space.

## Decision

Stage 2 uses a versioned multi-fidelity search funnel:

1. Preserve the complete verified baseline batch as immutable controls and lineage.
2. Generate topology-safe boundary hypotheses around manual, signal, TM, and
   structure-confidence anchors.
3. Combine source segments through declared pair, triple, and repeated-component
   architecture templates with independent per-junction linkers.
4. Apply deterministic hard constraints and transparent triage proxies based on
   retained MHC predictions, structure confidence, disorder, boundary support,
   compactness, and sequence liabilities.
5. Persist every unique candidate admitted by the bounded scoring policy as the
   eligible pool.
6. Select a smaller, diversity-preserving materialized panel for the canonical
   Stage 2 candidate schema.
7. Select a still smaller checksum-bound panel for expensive Stage 3 folding while
   retaining source/manual controls, a stratified legacy baseline, new boundaries,
   and new fusion families.
8. Emit pinned ESM3 and ProteinMPNN job requests. Model output may re-enter Stage 2
   only through an importer that verifies parent identity, model revision, mutable
   and protected positions, mutation count, sequence identity, and duplicates.

The three-protein Mock policy currently produces these distinct scales:

- 192 preserved baseline candidates;
- 36 new evidence-guided source segments;
- 24,464 unique eligible fusion sequences within the bounded scored space;
- 2,048 materialized new fusions, for 2,276 canonical Stage 2 records total;
- 384 Stage 3 selections: 9 source/manual, 48 generated baseline, 36 boundary, and
  291 new multi-family fusion candidates.

These numbers are policy outputs, not biological optimums.

## Authority And Evidence Boundaries

- Deterministic code owns enumeration, identity, hard filters, proxy calculation,
  budgets, selection, model masks, and verification.
- Existing Stage 3-5 artifacts are input evidence. They may prioritize hypotheses
  but cannot prove efficacy.
- ESM3 is a constrained local sequence proposer in this round. A job request is not
  evidence that ESM3 executed.
- ProteinMPNN remains blocked until the exact selected candidate has a verified
  Stage 3 backbone. Official and custom checkpoints are paired experimental arms.
- No model can score and release its own proposal. Imported children return to the
  normal Stage 2-7 evaluation path.
- Humans remain responsible for the scientific design policy and experimental
  release. Wet-lab evidence is required to claim superiority over manual designs.

## Consequences

Search artifacts are larger, but no admitted configuration disappears merely
because it missed an expensive-compute budget. The Stage 3 task can be reproduced
from a compact selection manifest, and a returned model sequence cannot bypass the
candidate lineage contract. Changing a boundary grid, linker library, objective
weight, model revision, or budget creates a new content identity rather than
rewriting the earlier search.

The policy remains bounded. It does not enumerate arbitrary internal antigen
mutations, every possible linker sequence, or unlimited model samples. Adding a new
proposal family requires an explicit adapter, validation contract, and policy
revision.
