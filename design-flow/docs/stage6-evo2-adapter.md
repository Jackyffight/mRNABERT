# Stage 6 Evo 2 Sequence-Evidence Adapter

Status: implemented in pipeline version `0.19.0` for checksum-bound `evo2_7b`
zero-shot scoring

## Scope

This adapter scores the real coding DNA records already emitted by Stage 6B. It
does not generate a coding sequence for a protein-only candidate and it does not
infer missing UTR, cap, poly(A), delivery, or host-cell assumptions.

The current routed Mock run contains seven mRNA/CDS records across six candidates:

- six source-derived coding controls;
- one user-declared company-optimized B5 Mock control;
- all seven are in `priority` or `diversity_rescue` and are eligible for expensive
  follow-up;
- B5 is the only current candidate with two synonymous sequences, so it is the only
  within-protein comparison in this batch.

The other 45 protein candidates in the 52-record expensive follow-up set do not
yet have coding sequences. They can enter Evo 2 only after a versioned target-
context codon table and translation-safe synonymous generation are enabled.

## Score

The worker uses the pinned autoregressive forward API from Evo 2 and calculates:

```text
total_log_likelihood = sum log P(token[i] | token[0:i]) for i=1..L-1
score = total_log_likelihood / (L - 1)
perplexity = exp(-score)
```

The adapter score is `mean_log_likelihood`; higher is more likely under the pinned
model. The sequence is the exact 5-prime-to-3-prime coding DNA including any
terminal stop codon. Reverse complement, UTRs, cap, poly(A), and phylogenetic prompt
tags are not added.

Every observation is deliberately marked `context`, not `supported`. A generic DNA
language-model likelihood is not calibrated expression, immune protection,
manufacturability, or efficacy evidence. Cross-protein values must not be treated as
a direct biological ranking. The default Stage 7 feature weight remains zero until
a task-specific calibration rationale is supplied.

## Pinned Runtime

| Component | Pin |
| --- | --- |
| Model | `evo2_7b` |
| Hugging Face revision | `bda0089f92582d5baabf0f22d9fc85f3588f6b58` |
| Checkpoint bytes | `13,766,621,200` |
| Checkpoint SHA-256 | `c66645929dc1b9c631f5be656da8726f38946315dc9167000a615dd626fcecf4` |
| Python package | `evo2==0.6.0` |
| Scoring protocol | `evo2-next-token-mean-log-likelihood-v1` |

The GPU worker verifies the complete 13.8 GB checkpoint SHA-256 before loading the
model. It uses one visible CUDA device and resumes completed records from an atomic
partial score file.

## 1. Prepare The Job On The Runtime Host

```bash
/data00/home/wangzhi.wit/models/mRNABERT/design-flow/scripts/prepare_stage6_evo2_job.sh
```

The command verifies the immutable Stage 6 run, binds the exact
`mrna_design_batch_sha256`, validates all seven FASTA records, and writes one
transfer archive under:

```text
/data00/home/wangzhi.wit/models/design-flow-runtime/three-protein/transfer/stage6-evo2
```

Only the resulting `.tar.gz` file needs to move to the GPU/NAS environment.

## 2. Score On The A100 Worker

Pull the same repository revision on the GPU worker and place the job archive at an
absolute NAS path. Run:

```bash
/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mRNABERT/design-flow/scripts/run_stage6_evo2_gpu_nas.sh \
  /absolute/nas/path/to/stage6-evo2-job.tar.gz
```

The fixed script uses the existing isolated Evo 2 environment and checkpoint:

```text
/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_baselines/evo2/
  venv-evo2-0.6.0
  evo2_7b-bda0089f92582d5baabf0f22d9fc85f3588f6b58/evo2_7b.pt
```

The result archive is written to a `results/` directory next to the transferred job.
The archive contains exactly four files:

- original `job-manifest.json`;
- original `sequences.fasta`;
- schema-valid `evo2-evidence.json`;
- checksum-bound `run-manifest.json`.

## 3. Import And Rerun Stage 6

Move the single result `.tar.gz` back to the runtime host and run:

```bash
/data00/home/wangzhi.wit/models/mRNABERT/design-flow/scripts/import_stage6_evo2_results.sh \
  /absolute/path/to/stage6-evo2-result.tar.gz
```

The importer:

1. rejects unsafe or unexpected archive members;
2. recomputes job and result identities;
3. verifies complete design-ID coverage and finite scores;
4. verifies the source Stage 6 artifact index and mRNA batch hash;
5. stores immutable evidence under `input/stage6/mrna-evidence/`;
6. archives the previous mRNA specification;
7. updates only the `evo2_sequence_score` adapter declaration;

## Stage 7 observed-subset sensitivity

Status: implemented in pipeline version `0.21.0`.

Imported evidence is optional in the canonical Stage 7 policy and therefore has
weight `0`. To measure its effect without treating unscored candidates as poor
candidates, generate paired policies over the exact Evo 2-observed subset:

```bash
./vaxflow init-stage7-evo2-sensitivity projects/three-protein/project.json \
  --from-run /absolute/path/to/verified-stage6-run \
  --evo2-weight 0.25
```

The command writes immutable `control.json` and `weighted.json` policies under
`input/stage7/experiments/`. Run each policy explicitly:

```bash
./vaxflow run-stage7 projects/three-protein/project.json \
  --from-run /absolute/path/to/verified-stage6-run \
  --specification /absolute/path/to/control.json

./vaxflow run-stage7 projects/three-protein/project.json \
  --from-run /absolute/path/to/verified-stage6-run \
  --specification /absolute/path/to/weighted.json
```

These are exploratory sensitivity runs, not formal release policies. Restricting
both arms to the same observed subset prevents missing Evo 2 values from being
misinterpreted as low scores.
8. reruns Stage 6 from the pinned Stage 4/5 parent and verifies the new run.

The new Stage 6 run will still be `needs_data`. Evo 2 removes one explicit missing
requirement; it does not waive unrelated product or evidence requirements.

## Remaining Stage 6 Inputs

After this Evo 2 run, these items remain separate:

- versioned cattle/target-context 61-codon table for new synonymous designs;
- approved target cell and delivery platform;
- approved 5-prime UTR, 3-prime UTR, poly(A), cap, and modified-nucleoside assumptions;
- RNA-structure evidence for the exact mRNA design batch;
- aggregation and solubility evidence, or an explicit Stage 5 waiver;
- recombinant-protein expression support and changed-construct structure recheck;
- Stage 7 hard gates, feature weights, controls, diversity policy, and experiment
  budget.

None of these is silently inferred from an Evo 2 score.
