# mRNABERT Baseline Experiment Protocol and Results

Date: 2026-07-11

## Current checkpoint

The internal scratch run reached global step 600000. Its latest fixed-proxy result
is:

```text
eval_loss: 2.2949452400
perplexity: 9.9238925653
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

## Observed results

### Proxy MLM

Both models were evaluated on the same 100,000-record proxy file with masking seed
42 and a 1024-token input limit.

| Model | Eval loss | Perplexity | Samples/s |
|---|---:|---:|---:|
| Internal checkpoint 600000 | 2.294945 | 9.923893 | 135.638 |
| Public `YYLY66/mRNABERT` | **2.046072** | **7.737445** | 124.683 |

The public model has 0.2489 lower MLM loss and about 22% lower perplexity. This is a
clear proxy-MLM win for the public checkpoint, but not a clean generalization result:
the proxy was extracted from the internal training source after training began, and
the public checkpoint was trained on a cleaned subset of the same public corpus
family.

### mRFP synonymous-codon expression prediction

The table reports held-out test metrics after exact cross-split de-duplication and an
equal learning-rate grid. Values are mean +/- sample standard deviation over seeds
13, 42, and 73.

| Model | LR | Spearman | Pearson | R2 | MSE |
|---|---:|---:|---:|---:|---:|
| Internal checkpoint 600000 | `2e-5` | 0.8645 +/- 0.0050 | 0.8971 +/- 0.0151 | 0.7559 +/- 0.0513 | 0.1358 +/- 0.0285 |
| Internal checkpoint 600000 | `5e-5` | 0.8648 +/- 0.0073 | 0.8973 +/- 0.0041 | 0.7605 +/- 0.0142 | 0.1332 +/- 0.0079 |
| Internal checkpoint 600000 | `1e-4` | 0.8372 +/- 0.0195 | 0.8882 +/- 0.0140 | 0.7690 +/- 0.0204 | 0.1285 +/- 0.0114 |
| Public `YYLY66/mRNABERT` | `2e-5` | 0.8703 +/- 0.0122 | 0.9022 +/- 0.0150 | 0.8054 +/- 0.0279 | 0.1082 +/- 0.0155 |
| Public `YYLY66/mRNABERT` | `5e-5` | 0.8652 +/- 0.0066 | 0.8991 +/- 0.0106 | 0.7911 +/- 0.0258 | 0.1162 +/- 0.0144 |
| Public `YYLY66/mRNABERT` | `1e-4` | 0.5238 +/- 0.3647 | 0.4389 +/- 0.4281 | 0.2634 +/- 0.4540 | 0.4098 +/- 0.2526 |
| Same-architecture random initialization | `1e-4` | NaN | NaN | -12.8498 +/- 3.0793 | 7.7046 +/- 1.7130 |

The equal-budget sweep changes the initial interpretation. At `2e-5` and `5e-5`,
the public model is stable and matches or slightly exceeds the internal model. The
best observed rank-correlation means differ by only 0.0055, which is not evidence of
a meaningful ranking advantage with three seeds. The public model has the stronger
calibrated regression result (R2 0.805 and MSE 0.108 at `2e-5`). The internal model
is less sensitive to learning rate, while the public checkpoint is damaged by the
`1e-4` full-fine-tuning setting.

This LR sweep is exploratory because test metrics were inspected at every setting.
The summarizer now reports each run's best dev Spearman; any formal model/LR choice
must be made from dev results before treating test metrics as confirmatory.

The random model produced near-constant predictions, making rank correlations
undefined and strongly negative R2. Under this training budget, the supervised head
cannot recover the task from roughly one thousand labels without pretrained
sequence representations.

### Interpretation

1. The internal pretrained weights contain real transferable signal for synonymous
   codon expression ranking; the result is not explained by a randomly initialized
   86.5M-parameter encoder learning the small downstream set.
2. Proxy MLM loss and downstream utility rank the models differently. We must not
   optimize or select the product encoder using MLM loss alone.
3. The equal-budget LR sweep does not support intrinsic superiority for the internal
   representation. A frozen-encoder probe is still needed to isolate representation
   quality from full-fine-tuning behavior.
4. mRFP is a single-protein synonymous library. It directly tests codon-expression
   relationships within one protein, not transfer across target proteins.
5. Exact train/dev/test duplicates were removed, but pretraining-to-downstream and
   near-duplicate leakage have not yet been ruled out.

## External-facing statement

The following wording is supported for a business plan or technical presentation:

> We trained an 86.5M-parameter codon-aware Transformer from random initialization
> using a corpus containing approximately 36.25 million mRNA records, creating
> internally owned model weights and a reproducible training asset. On the public
> mRFP synonymous-codon expression benchmark, after exact split de-duplication, the
> model reached test Spearman 0.865 +/- 0.007 over three seeds at its best observed
> lower-LR setting. Under an equal learning-rate search budget, its ranking result was
> competitive with the pinned public mRNABERT baseline (0.870 +/- 0.012), while a
> same-architecture random initialization failed to learn a useful ranking signal.
> These exploratory results are initial evidence that the internally trained weights
> capture transferable codon-expression relationships at a level comparable to the
> public checkpoint on this task. Cross-protein, temporal external-set, and wet-lab
> validation are in progress.

Required footnote:

```text
Public single-protein mRFP synonymous-codon dataset; cleaned split sizes
train/dev/test = 1018/219/219; full fine-tuning with seeds 13/42/73 and an exploratory
LR grid; test metrics were inspected across LR settings; no wet-lab or cross-protein
validation yet; pretraining-to-downstream near-duplicate leakage has not been
excluded.
```

Do not claim that this experiment demonstrates an original Transformer architecture,
general superiority over the public model, improved wet-lab expression, cross-target
generalization, or a proprietary-data moat. The architecture is BERT-derived, the
pretraining corpus and benchmark are primarily public, and the tuned public baseline
matches the internal model on rank correlation while retaining better R2/MSE.

## Decision and next experiments

- Freeze checkpoint 600000 as the current internal candidate; do not add blind
  pretraining steps based only on the leaked proxy curve.
- Run a frozen-encoder linear probe to separate representation quality from
  full-fine-tuning optimization stability.
- Add GC, CAI, 64-codon-frequency, and simple k-mer regression baselines. A learned
  encoder must beat these before supporting a codon-design product claim.
- Scan mRFP exact CDS and translated-protein identity against the pretraining corpus,
  then add a second-protein or multi-protein benchmark.
- Build the clean 2026 temporal holdout and begin wet-lab-linked candidate testing.

The first two controlled follow-ups are implemented as reusable NAS scripts:

```bash
scripts/run_mrfp_lr_sweep_nas.sh 600000
scripts/run_mrfp_frozen_probe_nas.sh 600000
scripts/print_mrfp_results_nas.sh
```
