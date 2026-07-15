# ADR 0002: Start Design Optimization At Program Intake

Status: accepted, 2026-07-15

Supersedes the workflow semantics of ADR 0001 while retaining its deterministic,
LLM-audited, human-authorized governance model.

## Context

Workflow v1 treated Stages 1-5 primarily as a quality-control line and deferred
meaningful product optimization until mRNA product design. That ordering can audit
a fixed menu but cannot represent an innovation program: objectives, tradeoffs,
searchable variables, generator choices, and feedback must exist before the first
candidate pool is formed.

Putting a free-running cycle inside one run would create a different problem. A
model could silently rewrite its own inputs, erase rejected candidates, or move a
threshold after seeing results. Historical evidence would no longer have stable
meaning.

## Decision

Adopt system architecture version 2 and workflow version 2:

1. Stage 1 freezes a versioned design brief, objective policy, variable registry,
   source controls, and any accepted feedback from an earlier round.
2. Stage 2 imports or generates a proposal pool. Every candidate records its round,
   generator, parameters, parents, transformation, rationale, and consumed feedback.
3. Stages 3-6 remain specialized evaluators. They may emit structured redesign
   requests, but they cannot mutate a candidate.
4. Ranking, experiments, and learning may accept, reject, or defer requests. An
   accepted request becomes input to a new immutable round that restarts at Stage 1.
5. Biological candidate identity is derived from the construct. Proposal provenance
   is separate, so the same construct is not assigned a new identity merely because
   a different tool proposed it.

The current nine candidates are registered as the seed population for `round-000`.
They validate the proposal/evaluation route; they are not claimed to be an optimized
or exhaustive design space.

## Authority Boundaries

- Deterministic code owns identity, schemas, state, hashes, hard constraints, and
  exact reproducibility.
- Pinned scientific models may generate candidates or produce evidence through
  explicit adapters.
- An LLM may propose objectives, variables, interpretations, or redesign requests,
  but its output remains a reviewable proposal with provenance.
- Humans approve the design contract, scientific assumptions, release gates, and
  experimental portfolio.

Missing evidence is not a redesign request. A request requires an explicit rule,
model result, human decision, or attributed LLM proposal. No component may convert
`not_evaluated` into favorable evidence.

## Consequences

The executable graph remains acyclic within a run. Optimization is a sequence of
immutable rounds connected by lineage and feedback. This costs more artifacts and
requires explicit migration, but preserves the ability to reproduce both successful
and rejected decisions.

Workflow v1 artifacts remain valid under their original approved contract hash.
New runs use workflow v2 and require the three Stage 1 design-contract inputs.
