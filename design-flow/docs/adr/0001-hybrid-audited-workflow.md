# ADR 0001: Freeze a Hybrid Audited Workflow

Status: accepted, 2026-07-13

## Context

The project combines exact sequence processing, specialized scientific models,
unstructured project evidence, human decisions, and future wet-lab labels. A
purely deterministic system cannot anticipate every cross-document or biological
ambiguity. A free-running LLM cannot provide stable identity, provenance, or
release authority. Keeping the route only in conversation would allow the system
to drift toward whichever model or output is most convenient at the time.

## Decision

Freeze system architecture version 1 and workflow version 1 with:

- a deterministic execution and provenance core;
- versioned rules for known failure modes;
- a high-freedom, evidence-producing LLM auditor;
- human adjudication and release authority;
- one immutable design-to-learning DAG with protein and mRNA branches;
- checked-in human and machine workflow specifications protected by tests.

The canonical artifacts are:

- `ARCHITECTURE.md` for system boundaries;
- `docs/workflow-v1.md` for the reviewed route;
- `docs/workflow-v1.json` for the frozen machine contract;
- `docs/audit-automation-and-llm-governance.md` for audit authority;
- `src/design_flow/workflow.py` for executable definitions.

## Consequences

The deterministic core remains operational offline. Full semantic coverage uses
an approved local or remote LLM auditor and visibly records when that audit is not
evaluated. LLM proposals cannot silently become facts. Repeated confirmed
findings should move into rules, but the LLM remains to search for unknown failure
modes.

Workflow changes now incur explicit versioning and migration work. This is
intentional: historical runs must retain their original meaning, and future
implementation must conform to the agreed product route rather than redefine it.

## Supersession

This ADR is not edited to authorize a different workflow or authority model. A
future change must add a new ADR that explicitly supersedes ADR 0001.
