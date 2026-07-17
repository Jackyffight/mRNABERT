# Research System Boundary Record

Date: 2026-07-17

Project: `three-protein-vaccine`, Mock workflow validation only

## Current conclusion

VaxFlow currently contains two capabilities at different maturity levels:

1. **Stage 1-7 is a replayable computational system.** Versioned Python owns
   input identity, candidate generation, model adapters, evidence binding, state,
   product branching, ranking, reports, and verification.
2. **Open-ended research is an audited LLM workflow prototype.** Source retrieval
   is reproducible, but claim extraction, analogy transfer, hypothesis generation,
   and scientific applicability still depend on model reasoning.

Writing an LLM answer to JSON makes it attributable; it does not make the answer
deterministic or scientifically true. Research outputs therefore enter as
`llm_proposed` records and cannot mutate candidates, evidence, gates, ranks, or
release state directly.

## Current maturity

| Capability | Maturity | Meaning |
| --- | --- | --- |
| Stage 1-7 deterministic workflow | L2 | Schema-bound, replayable, independently verified |
| Research source inventory | L2 | Rebuilt byte-identically from saved snapshots |
| LLM claim extraction | L1 | Raw model run exists; accepted proposed-claim artifact is not frozen |
| Analogy and hypothesis generation | L1/not started | Requires model reasoning and adjudication |
| Cross-model research benchmark | Planned L3 | Strong model, local 7B model, and deterministic baseline |
| Wet-lab-calibrated decision system | Not started | No assay labels have been ingested |

The maturity labels used here are:

- **L0:** conversational analysis with no stable contract;
- **L1:** an audited LLM workflow with frozen inputs and raw outputs;
- **L2:** a schema-validated, replayable system capability;
- **L3:** a capability evaluated across models, repeats, and hidden targets;
- **L4:** a capability calibrated against real experimental outcomes.

## Why atomic claim extraction is not only classification

Parts of the task are suitable for a small model or conventional classifier:

- source relevance;
- research-question assignment;
- evidence-class and polarity labels;
- source disposition and duplicate detection.

Other parts require semantic extraction or reasoning:

- locating the authors' own result rather than a cited background statement;
- decomposing one paragraph into individually supportable propositions;
- preserving negation, controls, host, modality, and assay context;
- distinguishing direct target evidence from a transferred prior;
- identifying attractive but unsupported design implications.

A local 7B instruction model is therefore a plausible first-pass extractor, but it
is not accepted as sufficient without a controlled comparison. Analogy transfer and
hypothesis generation remain higher-risk tasks and should be escalated separately.

## Research pilot status

Run:
`20260717T045258Z-research-pilot-2e941648`

The source inventory is complete and deterministically reproducible:

| Evidence inventory | Count |
| --- | ---: |
| Independent-prior sources | 25 |
| Direct-prior sources | 2 |
| Full-text snapshots | 7 |
| Abstract-only records | 19 |
| GenBank records | 1 |
| **Total** | **27** |

The two disclosed answer papers are absent from the independent arm. A raw Codex
claim-extraction event stream exists, but the required
`independent-claims.proposed.json` artifact is absent. Claim extraction is therefore
**attempted, not complete**. Hypothesis and candidate-impact directories are empty.

## Authority boundary

| Layer | May do | May not do |
| --- | --- | --- |
| Deterministic Python | Validate, compute, bind, replay, schedule eligible actions | Invent missing scientific facts |
| Scientific models | Produce pinned, checksum-bound evidence | Approve efficacy or release |
| General or local LLM | Propose claims, hypotheses, checks, and grammar changes | Write canonical facts or silently change candidates |
| Human reviewer | Adjudicate high-impact proposals and approve release inputs | Erase prior immutable evidence |

The immediate objective is not to prove that a strong LLM writes good prose. It is
to measure how much of the research operation can be assigned to a smaller local
model while retaining citation precision, unsupported-claim control, and hidden
design-direction recovery.

## Presentation snapshot

The latest Stage 1-7 execution has moved beyond the earlier 2026-07-16 closed-loop
report:

- Stage 7 executed in exploratory mode;
- 104 modality-specific ranking rows cover 52 candidates;
- 8 provisional slots represent 4 unique candidates;
- the formal portfolio remains empty;
- Stage 6B contains 1,543 mRNA designs;
- 215 designs over the routed 52-candidate subset have Evo2 evidence.

These are computational and system-engineering results. They do not establish
expression success, immunogenicity, safety, cattle protection, or experimental
readiness.
