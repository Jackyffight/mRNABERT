# mRNABERT

**A codon-aware mRNA language model, and the mRNA-regulation layer of a protein-to-mRNA design system.**

Release: `v0.1.0` — encoder foundation (Phase 0). · Stage: research / bench.

mRNABERT is a BERT-style masked-language-model encoder for mRNA and CDS sequences.
It tokenizes untranslated regions as single bases and coding regions as codons, so
the model reasons in the same units biology does. This repository ships the encoder
and its training/fine-tuning stack today; it is the first built piece of a larger,
mostly-planned system that turns a target protein into manufacturable, constraint-
satisfying mRNA. What is built and what is planned is stated plainly below — do not
read the roadmap as delivered capability.

The audited Internal-600k vs public mRNABERT comparison, Evo 2 probe status, and
reproducible Python figure are documented in
[`docs/reports/model-comparison-20260716.md`](docs/reports/model-comparison-20260716.md).

---

## Project status (read this first)

| Layer | What | Status |
|---|---|---|
| **Phase 0 — mRNA encoder** | codon-aware tokenizer, MLM pretraining (single- and multi-GPU streaming), fine-tuning heads | **Built & hardened** (this repo) |
| Phase 1 — tool pipeline | ESMFold2 / ProteinMPNN wrappers, translation-preserving mRNA candidate generator, rule baseline | Planned |
| Phase 2 — reward & reranker | supervised/pairwise mRNA scoring, Pareto reranker | Planned |
| Phase 3 — reasoning traces | tool-augmented design policy trained from run traces | Planned |
| Phase 4 — active learning | wet-lab queue, assay ingestion, reward retraining | Planned |
| Phase 5 — end-to-end policy | learned design policy with selective expert calls | Planned |

The full plan is in [`ROADMAP.md`](ROADMAP.md); the target architecture is in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). **Only Phase 0 exists in code.**

## What is ours, what we build on

- **Architecture and published weights** come from the paper *mRNABERT: advancing
  mRNA sequence design with a universal language model and comprehensive dataset*
  (Xiong et al., *Nature Communications* 2025), whose modeling framework derives
  from DNABERT-2. The public checkpoint is `YYLY66/mRNABERT` on Hugging Face and the
  74-token codon vocabulary is theirs.
- **Ours** is the production training layer around it: a hardened, unit-tested DDP
  streaming data plane; a single-source-of-truth sequence codec; run-manifest
  lineage; and the protein-to-mRNA design-system roadmap and architecture. We can
  train an encoder from scratch on our own corpus (the default) or continue from the
  published weights as a baseline.

## Repository layout

| Path | What |
|---|---|
| `main.py` | CLI: `pretrain` and `preprocess` subcommands |
| `mrnabert/sequence_codec.py` | FASTA parsing, longest-ORF CDS detection, codon/UTR tokenization (torch-free, the single source of truth) |
| `mrnabert/streaming.py` | torch-free DDP shard / shuffle / cap helpers (unit-tested without a GPU stack) |
| `mrnabert/modeling.py` | masked-LM model + tokenizer loading (scratch vs pretrained) |
| `mrnabert/pretrain.py` | stage-1 MLM `Trainer` orchestration |
| `run_train.sh` | multi-GPU launcher (single-node DDP via `torchrun`, auto-sharding) |
| `regression.py`, `classification.py` | fine-tuning heads (optional LoRA) |
| `data_process/` | FASTA/CSV preprocessing (thin wrappers over the codec) |
| `assets/mrnabert-base/` | local BERT config + tokenizer + vocab used for scratch init |
| `sample_data/` | tiny templates: `pre.txt` (pretrain) and `fine-tune/mRFP/*.csv` |
| `tests/` | codec, streaming, preprocessing-parity, and loader tests |
| `docs/` | architecture and design notes |

## Install

Requires Python 3.8+ and a CUDA/PyTorch runtime matched to your machine (PyTorch is
intentionally not pinned). `transformers`, `peft`, and `accelerate` are pinned to the
versions the training and fine-tune scripts were validated against; the rest are
unpinned:

```bash
pip install -r requirements.txt
```

The torch-free core (`mrnabert.sequence_codec`, `mrnabert.streaming`) and the test
suite for it run under any `python3` with no ML dependencies installed.

## Quickstart

```bash
# Unit tests — codec, streaming invariants, preprocessing parity (no torch needed):
python -m unittest discover -s tests

# Preprocess a directory of FASTA files into pretraining text (streaming):
python main.py preprocess --raw-dir raw --output-dir data/pretrain --workers 32

# Pretrain the MLM encoder from scratch (the default) on the local config + vocab:
python main.py pretrain \
  --output_dir output/pre/run- \
  --model_name_or_path assets/mrnabert-base \
  --init_mode scratch \
  --do_train --line_by_line --fp16 \
  --train_file sample_data/pre.txt \
  --per_device_train_batch_size 32 --gradient_accumulation_steps 4

# Multi-GPU single-node. --max-steps enables streaming, which auto-shards pre.txt into
# one shard per process (without it the run falls back to Arrow tokenization, unsuitable for a large corpus):
./run_train.sh --env devbox --train-file /path/pre.txt --launcher torchrun --devices 0,1,2 --max-steps 100000

# Build a leakage-free validation split (hash-based; train from the complement for a clean holdout):
python data_process/make_validation_split.py \
  --input /path/pre.txt --val-out valid.txt --train-out train_holdout.txt --val-fraction 0.01 --seed 42

# Evaluate a checkpoint on the fixed validation file (MLM loss + perplexity; select by this, not train loss):
python main.py pretrain --do_eval --init_mode pretrained \
  --model_name_or_path /path/output/checkpoint-100000 \
  --validation_file valid.txt --output_dir /tmp/eval-ckpt-100000

# Continue from the published checkpoint instead of scratch (baseline runs):
python main.py pretrain --model_name_or_path YYLY66/mRNABERT --init_mode pretrained \
  --attention_backend remote-safe --train_file sample_data/pre.txt --do_train

# Fine-tune (regression or classification; add --use_lora true for LoRA):
python regression.py     --model_name_or_path YYLY66/mRNABERT --data_path sample_data/fine-tune/mRFP --model_max_length 250 ...
python classification.py --model_name_or_path YYLY66/mRNABERT --data_path <dir>              --model_max_length 250 ...
```

Fine-tuning hyperparameters (`model_max_length`, `batch_size`, `num_train_epochs`,
`eval_steps`) are dataset-specific — tune them per dataset.

## How sequences are tokenized

One whitespace-separated mRNA per line: **UTR → single bases, CDS → codons**, after
normalizing each sequence (uppercase, `U → T`). The CDS is approximated as the
longest in-frame ORF (`ATG … in-frame stop`) — a deliberately narrow heuristic;
prefer curated RefSeq/GENCODE coordinates when you have them. The vocabulary is
exactly 74 tokens: 5 special + `A T C G N` + 64 codons.

All four entry points — `main.py preprocess`, `data_process/process_pretrain_data.py`,
`data_process/process_finetune_data.py`, and fine-tuning input — go through
`mrnabert.sequence_codec`, so no two preprocessing paths can drift.

## Training internals worth knowing

- **`init_mode` defaults to `scratch`**: it initializes weights randomly from the
  local `assets/mrnabert-base` config. It does **not** load `YYLY66/mRNABERT`. Use
  `--init_mode pretrained` for explicit baseline/continuation runs.
- **Streaming DDP**: three readers select via `--streaming_reader` — `line-stride`
  (default), `file-shard` (rank-assigned shard files, what `run_train.sh --auto-shard`
  produces), and `byte-range` (seek-based). All shard the corpus with no overlap or
  drop; the invariants are unit-tested in `tests/test_streaming.py`. A `file-shard`
  run with fewer shard files than ranks now fails fast instead of deadlocking.
- **`--max_train_samples` is a global cap** under streaming: it is divided across
  the rank×worker partitions so the total consumed is ~= the number you asked for,
  matching the non-streaming behavior.
- Every pretraining run writes `run_manifest.json` (args, tokenizer, library versions)
  for lineage, and logs the effective LR at train start (on resume the real LR comes
  from restored optimizer/scheduler state, not the configured `--learning_rate`).
- **Throughput:** keep `--dataloader-workers > 0` (default 4). The streaming readers
  are worker-aware, so extra workers overlap CPU tokenization with GPU compute; a run
  with 0 workers is CPU-bound and wastes most of the GPU.

## Data and weights

- Pretrained encoder: [`YYLY66/mRNABERT`](https://huggingface.co/YYLY66/mRNABERT) on
  Hugging Face.
- mRNA corpora and downstream datasets: Zenodo records
  [12516160](https://zenodo.org/records/12516160) (36M+ mRNA/CDS sequences) and
  [17786045](https://zenodo.org/records/17786045) (downstream fine-tuning sets).
  Pretraining input needs CDS prediction (e.g. NCBI ORFfinder) before the codon
  split; `sample_data/` holds only small templates.

## Safety

Codon and mRNA optimization is dual-use. The design system in the roadmap is built
around a safety-and-feasibility gate (restricted-family screening, deny/review flows,
provenance logging) as a hard product requirement, not a bypass of general-model
refusals. The encoder in this repo is a representation model; the gate lands with the
generation/optimization phases. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## License and citation

Built on the mRNABERT model and the DNABERT-2 framework. If you use this work,
please cite the original paper:

```bibtex
@article{xiong2025mrnabert,
  title={mRNABERT: advancing mRNA sequence design with a universal language model and comprehensive dataset},
  author={Xiong, Ying and Wang, Aowen and Kang, Yu and Shen, Chao and Hsieh, Chang-Yu and Hou, Tingjun},
  journal={Nature Communications},
  volume={16}, number={1}, pages={10371}, year={2025},
  publisher={Nature Publishing Group UK London}
}
```
