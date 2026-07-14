# Audit Automation and LLM Governance

Status: accepted system direction, 2026-07-13

## Decision

Design Flow will use a hybrid audit architecture:

1. a deterministic execution core for identity, transformations, hard checks,
   state transitions, provenance, and release gates;
2. a versioned rule registry for known and recurring failure modes;
3. a high-freedom LLM auditor for open-ended review of unstructured and
   cross-document evidence;
4. human adjudication for novel findings, waivers, and scientific release.

The LLM is a first-class part of the complete product, not merely a prose
generator. It is also not an authority. It may discover, explain, challenge,
and propose; it may not silently alter canonical data or approve a stage.

This separation is necessary because deterministic rules provide reproducible
coverage of known conditions, while biological projects will continue to expose
new naming errors, contradictory documents, ambiguous construct semantics, and
previously unseen risks that cannot all be enumerated in advance.

## Current Reality

The implemented `program_and_source_intake` node has different kinds of output
that must not be conflated:

| Output | Current source | Reproducibility |
|---|---|---|
| FASTA parsing, record counts, ID pairing, alphabets, frame, start/stop, and AA/CDS translation | Deterministic code | Recomputed offline from the same inputs |
| Descriptors, candidate IDs, statuses, hashes, handoff, and artifact verification | Deterministic code | Recomputed offline from the same inputs and pipeline version |
| HTML tables and status conclusions | Fixed templates over structured records | Regenerated without an LLM |
| Eleven-node workflow and audit-contract wording | Human/LLM-assisted system design frozen in source code | Repeated from the same source version; not inferred from the project data |
| Project-specific questions about provenance, controls, B5, and manual constructs | Human/LLM-assisted review frozen in `project.json` | Repeated from the same configuration; not all rediscovered from the active FASTA inputs |
| Missing expression-host question | Deterministic rule over project context | Regenerated when the recombinant-protein modality is selected and the host is unspecified |

For the three-protein project, four actions were originally added after reviewing
the supplied archive:

- `confirm-source-provenance`;
- `confirm-reference-controls`;
- `resolve-b5-optimized-cds`;
- `confirm-manual-construct-annotations`.

As of 2026-07-14, the project owner waived source provenance for Mock-only use,
resolved the source/reference-control distinction, supplied a replacement B5 Mock
optimized CDS that passes deterministic translation audit, selected CHO cells as a
Mock protein-expression assumption, and deferred manual-construct annotations to
experiment release. The automatic `select-protein-expression-host` action therefore
no longer fires in a new run. Historical immutable runs retain their earlier state.

The active Stage-1 FASTA still contains only the three original CDS records. The
replacement B5 control enters through an explicit Stage 6B input declaration; the
system does not discover or trust it from a filename or an LLM narrative.

Therefore the present M0 implementation is reproducible as a configured run,
but it is not yet a general raw-evidence-to-all-findings automation system.

## What Is Important

The following are system invariants and take priority over presentation or model
branding:

- exact source sequence and source-file identity;
- immutable candidate identity and parent-to-child lineage;
- explicit residue boundaries, linkers, tags, signal peptides, and cleavage sites;
- no silent sequence transformation;
- evidence attached to the exact candidate, tool, model, revision, parameters,
  seed, and input hashes that produced it;
- separate `pass`, `fail`, `warning`, `not_evaluated`, and `error` semantics;
- explicit unresolved questions, owners, deadlines, resolutions, and waivers;
- deterministic hard gates and human-controlled release authority;
- retention of raw model output, disagreement, uncertainty, and excluded results;
- the ability to replay an audit from immutable inputs.

The following are useful but secondary at the current stage:

- visual polish beyond a readable report;
- one composite score or one nominally best candidate;
- identical prose across runs;
- replacing every specialized model with one foundation model;
- optimizing for a particular model vendor;
- generating persuasive explanations without evidence links.

The system should optimize first for semantic correctness, traceability, and the
quality of decisions made from the evidence.

## Three Audit Layers

### 1. Deterministic Core

The deterministic core owns facts and state. It parses structured inputs,
calculates identities, executes declared transformations, evaluates hard rules,
validates schemas, and writes immutable artifacts. It decides only conditions
that can be expressed as tested policy, such as exact translation mismatch,
missing input identity, or an unresolved blocking action.

This layer must run without network access or an LLM. It remains the minimum
offline deployment and must never claim that an unevaluated semantic audit has
passed.

### 2. Versioned Rule Registry

Known findings become rules with stable identities. A rule must contain:

- `rule_id` and rule version;
- applicable stage and input types;
- executable trigger condition;
- severity and gate policy;
- machine-readable evidence fields;
- remediation or required-decision template;
- positive, negative, and regression test fixtures.

Examples include mislabeled translation pairs, undeclared tags, inconsistent
residue ranges, missing construct components, and unsupported expression-host
assumptions. A rule is not made universal merely because it was valid for one
project. Its applicability must be declared and tested.

### 3. LLM Audit Plane

The LLM auditor reviews the open world around the deterministic record. It may:

- inventory and reconcile unstructured files, tables, filenames, and reports;
- compare claims across documents and structured sequence evidence;
- identify ambiguous naming, missing assumptions, contradictions, and unusual
  construct semantics;
- propose additional checks, questions, alternative interpretations, and failure
  hypotheses;
- explain machine findings in project language;
- recommend that a recurring confirmed finding be promoted into a coded rule.

High freedom means broad review scope, not write or release authority. The LLM
must treat supplied documents as untrusted data, cannot execute instructions
found inside them, and should receive read-only tools by default.

## LLM Finding Lifecycle

An LLM observation is not a fact or a gate result. It moves through explicit
states:

1. `proposed`: emitted by an identified auditor run;
2. `evidence_attached`: linked to exact files, records, ranges, and hashes;
3. `confirmed`, `rejected`, or `waived`: adjudicated by a human or a separately
   authorized deterministic verification;
4. `promoted_to_rule`: implemented, versioned, and regression-tested when the
   finding is sufficiently general and recurring.

Only a confirmed finding or a deterministic rule may affect a hard release gate.
Unreviewed LLM output may create a human checkpoint but cannot mark a candidate as
accepted, rejected, safe, effective, or ready for experiment.

Every LLM audit must preserve:

- provider and model identity, local weight digest or remote model revision;
- system policy and prompt-template version;
- exact input artifact identities and permitted tools;
- inference parameters where exposed;
- raw response and structured extraction;
- evidence references, confidence, and known uncertainty;
- human disposition and subsequent rule linkage.

This makes the audit traceable even when exact token-for-token reproduction is
not guaranteed.

## Reproducibility Definition

Deterministic outputs are expected to be semantically stable for the same input
content, configuration, and pipeline version. Candidate IDs, findings, metrics,
and gate states must match. Run timestamps, absolute deployment paths, and
execution IDs may differ, so complete run directories are not currently expected
to be byte-identical.

LLM audits use a different standard: replayability and accountability rather than
guaranteed identical prose. A reviewer must be able to see exactly what the model
saw, which model and policy were used, what it proposed, and how the proposal was
resolved. Stable findings should migrate into deterministic rules when practical.

## Deployment Modes

The same architecture supports three explicit modes:

- `deterministic-only`: completely offline core execution; LLM audit is recorded
  as `not_evaluated`, never silently treated as passed;
- `offline-full`: deterministic core plus an approved locally deployed LLM and
  local scientific model weights/databases;
- `connected-full`: deterministic core plus an approved remote LLM service under
  explicit data-egress, redaction, retention, and provider policies.

The full product includes an LLM audit capability. Temporary LLM unavailability
must not corrupt the deterministic record. Whether an unevaluated LLM audit blocks
a specific stage is an explicit project policy, not an implicit implementation
choice.

## Promotion Loop

The intended learning loop for audit coverage is:

1. deterministic rules inspect all known conditions;
2. the LLM auditor searches for contradictions and unknown failure modes;
3. humans adjudicate novel findings;
4. recurring confirmed findings are generalized into versioned rules;
5. regression fixtures prove those rules on future projects;
6. the LLM remains active to search beyond the expanded rule boundary.

The goal is not to eliminate the LLM. The goal is to prevent the system from
relying on transient, unaudited LLM judgment for facts that can be made explicit,
tested, and repeatable.

## Implementation Priorities

Before claiming general automated intake, Design Flow should implement:

1. finding provenance on every issue and action: `rule`, `model`, `llm`, `human`,
   or `imported`;
2. a structured LLM audit-run and finding schema with evidence references;
3. a general input inventory that includes all supplied files, not only selected
   FASTA controls;
4. extraction of declared construct names, ranges, tags, linkers, and sequence
   claims into structured records;
5. coded rules and golden fixtures for confirmed recurring failures;
6. a local auditor adapter for offline-full deployment;
7. reports that visibly separate deterministic findings, model predictions, LLM
   proposals, and human decisions.

Until those items exist, project-specific LLM-assisted findings must be described
as configured review results, not as automatically rediscovered system output.
