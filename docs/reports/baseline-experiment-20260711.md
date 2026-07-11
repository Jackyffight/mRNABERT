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

The table reports held-out test metrics after exact cross-split de-duplication under
one fixed full-fine-tuning recipe. Values are mean +/- sample standard deviation over
seeds 13, 42, and 73.

| Model | Spearman | Pearson | R2 | MSE |
|---|---:|---:|---:|---:|
| Internal checkpoint 600000 | **0.837 +/- 0.019** | **0.888 +/- 0.014** | **0.769 +/- 0.020** | **0.128 +/- 0.011** |
| Public `YYLY66/mRNABERT` | 0.524 +/- 0.365 | 0.439 +/- 0.428 | 0.263 +/- 0.454 | 0.410 +/- 0.253 |
| Same-architecture random initialization | NaN | NaN | -12.850 +/- 3.079 | 7.705 +/- 1.713 |

Internal per-seed Spearman was 0.824, 0.860, and 0.827. The public model was highly
seed-sensitive at 0.129, 0.593, and 0.849. Its best run matched the internal range
and slightly improved some absolute-error metrics, so this experiment does not show
that the public representation has a lower capability ceiling. It shows that the
internal checkpoint is materially more stable under this fixed optimization recipe.

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
3. Public-model instability requires an equal-budget learning-rate sweep and a
   frozen-encoder linear probe before claiming intrinsic representation superiority.
4. mRFP is a single-protein synonymous library. It directly tests codon-expression
   relationships within one protein, not transfer across target proteins.
5. Exact train/dev/test duplicates were removed, but pretraining-to-downstream and
   near-duplicate leakage have not yet been ruled out.

## External-facing statement

The following wording is supported for a business plan or technical presentation:

> We trained an 86.5M-parameter codon-aware Transformer from random initialization
> using a corpus containing approximately 36.25 million mRNA records, creating
> internally owned model weights and a reproducible training asset. On the public
> mRFP synonymous-
> codon expression benchmark, after exact split de-duplication, the model achieved
> test Spearman 0.837 +/- 0.019, Pearson 0.888 +/- 0.014, and R2 0.769 +/- 0.020 over
> three random seeds. Under the same fixed fine-tuning protocol, it showed a higher
> average score and substantially better stability than the pinned public mRNABERT
> baseline, while a same-architecture random initialization failed to learn a useful
> ranking signal. These results are initial evidence that the pretrained weights
> capture transferable codon-expression relationships. Cross-protein, temporal
> external-set, and wet-lab validation are in progress.

Required footnote:

```text
Public single-protein mRFP synonymous-codon dataset; cleaned split sizes
train/dev/test = 1018/219/219; full fine-tuning with seeds 13/42/73; no wet-lab or
cross-protein validation yet; pretraining-to-downstream near-duplicate leakage has
not yet been excluded.
```

Do not claim that this experiment demonstrates an original Transformer architecture,
general superiority over the public model, improved wet-lab expression, cross-target
generalization, or a proprietary-data moat. The architecture is BERT-derived, the
pretraining corpus and benchmark are primarily public, and the public baseline's best
seed matched the internal model.

## Decision and next experiments

- Freeze checkpoint 600000 as the current internal candidate; do not add blind
  pretraining steps based only on the leaked proxy curve.
- Give the internal and public models an equal learning-rate search budget at
  `2e-5`, `5e-5`, and `1e-4`, then repeat multiple seeds.
- Run a frozen-encoder linear probe to separate representation quality from
  full-fine-tuning optimization stability.
- Add GC, CAI, 64-codon-frequency, and simple k-mer regression baselines. A learned
  encoder must beat these before supporting a codon-design product claim.
- Scan mRFP exact CDS and translated-protein identity against the pretraining corpus,
  then add a second-protein or multi-protein benchmark.
- Build the clean 2026 temporal holdout and begin wet-lab-linked candidate testing.
