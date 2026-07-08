# Operational Scripts

These scripts pin the current NAS/HDFS paths used for the 2026-07-07 mRNABERT run.
Run them from the repository root or by absolute script path; no shell variables need
to be exported by the caller.

Recommended order:

```bash
scripts/sync_checkpoints_to_nas.sh
scripts/make_validation_split_nas.sh
scripts/eval_one_checkpoint_nas.sh 100000 2000
scripts/eval_one_checkpoint_nas.sh 100000
scripts/eval_checkpoints_nas.sh
scripts/print_eval_results_nas.sh
scripts/continue_train_nas.sh 150000 100000
```

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
