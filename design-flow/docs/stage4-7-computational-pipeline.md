# Stage 4-7 Computational Pipeline

Status: implemented exploratory execution path, version `0.18.0`

This document defines the executable path after a verified Stage 3 structure run.
Under workflow v2, these evaluators also emit structured next-round redesign
requests without mutating candidates. Stage 4 and 5 execute together, Stage 6A and
6B execute together, and Stage 7 consumes both product branches and aggregates the
round feedback. Each command writes
a new immutable continuation run and publishes `latest.json` only after integrity
and semantic recomputation pass.

## Candidate Routing Policy

Stage 4 and Stage 5 evaluate the complete checksum-bound Stage 3 candidate set. They
must not prefilter to higher/mixed ESMFold2 confidence because immune and sequence
developability evidence is complementary, comparatively inexpensive, and may rescue
long or multi-domain candidates penalized by global structure confidence.

The Stage 3 continuation intentionally retains the complete Stage 2 candidate batch
for lineage. Its structure handoff defines the active evaluated subset. Consumers
must therefore resolve candidates from the verified Stage 3 handoff and assessment
order, not by iterating every record in `candidate_batch.json`. Adapter identities
bind both the full candidate-batch SHA256 and a canonical active-candidate-set
SHA256. This permits two selections from the same Stage 2 pool without evidence
collision or accidental full-pool execution.

After Stage 4/5, expensive follow-up uses the priority, diversity-rescue, and archive
lanes defined in [ADR 0004](adr/0004-post-structure-candidate-routing.md). A low
structure-confidence label remains evidence and a review trigger, not an implicit
rejection or deletion.

## System Boundary

The deterministic core owns:

- candidate, sequence, structure, and parent-run identity checks;
- input schema and path validation;
- calculations, status transitions, reports, and handoff records;
- exact output hashes and semantic recomputation;
- explicit `not_evaluated` and `needs_data` states;
- rejection of translation-inconsistent coding sequences;
- transparent Stage 7 scores, exclusions, and sensitivity analysis.

External tools own only their declared scientific calculations. Their output enters
through checksum-bound JSON adapters. An LLM may review the resulting evidence in a
separate audit plane, but it does not create official node scores, change status, or
release candidates. Human approval remains required for biological assumptions,
thresholds, exact product sequences, and experiment release.

## Requirement Gates

`needs_data` describes evidence completeness; it is not an instruction to stop all
work. Every Stage 4/5 requirement therefore carries four independent,
machine-readable fields: `requirement_class`, `required_before_stage`,
`resolution_strategy`, and `exploratory_progress_allowed`.

| Class | Meaning | Default continuation behavior |
|---|---|---|
| `blocking_now` | A required identity or branch-defining input is absent, so the affected next branch cannot be constructed correctly. | Block only the affected immediate branch. |
| `design_variable` | The missing choice belongs in the candidate/product design space rather than being guessed as one fixed answer. | Continue exploratory enumeration; select and approve before release. |
| `required_before_ranking` | Designs can still be generated and assessed, but the missing evidence is required for a formal integrated ranking. | Carry through Stage 6 and block formal Stage 7 handoff. |
| `required_before_release` | The gap does not invalidate exploratory design or technical ranking, but must be closed before an experiment package is released. | Carry through ranking and block experiment release. |

The handoff records preserve two separate blocker views:

- `blocking_action_ids` and `blocking_action_ids_by_stage` contain all overdue or
  target-stage human actions that prevent a **formal** handoff;
- `execution_blocking_action_ids` and
  `execution_blocking_action_ids_by_stage` contain only `blocking_now`
  requirements that prevent the affected exploratory branch from proceeding.

`exploratory_progress_allowed` is false only when an execution blocker is due. The
HTML reports render the class, deadline, resolution path, and continuation state for
each requirement. Project declarations can resolve an action without deleting this
system-owned gate metadata only after the corresponding specification or evidence
removes the deterministic requirement. Marking an action `resolved` while its input
is still absent reopens it during recomputation.

## Execution Route

```bash
./vaxflow init-stage4-5 projects/three-protein/project.json \
  --from-run /absolute/path/to/stage3-run
./vaxflow prepare-stage4-mhc projects/three-protein/project.json \
  --from-run /absolute/path/to/stage3-run \
  --netmhcpan-root /absolute/path/to/netMHCpan-4.2 \
  --netmhciipan-root /absolute/path/to/netMHCIIpan-4.3 \
  --class-i-allele 'BoLA-1:00901' \
  --class-ii-allele 'BoLA-DRB3_00101'
./vaxflow run-stage4-5 projects/three-protein/project.json \
  --from-run /absolute/path/to/stage3-run

./vaxflow init-stage6 projects/three-protein/project.json \
  --from-run /absolute/path/to/stage4-5-run
./vaxflow run-stage6 projects/three-protein/project.json \
  --from-run /absolute/path/to/stage4-5-run

./vaxflow init-stage7 projects/three-protein/project.json \
  --from-run /absolute/path/to/stage6-run
./vaxflow run-stage7 projects/three-protein/project.json \
  --from-run /absolute/path/to/stage6-run
```

Stage 6 initialization first creates the policy and checksum-bound candidate routing
manifest described in [Stage 6 Candidate Routing](stage6-candidate-routing.md).
Use `--refresh-selection` only to archive and migrate a stale pre-routing Stage 6
specification. Product drafting includes all three lanes; generated expensive-model
payloads include only `priority` and `diversity_rescue`.

The `init-*` commands never overwrite existing specifications. Edit the generated
runtime JSON, add the required data files under `runtime_root`, and rerun the matching
stage command. Absolute and runtime-relative paths are accepted, but every input must
resolve inside `runtime_root`.

For the installed technical MHC panel and Stage 5 sequence-model profile, prepare
both adapters against one exact Stage 3 run before executing the combined node:

```bash
/data00/home/wangzhi.wit/models/mRNABERT/design-flow/scripts/run_stage4_5_full.sh \
  /absolute/path/to/verified-stage3-run cpu
```

This ordering prevents one adapter from being evaluated against a stale candidate
batch while the other adapter is being regenerated.

### Current 384-candidate validation

The 2026-07-15 continuation from Stage 3 completed for the complete active set of
384 candidates and passed all 17 integrity and semantic-verification checks.

- NetMHCpan/NetMHCIIpan produced 881,853 peptide-allele observations. The technical
  one-allele-per-class panel labeled 12,454 class-I and 7,056 class-II observations
  as supported under the pinned predictor thresholds.
- TMbed found 2 signal-peptide regions and 3 transmembrane regions; metapredict found
  290 disorder regions.
- The version-0.17 rerun remained `needs_data`, but both nodes were explicitly
  `exploratory_ready` with zero execution blockers. Stage 4 emitted 8 requirements:
  4 required before ranking and 4 required before experiment release. Stage 5
  emitted 5 requirements: 1 expression-compartment design variable and 4 required
  before experiment release. The former combined context requirement is now split
  by decision type without changing scientific evidence.

These counts validate execution and evidence plumbing, not cattle population
coverage, immunogenicity, secretion, expression yield, or product release. In
particular, the one-allele MHC panel is still a technical smoke panel.

## Stage 4: Immune Evidence

The selected CPU tools, installation paths, selection rationale, model boundaries,
and planned adapter artifacts are frozen in
[Stage 4 CPU Toolchain](stage4-toolchain.md).

Specification:
`input/stage4/immune_evidence_specification.json`

Built-in deterministic evidence:

- candidate and Stage 3 residue-map identity;
- a C-alpha non-local-neighbor surface proxy;
- projection of source-protein alignment conservation into every construct;
- residue-level aggregation of optional external adapters.

The surface value is explicitly not solvent-accessible surface area. It is a stable
screening descriptor only.

Data to supply:

- one gapped amino-acid multiple-sequence alignment per immutable source protein;
- an exact `reference_record_id` whose ungapped sequence equals the source control;
- target cattle population/breed assumptions and a versioned BoLA panel;
- `mhc_binding`, `host_similarity`, and `epitope_support` adapter results;
- an approved evidence-use policy.

All three adapters use this envelope:

```json
{
  "schema_version": "vaxflow.residue-evidence.v1",
  "adapter_id": "mhc_binding",
  "candidate_batch_sha256": "<candidate_batch.json sha256>",
  "candidate_set_sha256": "<ordered active Stage 3 candidate-set sha256>",
  "tool": {
    "name": "<tool>",
    "version": "<version>",
    "revision": "<database/model revision>"
  },
  "observations": [
    {
      "evidence_id": "<stable id>",
      "candidate_id": "<candidate id>",
      "sequence_sha256": "<candidate aa sha256>",
      "residue_start": 1,
      "residue_end": 9,
      "status": "supported"
    }
  ]
}
```

Allowed observation statuses are `supported`, `risk`, `context`, and
`not_supported`. Empty observations are valid evaluated results when the pinned tool
really returned no records; absence of the adapter file is `not_evaluated`.

Raw residue observations remain in the immutable adapter artifact and its
checksum-bound run input snapshot. Stage 4/5 node results retain tool identity,
artifact SHA256, total counts, and per-candidate status counts rather than duplicating
the entire raw observation array. Stage 7 consumes these exact aggregates. The
verifier reloads the raw snapshot and recomputes them, so compact storage does not
weaken traceability.

`prepare-stage4-mhc` is the implemented NetMHC adapter. It invokes the static
executables directly with explicit environment paths, runs one allele per raw table,
and verifies every reported peptide against the candidate sequence and residue
coordinates before writing evidence. Its content identity includes the candidate
batch, executable and predictor-model hashes, alleles, peptide lengths, and rank
thresholds. Repeating the same request reuses the verified output directory.

The repository smoke script uses one BoLA-I and one BoLA-DRB3 allele only to verify
the software path. Its generated panel is marked `technical_smoke_test`, leaves host
population approval pending, and cannot support population-coverage or release
claims. Replacing it with a biologically justified versioned panel is a separate
human decision.

## Stage 5: Developability

Specification:
`input/stage5/developability_specification.json`

Built-in deterministic descriptors:

- GRAVY and charge proxy;
- hydrophobic windows, low-complexity windows, and homopolymers;
- N-X-S/T motifs and cysteine count;
- carried Stage 3 low-confidence and boundary-review flags.

These descriptors do not establish solubility, transmembrane topology, modification
occupancy, expression yield, or manufacturing success.

Data to supply:

- approved host, compartment, purification, and formulation context;
- pinned `signal_peptide`, `transmembrane_topology`, `disorder`, `solubility`, and
  `aggregation` adapter results;
- calibrated and approved thresholds.

Stage 5 adapters use the same residue-evidence envelope as Stage 4. Missing predictor
results remain `not_evaluated`; intrinsic rules are not substituted for them.

The executable first model profile provides three categories:

- TMbed 1.0.2 produces separate `signal_peptide` and
  `transmembrane_topology` evidence documents from one pinned local model run;
- metapredict V3 produces residue-level `disorder` evidence;
- `solubility` and `aggregation` remain `not_evaluated` rather than being inferred
  from unrelated models or intrinsic hydrophobicity.

Until the expression and developability policies are approved, predicted signal,
membrane, and disorder regions have evidence status `context`. They cannot silently
become hard failures or ranking penalties. Installation, provider choices, exact
commands, and upgrade boundaries are recorded in
[stage5-toolchain.md](stage5-toolchain.md).

## Stage 6A: Recombinant Protein Product

Specification:
`input/stage6/protein_product_specification.json`

The schema-version-2 specification binds the routing policy/manifest and exact
active Stage 3 candidate set. It cannot silently fall back to the full Stage 2 batch.

For every selected antigen, the system separately records:

- immutable antigen sequence;
- expressed protein sequence including declared N- or C-terminal elements;
- final product sequence after excluding elements declared not retained;
- exact DNA coding sequence and translation audit;
- whether expression additions require a structure recheck.

Every added element requires an ID, location, amino-acid sequence, role, and
`retained_in_final_product` flag. A changed sequence never overwrites the antigen.
Provided CDS files that do not translate exactly to the expression construct cause a
hard error. A mismatched CDS inherited from an exploratory candidate is quarantined
and becomes a data requirement instead of stopping unrelated candidates.

Outputs include:

- `protein_products.json` and `products.csv`;
- `expression_constructs.fasta`, `final_products.fasta`, and
  `coding_sequences.fasta`;
- `structure_recheck_candidates.fasta` and `structure_recheck_job.json` for an
  external ESMFold2 adapter;
- `model_followup_manifest.json` containing only expensive-follow-up-eligible
  products;
- optional `structure_recheck` and `expression_support` evidence adapters.

Protein adapter evidence uses schema `vaxflow.product-evidence.v1`, binds to
`product_batch_sha256`, pins tool name/version/revision, and references `design_id`.

## Stage 6B: mRNA Product

Specification:
`input/stage6/mrna_product_specification.json`

The schema-version-2 specification uses the same routing identity as the protein
branch. All lanes may receive low-cost coding drafts when an exact CDS or versioned
codon table is available; missing coding sequences remain requirements. The
model-follow-up manifest excludes archive candidates.

The source CDS is retained as a named control whenever it translates exactly. After
a codon table is supplied and generation is enabled, the system:

1. creates seeded synonymous trials;
2. rejects GC, forbidden-motif, and homopolymer hard-constraint violations;
3. calculates a codon-adaptation proxy and GC-target deviation;
4. selects deterministic non-dominated/Pareto designs, then labels any requested
   count filled by objective ordering as `objective_order_fallback` rather than
   mislabeling it as Pareto-frontier output;
5. retranslates every selected CDS and requires exact antigen identity;
6. assembles a full mRNA only after exact non-coding elements are approved.

Additional externally supplied coding controls may be declared in
`provided_coding_sequences`. Each declaration binds a stable `control_id` and exact
candidate ID to a runtime-local sequence file, evidence class, provenance status,
and intended use. The pipeline hashes the file and requires exact translation before
emitting a `provided_cds_control`; an LLM description cannot satisfy this check.

`manufacturing_context.method` is recorded independently from
`target_context.delivery_platform`. For example, IVT describes how mRNA is made,
whereas an LNP or another delivery system describes how it is delivered. The
workflow does not infer one from the other.

The codon table contract is:

```json
{
  "schema_version": "vaxflow.codon-usage.v1",
  "species": "<target>",
  "provenance": {
    "source": "<source>",
    "version": "<version>",
    "revision": "<revision>"
  },
  "codon_frequencies": {
    "AAA": 0.1
  }
}
```

`codon_frequencies` must contain exactly all 61 standard sense codons, with at least
one positive codon for each amino acid. Stage 6B external adapters are
`rna_structure` and `evo2_sequence_score`. They use schema
`vaxflow.mrna-evidence.v1`, bind to `mrna_design_batch_sha256`, and reference
`design_id`. Evo2 is therefore evidence in the product branch, not an implicit
workflow controller or a replacement for translation and synthesis constraints.

## Stage 7: Integrated Ranking

Specification:
`input/stage7/ranking_specification.json`

The default policy intentionally gives zero weight to uncalibrated immune evidence.
Those values remain visible but cannot change rank. Positive default weights cover
structure confidence, intrinsic developability review count, and modality-specific
product completeness. The runtime policy can be changed only explicitly.

Pinned MHC support, external developability risk flags, protein expression support,
Evo2 sequence scores, and RNA-structure scores are also exposed as named Stage 7
features. Their default weight is zero. A finite adapter `score` is aggregated only
for the exact checksum-bound design IDs; changing its direction or weight requires an
explicit ranking-specification revision and calibration rationale.

Execution order:

1. bind exact candidates and join Stage 3-6 evidence by candidate ID;
2. apply required-feature checks and declared hard gates;
3. min-max normalize each feature across the frozen candidate set;
4. compute weighted scores with a coverage penalty for missing positive-weight data;
5. retain excluded candidates and component contributions;
6. construct per-modality provisional portfolios with control minimums and a 3-mer
   Jaccard sequence-diversity threshold;
7. perturb every positive weight and report rank spans.

The report is technical prioritization, not an efficacy ranking. The machine output
always contains `formal_portfolio: []`; Stage 8 human experiment release is outside
the implemented scope.

## Status Semantics

- `evaluated`: all inputs required by the node specification were supplied and
  validated; this still does not mean experimentally validated or released.
- `needs_data`: one or more declared datasets, model results, contexts, or policies
  are absent, or an upstream dependency remains `needs_data`. This status alone
  does not block exploratory continuation.
- `needs_human_input`: deterministic calculation is complete but formal decisions
  remain open.
- `not_evaluated`: a specific category has no valid supplied evidence.

Formal handoff readiness and exploratory execution readiness are reported
separately. A node may therefore be `needs_data` and still be
`exploratory_ready`.

No missing evidence is converted into a favorable score.

## Verification and Tamper Detection

Every continuation run contains exact copied parent artifacts, a unique parent
manifest/index seal under `inputs/lineage/`, input snapshots, node result hashes, and
an artifact index. `verify-run` then recomputes Stage 4-7 semantic outputs from copied
inputs. Rebuilding `artifact_index.json` after changing a score, descriptor, or
sequence does not bypass semantic verification.

Covered tests include:

- missing-data execution through Stage 7;
- complete Stage 4/5 adapter and alignment inputs;
- complete codon-table generation with translation-safe Pareto designs;
- output and parent-lineage integrity;
- semantic tampering after an attacker rebuilds the artifact index.
