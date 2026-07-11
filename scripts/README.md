# Operational Scripts

These scripts pin the current NAS/HDFS paths used for the 2026-07-07 mRNABERT run.
Run them from the repository root or by absolute script path; no shell variables need
to be exported by the caller.

Recommended order:

```bash
scripts/bootstrap_streaming_state_nas.sh 600000 57600000
scripts/run_mlm_baseline_suite_nas.sh 600000 100000 42
scripts/run_mrfp_baseline_nas.sh 13 600000
scripts/run_mrfp_baseline_nas.sh 42 600000
scripts/run_mrfp_baseline_nas.sh 73 600000
scripts/print_eval_results_nas.sh
scripts/print_mrfp_results_nas.sh
scripts/run_mrfp_lr_sweep_nas.sh 600000
scripts/run_mrfp_frozen_probe_nas.sh 600000
```

`continue_train_nas.sh` uses the current measured throughput sweet spot:
3 GPUs, per-device batch 32, file-shard streaming, and 4 dataloader workers.
On streaming resume, `run_train.sh` keeps Trainer's slow data replay disabled and
uses `streaming_state.json` from the checkpoint as the authoritative raw-example
cursor. Every new checkpoint records cursor, global step, corpus pass/offset, and
the stable shard-manifest identity. Reader/shuffle/rank/worker topology is also
checked, and an incompatible resume fails fast.

Resume is cursor-correct but not constant-time. The reader scans and discards raw
lines until it reconstructs the bounded-shuffle cursor; this happens before
tokenization and GPU training. A large cursor can therefore spend several minutes
at 0% while NAS is busy. Those scanned records are not trained again.

`checkpoint-600000` predates checkpoint-level cursor persistence. Bootstrap it once
with `scripts/bootstrap_streaming_state_nas.sh 600000 57600000` before any future
resume. The `57,600,000` cursor matches the launcher's latest global-step fallback
for effective batch 96; it cannot reconstruct cursor overrides that were not saved.
Confirm it against the command used for the last resume and inspect the emitted JSON
before continuing.

For a legacy final segment, calculate the cursor as:

```text
final_cursor = logged_resume_skip_samples + (final_step - resume_step) * 96
```

Public baseline assets are pinned to Hugging Face revision
`a1eb7df25804d23f08646e1cb996b234d7208a40`. The download script verifies the model
weight SHA-256 and the Zenodo file MD5 checksums. `run_mlm_baseline_suite_nas.sh`
compares MLM loss on the existing proxy validation set; this set leaked into the
original training corpus, so use it only as a quick diagnostic. The mRFP scripts
are the first task-level comparison and should be run over multiple seeds. They
remove exact cross-split leakage and compare the internal model, public model, and
a same-architecture random initialization.

After the first three-seed result, `run_mrfp_lr_sweep_nas.sh` gives both learned
encoders the same missing full-fine-tuning LR trials (`2e-5`, `5e-5`; the original
`1e-4` results are retained). `run_mrfp_frozen_probe_nas.sh` freezes embeddings and
Transformer blocks while training the newly initialized pooler and regression head
at `1e-4`, `3e-4`, and `1e-3`. Result names encode mode and LR, and the summarizer
labels legacy results as `full-lr1e-4`. The summary prints each run's best dev
Spearman separately from test metrics. Select recipes using dev results; the test
values from the completed LR sweep have already been inspected and are exploratory,
not an untouched confirmatory comparison.

Throughput checks:

```bash
scripts/benchmark_throughput_nas.sh smoke
scripts/benchmark_throughput_nas.sh quick
scripts/print_throughput_benchmark_nas.sh /mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_runs/benchmarks/<benchmark-dir>
scripts/throughput_workers4_nas.sh
scripts/throughput_workers0_nas.sh
```

Final archival back to HDFS:

```bash
scripts/archive_nas_run_to_hdfs.sh
```

The eval scripts intentionally use streaming mode and NAS-local cache/output paths.
They should not print `Tokenizing mRNA records (num_proc=8)`. If that message appears,
the run is using Arrow tokenization instead of streaming and should be stopped.
