# Exploratory Research Loop Pilot Plan

This plan converts the current literature-assisted pilot into an evaluated system
capability without allowing an LLM to become canonical state or release authority.

## Objective

Measure whether public direct and analogous evidence can recover or improve the
supplied Mock truncation and fusion directions, and whether those findings
materially change the frozen candidate space or validation portfolio.

## Status and execution order

| Step | Status | Output | Exit condition |
| --- | --- | --- | --- |
| 1. Freeze source inventory | Complete | `02-retrieval/sources.json` | Deterministic rebuild is byte-identical; direct papers do not leak into independent arm |
| 2. Extract independent claims | In progress | `03-claims/independent-claims.proposed.json` | Every claim has an allowed source and exact location; all 25 sources have dispositions |
| 3. Extract direct claims | Pending | Separate direct-arm artifact | Independent claims and hypotheses are already frozen |
| 4. Generate hypotheses | Pending | `04-hypotheses/hypotheses.json` | Claims, transfer matches, mismatches, counterfactuals, and controls are explicit |
| 5. Evaluate candidate impact | Pending | `05-impact/candidate-impact.json` | Every hypothesis is covered, under-covered, absent, contradicted, or blocked |
| 6. Produce readiness report | Pending | Bilingual HTML and Markdown | Recovery, precision, unsupported-edge, novelty, and decision-impact metrics are visible |
| 7. Validate and compare models | Pending | Regression fixtures and benchmark table | Strong LLM, local 7B, and deterministic baseline use identical frozen inputs |

## Evidence-arm discipline

The independent arm excludes:

- `10.1038/s41590-023-01715-7`;
- `10.1016/j.ebiom.2024.105392`;
- the hidden Mock evaluation document.

Independent claims and hypotheses are frozen before the direct arm opens. This
tests recovery from analogous public evidence rather than the ability to summarize
the disclosed answer papers.

## Claim-extraction benchmark

Run three controlled arms against the same 25 independent sources:

1. a strong general LLM;
2. a local 7B instruction model;
3. a deterministic keyword/relevance baseline.

Long documents are processed per source. Cross-source normalization and duplicate
handling occur only after source-level extraction. The local 7B model is evaluated
first as a bounded extractor, not as an unrestricted scheduler or hypothesis owner.

Primary metrics:

- valid JSON/schema rate;
- valid source and evidence-location rate;
- claim entailment precision;
- authors-own-result attribution precision;
- unsupported design-implication rate;
- source-disposition completeness;
- direct-paper leakage rate;
- repeated-run agreement;
- latency and compute cost.

Secondary research metrics:

- recovery of hidden Mock design directions;
- accepted novel-hypothesis rate;
- candidate families added or reprioritized;
- portfolio stability after proposed evidence;
- human review minutes per decision-changing result.

## State transitions

Research records follow:

```text
llm_proposed
  -> evidence_attached
  -> confirmed | rejected | waived
  -> promoted_to_rule (only when recurring and regression-tested)
```

No unreviewed claim may alter a hard gate. Hypotheses may produce a versioned
grammar-patch proposal, but only an accepted patch creates a new immutable design
round.

## Near-term deliverables

1. Recover or rerun the independent claim output while preserving the raw event log.
2. Add deterministic claim validation for source IDs, locations, arm leakage, and
   source-disposition completeness.
3. Freeze the strong-model output as a benchmark candidate, not as gold truth.
4. Run the same extraction contract with a local 7B model.
5. Adjudicate a high-impact sample and estimate precision before hypothesis work.
6. Continue to hypothesis and candidate-impact analysis only if source-grounded
   precision is acceptable.

The plan deliberately delays a learned scheduler. Action-ranking supervision is
not credible until these traces contain adjudicated outcomes.
