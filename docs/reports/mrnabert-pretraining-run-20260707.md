# mRNABERT Pretraining Run Record

Date: 2026-07-07

## Executive Summary

This document records the first long mRNABERT scratch pretraining run on the full pretraining corpus and defines the next sample-running plan for model selection and wet-lab candidate generation.

The run should be treated as a successful infrastructure and first-stage pretraining milestone:

- scratch initialization from `assets/mrnabert-base`
- 3x A100-SXM4-80GB with `torchrun`
- NAS-local training data shards
- HDFS checkpoint/output storage
- streaming `file-shard` reader with bounded shuffle
- completed global step `100000`
- final checkpoint saved at:

```text
/mnt/hdfs/byte_neptune_ai/mrna/train/runs/mrnabert-full-devbox-20260707024008/output/checkpoint-100000
```

The final Trainer metric `train_loss=1.2065` should not be used as the real model-quality number. Because this run resumed from `checkpoint-50000`, the aggregate metric is distorted by Trainer accounting. The realistic recent streaming batch loss near the end was approximately `2.2-2.7`, with occasional harder regions around `2.8`.

The main next step is not another blind long run. We need a fixed validation set and checkpoint comparison before deciding whether to continue from `100000`, lower the learning rate, adjust shuffle/data order, or change model/data parameters.

## Why This Run Matters

Before this run, the training path had several practical blockers:

- local/root filesystem cache could fill up
- HDFS/FUSE data generation and multiprocessing writes were unstable
- HuggingFace Arrow conversion was not suitable for a 141 GiB text corpus on the available filesystem
- naive 3-GPU streaming from one file caused stalls and poor throughput
- resume from streaming checkpoints tried to replay skipped data and hung
- PyTorch 2.6+ refused to load older Trainer RNG state files under the new `weights_only=True` default

The current launcher and training code now support the intended working mode:

```text
NAS/local large text input -> deterministic random text shards -> torchrun file-shard streaming
                           -> HDFS checkpoints and final model artifacts
```

This is the right shape for this environment: data reads stay close to the training node; durable model outputs remain on HDFS.

## Data

Original full pretraining text:

```text
/mnt/hdfs/byte_neptune_ai/mrna/pre.txt
```

NAS-local copy used for training:

```text
/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_data/pre.txt
```

Observed corpus size:

```text
lines: 36,248,629
bytes: 141.40 GiB
```

Auto-sharded NAS-local training files:

```text
/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_data/data_shards/pre-3shards-seed42/pre_shard_*.txt
```

Shard summary:

| Shard | Lines | Size |
| --- | ---: | ---: |
| `pre_shard_00000.txt` | 12,084,439 | 47.13 GiB |
| `pre_shard_00001.txt` | 12,081,496 | 47.13 GiB |
| `pre_shard_00002.txt` | 12,082,694 | 47.14 GiB |

Sharding performance:

```text
elapsed: 5m29s
rate: 439.16 MiB/s
```

The sharding step is deterministic for the same input metadata, shard count, and seed. The launcher reuses the shard cache when the manifest matches.

## Environment

Observed runtime environment:

| Item | Value |
| --- | --- |
| Python | 3.11.2 |
| PyTorch | 2.7.1 |
| CUDA | 12.6 |
| Transformers | 4.32.0 |
| Datasets | 5.0.0 |
| Accelerate | 0.24.1 |
| GPUs | 3x NVIDIA A100-SXM4-80GB |
| dtype | bf16 |
| TF32 | enabled |

Kernel warning observed:

```text
Detected kernel version 5.4.143, below recommended 5.5.0.
```

This warning did not stop the completed run, but it remains a residual hang risk for long distributed jobs.

## Code Path

The run used the packaged pretraining entrypoint:

```text
./run_train.sh -> main.py pretrain -> mrnabert.pretrain
```

Important launcher behavior:

- direct launch defaults to one GPU to avoid implicit `DataParallel`
- `torchrun` uses one process per visible GPU
- scratch mode uses local config/tokenizer from `assets/mrnabert-base`
- streaming is enabled by default when `--max-steps` is set
- one large text file is auto-sharded for multi-GPU streaming
- `file-shard` assigns shard files to ranks
- `dispatch_batches=false` prevents one rank from becoming the data bottleneck
- streaming resume uses `ignore_data_skip=true` plus a local raw-example skip offset
- resume exports `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1` for PyTorch 2.6+ checkpoint RNG compatibility

## Completed Commands

### First Full-Scale Segment

This segment trained from scratch to global step `50000`.

```bash
./run_train.sh \
  --env devbox \
  --train-file /mnt/hdfs/byte_neptune_ai/mrna/pre.txt \
  --launcher torchrun \
  --devices 0,1,2 \
  --max-steps 50000 \
  --batch-size 32 \
  --grad-accum 1 \
  --warmup-steps 2000 \
  --logging-steps 50 \
  --save-steps 5000 \
  --save-total-limit 5 \
  --lr 5e-5
```

Output workspace:

```text
/mnt/hdfs/byte_neptune_ai/mrna/train/runs/mrnabert-full-devbox-20260707024008
```

### Resume Segment

This segment resumed from `checkpoint-50000` to global step `100000`.

```bash
./run_train.sh \
  --env devbox \
  --train-file /mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_data/pre.txt \
  --shard-dir /mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_data/data_shards/pre-3shards-seed42 \
  --output-root /mnt/hdfs/byte_neptune_ai/mrna/train/runs \
  --launcher torchrun \
  --devices 0,1,2 \
  --max-steps 100000 \
  --batch-size 32 \
  --grad-accum 1 \
  --warmup-steps 2000 \
  --logging-steps 50 \
  --save-steps 5000 \
  --save-total-limit 5 \
  --lr 3e-5 \
  --run-name mrnabert-full-devbox-20260707024008 \
  --resume /mnt/hdfs/byte_neptune_ai/mrna/train/runs/mrnabert-full-devbox-20260707024008/output/checkpoint-50000
```

Expected launcher summary for the resume command:

```text
resume: /mnt/hdfs/byte_neptune_ai/mrna/train/runs/mrnabert-full-devbox-20260707024008/output/checkpoint-50000
ignore_data_skip: true
streaming_resume_global_step: 50000
streaming_resume_world_size: 3
streaming_resume_skip_samples: 4800000
torch_force_no_weights_only_load: 1
streaming_reader: file-shard
streaming_shuffle_buffer: 20000
streaming_shuffle_seed: 42
dispatch_batches=False
```

## Training Configuration

| Parameter | Value |
| --- | --- |
| init mode | scratch |
| model path | `assets/mrnabert-base` |
| trainable parameters | 86,493,002 |
| max sequence length | 1024 |
| MLM probability | 0.15 |
| per-device batch size | 32 |
| world size | 3 |
| gradient accumulation | 1 |
| effective batch size | 96 |
| dtype | bf16 |
| optimizer | AdamW |
| weight decay | 0.01 |
| first segment LR | `5e-5` |
| resume segment LR | `3e-5` |
| warmup steps | 2000 |
| scheduler | linear decay |
| save interval | 5000 steps |
| checkpoint retention | 5 |
| dataloader workers | 0 |
| streaming reader | `file-shard` |
| shuffle buffer | 20000 per rank |

## Scale Accounting

Full corpus examples:

```text
36,248,629
```

Effective batch size:

```text
32 per GPU * 3 GPUs * 1 grad accumulation = 96 examples / optimizer step
```

Approximate full-epoch steps:

```text
36,248,629 / 96 = 377,590 steps
```

Completed steps:

```text
100,000 steps
```

The training log reports `epoch=0.5` because the Trainer estimates examples from `max_steps * effective_batch_size`. Based on the observed corpus size, `100000` steps is approximately:

```text
100,000 * 96 / 36,248,629 = 26.5% of one full pass
```

If we define "one epoch" by the Trainer's synthetic max-step accounting for this resumed job, the displayed epoch can be different. For model-quality discussion, use the explicit step count and fixed validation loss, not the displayed streaming epoch alone.

## Observed Runtime

Resume segment final metrics:

```text
train_runtime: 9:31:20.75
train_samples_per_second: 280.041
train_steps_per_second: 2.917
```

Important interpretation:

- The aggregate `train_steps_per_second=2.917` is not the wall-clock throughput of the whole 100k-step training history.
- The observed progress bar during the resume segment was commonly around `1.3-1.6 it/s`.
- The resume segment completed 50k additional steps in about 9.5 hours, which is about `1.45 step/s`.

Use wall-clock progress-bar speed for operational estimates.

## Loss Observations

Random baseline for 74-token vocabulary:

```text
ln(74) ~= 4.30
```

Early scratch training started near `4.0+`, then dropped into the `2.x` range. That confirms the model learned non-trivial token structure.

Representative stable regions after resume:

```text
2.58 -> 2.35 around epoch display 0.10-0.11
2.52 -> 2.25 around epoch display 0.31-0.32
2.60 -> 2.15 -> 2.70 -> 2.40 around epoch display 0.40-0.44
2.50 -> 2.20 -> 2.88 -> 2.45 near the end
```

Interpretation:

- This is not a clean monotonic training curve because data is streamed, dynamically masked, and shuffled with a bounded buffer.
- Local drops to `1.8-1.9` are not sufficient evidence that the model converged to that level.
- Later `2.6-2.8` regions are not automatically divergence; they likely reflect harder data regions or different sequence distribution.
- There was no NaN, no obvious exploding loss, and checkpoints saved correctly.

The final reported aggregate:

```text
train_loss: 1.2065
```

should not be used as a real quality score. It is likely distorted by resumed Trainer accounting. The reliable signals are per-log batch loss and, next, fixed validation loss.

## Current Checkpoints

Primary checkpoint:

```text
/mnt/hdfs/byte_neptune_ai/mrna/train/runs/mrnabert-full-devbox-20260707024008/output/checkpoint-100000
```

Final exported output directory:

```text
/mnt/hdfs/byte_neptune_ai/mrna/train/runs/mrnabert-full-devbox-20260707024008/output
```

Because `--save-total-limit 5` was used, older checkpoints are automatically deleted. Near the end of the run, `checkpoint-70000` and `checkpoint-75000` were deleted as expected.

The checkpoints most worth comparing now are the retained high-step checkpoints, especially:

```text
checkpoint-80000
checkpoint-85000
checkpoint-90000
checkpoint-95000
checkpoint-100000
```

Exact availability should be checked in the output directory before evaluation.

## Operational Lessons

### Keep Data Local/NAS for Training

Training input should live on NAS/local training storage:

```text
/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_data
```

HDFS should keep durable outputs:

```text
/mnt/hdfs/byte_neptune_ai/mrna/train/runs
```

This split avoids HDFS/FUSE seek and multiprocessing write issues while preserving checkpoints.

### Use Shards for Multi-GPU Training

For 3 GPUs, use 3 shards. For 8 GPUs, use 8 shards. Do not make every rank stream the same huge file.

The launcher can create/reuse shards automatically. For explicit control, pass:

```bash
--shard-dir /path/to/data_shards/pre-3shards-seed42
```

Use `--reshard` only when the input file changed or a new shard count/seed is desired.

### Resume Streaming Runs Without Data Replay

Earlier runs used:

```text
--ignore_data_skip true
```

by itself. That resumes model, optimizer, scheduler, and global step, but it also
restarts the local streaming iterator from the beginning. For fixed shards and
fixed shuffle seeds, each resume segment can therefore replay the same shard
prefix. Do not interpret historical `global_step * effective_batch` as unique
corpus coverage for those segments.

The launcher now keeps `--ignore_data_skip true` only to avoid HuggingFace
Trainer's slow batch-level replay, and instead passes a raw streaming offset:

```text
--streaming_resume_skip_samples <checkpoint_global_step * effective_batch>
```

This skip is applied inside the local streaming dataset after bounded shuffle and
before tokenization, so future resumes advance the data stream without GPU-side
batch replay. For legacy checkpoints that were already trained with replay, pass
`--streaming-resume-skip-samples` explicitly if you want to use an estimated true
data cursor rather than the checkpoint global step.

### Trust Local Batch Loss, Not Resumed Aggregate Loss

When resuming with HuggingFace Trainer, aggregate `train_loss` can be misleading. For training-health monitoring, use:

- recent batch loss windows
- NaN/explosion checks
- checkpoint save success
- fixed validation loss

## Next Step: Fixed Validation Set

Before more long training, create a fixed validation set from the same distribution.

Recommended size:

```text
50,000 to 200,000 records
```

Recommended storage:

```text
/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_data/eval/valid_100k.txt
```

Selection requirements:

- deterministic seed
- no overlap with training shards if possible
- same line format as `pre.txt`
- saved as a durable file, not an ephemeral stream sample

Evaluation policy:

- evaluate retained checkpoints on the exact same validation file
- report MLM eval loss and perplexity
- choose the next base checkpoint by validation loss, not training loss

Candidate comparison table:

| Checkpoint | Train step | Eval loss | Perplexity | Notes |
| --- | ---: | ---: | ---: | --- |
| `checkpoint-80000` | 80000 | TBD | TBD | compare |
| `checkpoint-85000` | 85000 | TBD | TBD | compare |
| `checkpoint-90000` | 90000 | TBD | TBD | compare |
| `checkpoint-95000` | 95000 | TBD | TBD | compare |
| `checkpoint-100000` | 100000 | TBD | TBD | current final |

## Next Step: Model Continuation Decision

After fixed validation:

### Continue if Validation Still Improves

Recommended continuation:

```bash
./run_train.sh \
  --env devbox \
  --train-file /mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_data/pre.txt \
  --shard-dir /mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_data/data_shards/pre-3shards-seed42 \
  --output-root /mnt/hdfs/byte_neptune_ai/mrna/train/runs \
  --launcher torchrun \
  --devices 0,1,2 \
  --max-steps 150000 \
  --batch-size 32 \
  --grad-accum 1 \
  --warmup-steps 0 \
  --logging-steps 50 \
  --save-steps 5000 \
  --save-total-limit 5 \
  --lr 1e-5 \
  --run-name mrnabert-full-devbox-20260707024008 \
  --resume /mnt/hdfs/byte_neptune_ai/mrna/train/runs/mrnabert-full-devbox-20260707024008/output/checkpoint-100000
```

Rationale:

- checkpoint already reached a mature low-LR region
- avoid restarting a high warmup schedule
- use a smaller LR for refinement
- keep the same run name for checkpoint continuity

### Pause if Validation Plateaus

If `checkpoint-95000` and `checkpoint-100000` are not better than earlier checkpoints, stop long pretraining and move to:

- evaluation harness
- scoring/ranking experiments
- validation-set curation
- downstream codon optimization candidate generation

## Sample-Running Design

The goal of sample running is not to ask the model for one impressive sequence. The goal is to build a reproducible candidate funnel that can be compared against baselines and sent to wet lab.

### Stage 0: Safety and Task Definition

Each sample request should produce a typed design state:

| Field | Purpose |
| --- | --- |
| `target_id` | stable sample id |
| `protein_sequence` | amino-acid target, if protein-preserving |
| `host_context` | host/cell/assay context |
| `objective` | expression, stability, translation, manufacturability weights |
| `hard_constraints` | preserve protein, forbidden motifs, GC bounds |
| `baseline_methods` | rules/GPT/Claude/public codon tools |

Unsafe or unsupported requests should be filtered before model scoring.

### Stage 1: Protein/Structure Branch

For protein-level design tasks, use the existing four-step system:

```text
ESMFold2 geometry -> ProteinMPNN expansion -> ESMFold2 refold filter -> mRNABERT mRNA ranking
```

Outputs to persist:

- folded structure path
- fold model revision and checksum
- geometric axis/interface table
- ProteinMPNN candidate set
- refold scores
- pass/fail reasons

This branch should be used when amino-acid sequence expansion is allowed.

### Stage 2: mRNA/Codon Branch

For protein-preserving codon optimization, skip ProteinMPNN mutation and generate synonymous CDS candidates for the same amino-acid sequence.

Candidate generation should include:

- standard codon-usage baseline
- CAI-style baseline
- random synonymous candidates under constraints
- mRNABERT-scored candidates
- optional GPT/Claude proposed candidates for benchmark only

Hard filters:

- translate back to the exact target protein
- obey start/stop requirements
- reject forbidden motifs
- reject invalid characters
- reject extreme GC windows
- reject problematic homopolymers/repeats

mRNABERT should initially be used as a scorer/reranker, not as an unconstrained generator. This avoids illegal protein-changing outputs.

### Stage 3: Candidate Scoring

For each candidate CDS, record:

| Feature | Example |
| --- | --- |
| `candidate_id` | stable id |
| `target_id` | parent target |
| `cds_sequence_hash` | dedup key |
| `translated_protein_hash` | hard-constraint check |
| `model_checkpoint` | mRNABERT checkpoint path |
| `mlm_pseudo_loss` | sequence score |
| `gc_global` | global GC |
| `gc_windows` | sliding-window GC metrics |
| `cai` | host codon-adaptation score |
| `rare_codon_count` | host-specific count |
| `motif_flags` | restriction/immune/repeat flags |
| `structure_context` | optional ESMFold2/ProteinMPNN lineage |
| `rank_score` | final weighted score |

The first version can be a rule-weighted reranker. Once wet-lab labels exist, replace or augment it with a learned reward model.

### Stage 4: Wet-Lab Batch Selection

Do not send only the top model predictions. Use a small, information-rich panel:

```text
per target:
  3-5 rule baseline candidates
  3-5 mRNABERT high-score candidates
  3-5 diverse candidates near the Pareto frontier
  1-2 intentionally weak but valid candidates as controls
```

This gives both performance measurement and training signal.

Wet-lab result table should include:

| Field | Purpose |
| --- | --- |
| `candidate_id` | joins back to design record |
| `assay_id` | experimental batch |
| `host_context` | actual context |
| `expression_value` | normalized expression |
| `stability_value` | mRNA/protein stability if measured |
| `toxicity_or_growth` | negative effect |
| `batch_effects` | plate/run metadata |
| `pass_fail_label` | thresholded label |
| `raw_artifacts` | raw measurement files |

Failed candidates are important training data and should not be discarded.

## Proposed First Sample Batch

The first sample batch should be small and diagnostic, not large.

Recommended shape:

```text
targets: 5-10 safe internal proteins
candidates per target: 12-20
total wet-lab candidates: 60-200
```

Purpose:

- verify pipeline correctness
- compare mRNABERT against rule baselines
- estimate assay noise
- find obvious failure modes
- produce first supervised labels

Decision criteria:

- Does mRNABERT reranking beat codon-rule baseline on at least some targets?
- Are failures explainable by host/context/motif constraints?
- Does model score correlate with expression/stability at all?
- Are the best candidates diverse or collapsed to one narrow sequence pattern?

## Recommended Immediate Checklist

1. Keep `checkpoint-100000` as the current primary checkpoint.
2. Create a fixed validation file of 50k-200k records.
3. Evaluate retained checkpoints on that file.
4. Select the best checkpoint by fixed eval loss.
5. Build a small codon candidate scorer around that checkpoint.
6. Run a 5-10 target dry run with all candidate records persisted.
7. Only then decide whether to continue pretraining to 150k/200k steps.

## Current Assessment

The training system is now usable. The model has clearly moved far below random MLM loss and completed a 100k-step run without numerical failure. However, we should not overclaim model quality until fixed validation and downstream candidate benchmarks are in place.

The next meaningful milestone is not "more steps"; it is:

```text
fixed validation + checkpoint comparison + first reproducible candidate batch
```

That is the bridge from pretraining infrastructure to an actual design asset.
