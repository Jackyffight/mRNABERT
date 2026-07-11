# mRNABERT Baseline Experiment Protocol

Date: 2026-07-11

## Current checkpoint

The internal scratch run reached global step 600000. Checkpoint 480000 is the most
recent checkpoint with a reported fixed-proxy result:

```text
eval_loss: 2.3140273094
perplexity: 10.1150792898
```

The proxy validation set is not a clean holdout: it was split from `pre.txt` after
the run had already trained on that source. Use it for checkpoint diagnostics, not
as evidence of external generalization.

## Public baseline

Pinned model:

```text
model: YYLY66/mRNABERT
revision: a1eb7df25804d23f08646e1cb996b234d7208a40
weight bytes: 455973118
weight sha256: cb2eb64831a494d4cac14acb5df908f734e088c4d62256ac3e42cada60c3bf75
```

The public model card states that the checkpoint used a cleaned 18M-sequence subset
of the 36M public corpus and incorporated amino-acid semantic contrastive learning.
The internal checkpoint uses the same 74-token vocabulary family but a standard
86.5M-parameter BERT MLM initialized from scratch. This is a product-level baseline,
not a controlled single-variable architecture ablation.

Official sources:

- Hugging Face: `https://huggingface.co/YYLY66/mRNABERT`
- Pretraining corpus: `https://zenodo.org/records/12516160`
- Downstream datasets: `https://zenodo.org/records/17786045`

Downloaded downstream packages:

| File | Size | Checksum |
|---|---:|---|
| `full_length.zip` | 281158 bytes | MD5 `3652178c257341010800e2d241a9c258` |
| `te_ultra_full_length.zip` | 32611876 bytes | MD5 `939b495793687db362d4b9464a5df570` |

## Experiment order

### 1. Bootstrap the legacy 600k cursor

```bash
scripts/bootstrap_streaming_state_nas.sh 600000 57600000
```

The cursor assumes effective batch 96 and the latest launcher fallback. Inspect the
written `streaming_state.json` before any future resume. Legacy checkpoints did not
store cursor overrides, so this value is only correct if the latest resume used the
fallback printed by `run_train.sh`; it cannot be recovered from model weights.
Future resumes validate shard and reader topology. They may still spend minutes
scanning raw NAS lines to rebuild the bounded-shuffle cursor, but skipped lines are
discarded before tokenization and do not become repeated optimizer samples.

For any legacy last segment, use:

```text
final_cursor = logged_resume_skip_samples + (final_step - resume_step) * 96
```

### 2. Proxy MLM comparison

```bash
scripts/run_mlm_baseline_suite_nas.sh 600000 100000 42
```

Primary outputs are MLM loss, perplexity, runtime, and samples/second under the same
mask seed and 1024-token input limit.

### 3. mRFP expression comparison

```bash
scripts/run_mrfp_baseline_nas.sh 13 600000
scripts/run_mrfp_baseline_nas.sh 42 600000
scripts/run_mrfp_baseline_nas.sh 73 600000
scripts/print_mrfp_results_nas.sh
```

Report mean, standard deviation, and each seed for Spearman, Pearson, R2, and MSE.
The suite compares the internal checkpoint, pinned public checkpoint, and a
same-architecture random initialization. Do not select a winner from one seed.

The source mRFP splits contain 1021/219/219 train/dev/test rows. Exact de-duplication
with test, then dev, then train priority removes two train rows that overlap a
higher-priority split and one duplicate within train, leaving 1018/219/219. All
records are 226 tokens, so the 250-token limit does not truncate this benchmark.

### Downloaded-data audit

- The seven small `full_length` regression tasks have `dev.csv` byte-identical to
  `test.csv`. They must be re-split before anyone reports independent dev/test
  metrics.
- The TE human and mouse sets are useful later, but roughly 80% of records exceed
  the current 1024-token context. Running them now would mostly measure truncation;
  keep them for the planned long-context model comparison.

### 4. Clean external evaluation

Expand `2026_corpus/` beyond its ten-record smoke set. Use annotated CDS coordinates,
then exact- and near-deduplicate against the original pretraining corpus. Keep human
and non-human slices separate. The public and internal models must be evaluated on
the identical frozen output and masking seeds.

## Decision rule

- If checkpoint 600k improves clean MLM and downstream metrics relative to older
  internal checkpoints, continuation remains justified.
- If the public model wins MLM but the internal model wins downstream, prefer the
  internal encoder for the product task and stop optimizing only MLM loss.
- If the public model wins both by a material margin, do not add blind steps; isolate
  architecture size and contrastive-objective differences first.
- If both learned models fail to beat CAI/GC/codon-frequency baselines downstream,
  prioritize labels and objectives over additional foundation pretraining.
