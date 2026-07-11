# Seven-Day GPU Execution Plan: Fusion Protein Design Pipeline

Date: 2026-07-11

## Objective

Use the approximately seven-day free-GPU window to create assets that remain
valuable after GPU access becomes expensive. The sprint is not organized around
giving equal compute to each repository. It is organized around the full fusion
protein-to-mRNA workflow:

```text
fusion design specification
  -> domain order and linker candidates
  -> structure prediction
  -> constrained ProteinMPNN expansion
  -> structure refold and domain-preservation checks
  -> structural/manufacturability ranking
  -> synonymous CDS generation
  -> rule-based and mRNABERT ranking
```

The primary sprint asset is a versioned candidate-and-structure-label dataset. Model
checkpoints are secondary: they are useful only when they improve held-out or
fusion-task outcomes.

## Current State at Sprint Start

### mRNABERT

- Checkpoint 600000 is the frozen internal baseline.
- It contains transferable signal on the single-protein mRFP benchmark, but does not
  outperform the tuned public checkpoint conclusively.
- Frozen-probe, feature-baseline, cross-protein, and clean temporal evaluations are
  still open.
- Long blind continuation training is not a sprint priority.

### ProteinMPNN

- The official `v_48_020` checkpoint is available as the unchanged baseline.
- The current evaluator has only a 16-structure CPU pilot result, not a complete GPU
  baseline.
- The post-2021 ProteinMPNN v1 dataset contains 46,619 records and passed semantic
  validation with zero exact-sequence and zero PDB split leaks.
- The training launcher is single-GPU. Multi-GPU DDP is not a prerequisite because
  independent configurations and seeds can run one per V100.
- The current implementation changes must be reviewed, tested with full
  dependencies, committed, and pinned before a long run starts.

### Structure Layer

- ESMFold2 is specified as a frozen structure oracle in the target architecture.
- No production ESMFold2 wrapper, pinned weights, candidate queue, or resumable
  fold/refold artifact pipeline exists in this workspace yet.
- Fusion-protein design inputs have not yet been materialized as target
  specifications.

## Resource Allocation

The available resources are one indivisible 3xA100 allocation, additional A100s,
and multiple V100s. Exact pool sizes may change; the assignment scales by adding
workers, not by changing the experiment design.

| Resource | Primary role | Execution pattern |
|---|---|---|
| Indivisible 3xA100 allocation | Fold/refold label production | Three independent candidate-shard workers inside one allocation |
| Additional A100s | More fold/refold shards, top-candidate recomputation, surrogate training | One inference worker per GPU |
| V100 pool | ProteinMPNN pilots, configurations, seeds, and optional from-scratch control | One independent run per GPU |
| One spare V100 when available | mRNABERT frozen probe and final CDS scoring | Short bounded jobs |
| CPU and storage workers | Candidate enumeration, deduplication, metrics, queues, manifests, and archival | Continuous alongside GPU jobs |

Target allocation by GPU-hours:

- 60-70%: structure prediction and refold labels;
- 20-30%: ProteinMPNN baselines, continuation, and surrogate/ranker training;
- at most 10%: mRNABERT validation and bounded controlled experiments;
- reserve enough capacity for failed-job recovery and final recomputation.

GPU utilization does not need to mean end-to-end model training. Batched inference
that produces durable oracle labels is the highest-value use of this window.

## Required Target Package

Large-scale fusion computation does not start until at least one target package
exists. Each target must contain:

- stable target and domain identifiers;
- amino-acid sequence and, when available, a reference structure for each domain;
- immutable antigen-domain and epitope residues;
- explicitly mutable positions or regions;
- allowed domain orders;
- allowed linker families and length ranges;
- intended monomeric or oligomeric state;
- expression host, purification tags, and maximum total length;
- project safety-review status and any prohibited modifications.

The pipeline must reject any candidate that changes immutable residues. ProteinMPNN
is used to design only allowed linker, junction, scaffold, or explicitly mutable
positions. A better-folded candidate that changes the intended epitope is a failed
candidate.

## Seven-Day Schedule

### Day 0-1: Freeze Assets and Measure Throughput

Engineering and data tasks:

1. Review and commit the current ProteinMPNN implementation changes.
2. Run dependency-complete ProteinMPNN tests in the actual GPU environment.
3. Pin the v1 dataset manifest, validation result, file sizes, and checksums.
4. Materialize the first fusion target package and candidate manifest schema.
5. Implement a resumable structure-inference wrapper with candidate IDs, model
   revision, weight checksum, parameters, timing, and failure records.

GPU tasks:

1. Evaluate official ProteinMPNN weights on complete 2021 validation/test and 2026
   validation/test splits.
2. Benchmark ProteinMPNN token budgets and loader settings separately on A100 and
   V100.
3. Benchmark the structure predictor on representative fusion lengths and record
   wall time, peak memory, failure rate, and output size.
4. Run 30-100 structure smoke examples before scaling.

Gate 1: by the end of Day 1, the team must know the measured fold/refold throughput
per GPU and have resumable, provenance-complete outputs. If ESMFold2 integration is
not ready, validate the pipeline with an available pinned structure predictor, but
do not launch the large label-production phase with an unversioned fallback.

### Day 1-2: Close the Small Fusion Loop

Generate 100-500 diverse architecture candidates across domain order and linker
choices. For candidates that pass the first fold:

1. derive a backbone and mutable-position mask;
2. sample constrained ProteinMPNN sequences at a small, recorded parameter grid;
3. verify all immutable residues exactly;
4. refold the candidate sequence;
5. compare each refolded domain with its isolated/reference domain;
6. write scalar metrics and explicit pass/fail reasons.

Gate 2: scale only when the pipeline differentiates candidates, constraints are
never violated, outputs are resumable, and storage growth is understood. Thresholds
are calibrated on this pilot rather than invented before observing score
distributions.

### Day 2-4: ProteinMPNN Matrix and First Label Wave

Run short single-seed ProteinMPNN pilots first:

| ID | Initialization | Training data | Purpose |
|---|---|---|---|
| P0 | Official checkpoint, no training | None | Fixed published baseline |
| P1 | Official checkpoint | Post-2021 v1 only | Measure new-data gain and forgetting risk |
| P2 | Official checkpoint | New data plus 25% upstream replay | Primary anti-forgetting candidate |
| P3 | Official checkpoint | New data plus 50% upstream replay | Replay-ratio control |

A multi-source sampler or an equivalent reproducible mixed index is required before
P2/P3. Do not approximate replay manually between epochs.

Select one or two continuation configurations using 2021 and 2026 held-out metrics,
then run three seeds. A from-scratch reproduction may use spare V100 capacity, but
does not displace continuation or structure-label jobs.

At the same time, run the first large, diverse fold/refold wave on all A100 workers.
The candidate count is determined from measured throughput:

```text
daily candidate capacity = A100 workers * 86400 / mean seconds per candidate
```

Gate 3: a continued ProteinMPNN model advances only if it improves 2026 held-out
performance without material regression on the 2021 baseline and improves or
preserves fusion-task refold outcomes. Validation NLL alone is not sufficient.

### Day 4-6: Surrogate Ranking and Active Selection

Train a fast surrogate/ranker from the first structure-label wave. Inputs may
include sequence, domain/linker design, ProteinMPNN scores, and inexpensive sequence
features. Targets remain component-wise visible: refold pass, domain preservation,
junction quality, interface quality when applicable, and structural liabilities.

Split surrogate evaluation by target or protein family, not by random candidate
rows. Use it to select both high-scoring and uncertain candidates for a second
expensive structure wave. Compare pass-rate enrichment against diverse random
selection.

Gate 4: keep the surrogate only if it enriches expensive refold passes on unseen
targets or held-out families. Otherwise retain the oracle labels and do not promote
the model.

### Day 6-7: Add the mRNA Layer and Freeze Outputs

For structurally retained protein candidates:

1. generate synonymous CDS candidates with exact translation checks;
2. compute GC, CAI, codon-frequency, motif, and other rule-based features;
3. add mRNABERT scores or a supervised head where supported;
4. retain component-level structural, protein, and mRNA scores for Pareto ranking;
5. recompute final top protein candidates with higher confidence or an independent
   structure check when available.

Stop starting jobs that cannot finish at least 12 hours before the free window ends.
Use the remaining time for final evaluation, checksums, copying, and recovery of any
missing shards.

## Candidate and Label Artifact

Every candidate must be traceable from target specification through final CDS. At a
minimum, persist:

- target, domain, linker, and candidate IDs;
- source domain order and immutable/mutable residue masks;
- input sequence and translated-protein hash;
- ProteinMPNN checkpoint, parameters, seed, log probability, and rank;
- fold/refold model revision, checksum, parameters, seed, runtime, and status;
- per-domain structural preservation metrics;
- junction confidence/error metrics;
- global fold confidence and interface metrics when applicable;
- clash, compactness, aggregation, and manufacturability proxies;
- epitope/immutable-residue preservation result;
- mRNA/CDS sequence, rule features, and mRNABERT score;
- component-wise pass/fail decisions and rejection reasons.

For all candidates, retain compressed structures, per-residue confidence, and scalar
metrics. Retain large PAE arrays, logits, and embeddings only for top, uncertain, or
diagnostic candidates unless storage measurements prove full retention is safe.

These labels are structural and manufacturability evidence, not evidence of vaccine
efficacy or immunogenicity. Those claims require appropriate assays and wet-lab
labels.

## Storage and Reliability

The sprint must not repeat the earlier checkpoint quota failure.

- Use NAS/local high-throughput storage for live reads and writes.
- Preflight free space and quota before every scale-up gate.
- Write candidate outputs in atomic, restartable shards of approximately 500-1000
  candidates with manifests and checksums.
- Keep only best/latest training checkpoints plus explicitly retained milestones.
- Separate compact scalar tables from large structure tensors.
- Continuously copy completed immutable shards to the archive destination rather
  than waiting for Day 7.
- Record failed candidates; do not silently drop them or rerun them indefinitely.

## Sprint Exit Criteria

The seven-day window is successful when it leaves:

1. a pinned ProteinMPNN code revision and validated v1 dataset identity;
2. complete official ProteinMPNN baseline results on old and new held-out data;
3. at least one three-seed continued-training comparison, if the pilots pass;
4. a resumable multi-GPU fold/refold queue with measured cost per candidate;
5. a versioned fusion candidate dataset with constraints, structures, labels, and
   rejection reasons;
6. a measured surrogate/ranker result or a documented negative result;
7. an end-to-end demonstration from fusion target specification to ranked CDS;
8. archived checkpoints, manifests, logs, checksums, and a short decision report.

## Explicit Non-Goals

- Do not blindly add another large mRNABERT continuation run.
- Do not train ESMFold2 from scratch.
- Do not prioritize ProteinMPNN DDP over independent V100 experiments.
- Do not launch generic fusion generation without target constraints.
- Do not select ProteinMPNN only by validation NLL.
- Do not collapse structural, manufacturability, mRNA, and eventual wet-lab outcomes
  into one opaque score.
- Do not describe structure-model predictions as vaccine efficacy.
