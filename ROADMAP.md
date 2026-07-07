# mRNABERT Roadmap

This is the authoritative plan. The design essays under `docs/reports/` are the
thinking behind it; where they and this file disagree, **this file wins**, and
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) is the canonical target architecture.

Last updated: 2026-07-07.

## Thesis

We are not trying to build a small model that out-argues a general LLM on "give me a
codon-optimized CDS". A general model will keep getting better at that single-shot
task. The defensible thing is a **controlled, auditable, batch design loop**: turn a
target protein into many constraint-satisfying mRNA candidates, screen them
computationally, test the best in the lab, and feed the results back into the
scorers. The moat is the experiment-linked data and the reproducible provenance, not
model size. mRNABERT is one component of that loop — the mRNA-regulation encoder —
not the whole product.

## Where we are

**Phase 0 (mRNA encoder) is built and hardened.** Concretely, this repo ships:

- A single-source-of-truth sequence codec (codon/UTR tokenization, longest-ORF CDS,
  `U→T` normalization) with all preprocessing paths routed through it.
- MLM pretraining from scratch or from the published checkpoint, single-GPU and
  multi-GPU (DDP via `torchrun`) with three streaming readers whose shard/shuffle/cap
  invariants are unit-tested without a GPU.
- Fail-fast guards against the DDP starvation deadlock, a global `max_train_samples`
  cap, run-manifest lineage, and fine-tuning heads (regression/classification, LoRA).

**Everything past Phase 0 is design, not code.** There are no ESMFold2/ProteinMPNN
wrappers, no mRNA candidate generator, no reward model, no reasoner, and none of the
data tables materialized. Treat the phases below as a plan to execute, not a
description of the system.

## Immediate next step (post-100k pretraining run)

The first full-corpus scratch run reached global step 100000 (~26.5% of one pass over
36.2M records) on 3×A100 with the file-shard streaming path
([run record](docs/reports/mrnabert-pretraining-run-20260707.md)). It is an
infrastructure milestone, not a converged model. The correct next step is **not**
another blind long run — it is to make model quality measurable, in this order:

1. **Re-measure throughput (fixed, verify the win).** That run trained with
   `dataloader_num_workers=0` and hit ~8% A100 MFU — it was CPU-tokenization bound,
   not compute bound. The launcher no longer forces workers to 0 (defaults to 4), so
   a short re-measure should show materially higher throughput. Getting throughput up
   matters more than any LR tweak, because at 26.5% of an epoch the model is data-
   starved, not schedule-limited: **finishing an epoch beats lowering the LR.**
2. **Build a fixed, leakage-free validation set.** Use
   `data_process/make_validation_split.py` to hash-split off 50k–200k records; for a
   clean holdout, *train from the emitted `--train-out` complement*, not the original
   `pre.txt`. (Evaluating the existing checkpoint against this split is only a proxy —
   it was trained on the full corpus, so a fraction of val was already seen.)
3. **Evaluate retained checkpoints on that fixed file** (`--do_eval` without
   `--do_train`, `--init_mode pretrained --model_name_or_path <checkpoint>`), report
   MLM eval loss + perplexity, and select the next base checkpoint by **validation
   loss, not train loss**. (The run's aggregate `train_loss` and
   `train_samples_per_second` are both resume-distorted; ignore them. Confirm the
   actual resumed LR from the new `LR/schedule at train start` log line rather than
   assuming the configured `--learning_rate` held.)
4. **Then decide continue vs pivot.** If validation still improves, continue from the
   best checkpoint at a low LR toward one full epoch; if it plateaus, stop pretraining
   and move to Phase 1 (candidate generation + rule baseline).

Data hygiene to fold in before trusting eval numbers: a **near-duplicate pass** over
the multi-species corpus (orthologs/paralogs inflate apparent learning), and always
holding out validation by hash *before* training so exact duplicates cannot leak.

## Two preconditions that gate the whole plan

These are not phases; they are gates. The roadmap past Phase 1 is not credible until
both are answered, and we should not claim the batch-throughput or data-moat
advantages before then.

1. **A compute/cost model.** The entire competitive claim is *hit rate per N
   candidates*, and that economics is dominated by ESMFold2 fold + refold cost per
   candidate. We need GPU-hours, dollars, and latency per 1,000 candidates at a
   chosen refold budget before designing around batch throughput. This is currently
   an open question, not a number.
2. **A wet-lab data source.** Experiment-linked data is the only durable moat we
   claim. We need a concrete source — a partner LOI, an in-house assay, or a named
   public dataset — with a timeline. Proxy labels are a bootstrap, not the moat; by
   our own analysis a proxy-only system collapses back into "a better candidate
   generator" with no durable edge.

## Phases

### Phase 0 — mRNA encoder — DONE

First full-corpus run complete (100k steps); tooling for the next step is in place:
the hash-based validation split (`data_process/make_validation_split.py`), effective-
LR-at-start logging, and the dataloader-throughput fix. Remaining polish, not
blockers: validation loss dashboards over the checkpoint series; a cached
(non-streaming) tokenized dataset path for small corpora; checkpoint-lineage tags;
a corpus near-duplicate pass.

### Phase 1 — Tool pipeline and schemas

- Define the typed **design state** and the four auditable tables (design-state,
  axis/geometry, candidate, experiment) as real schemas, not markdown.
- Implement a **translation-preserving mRNA candidate generator** with a hard check:
  every emitted CDS must translate back to the exact input protein.
- Add a **rule-based codon-optimizer baseline** (GC window, codon-usage table,
  forbidden-motif removal) — the honest floor every learned model must beat.
- Wrap **ESMFold2** (fold + refold, pinned revision + weight checksum + seed) and
  **ProteinMPNN** (constrained sequence expansion) as frozen expert tools with
  recorded provenance.

Exit criterion: a target protein flows end-to-end to scored mRNA candidates through
rule-based scoring, with a full provenance trace, on a real example.

### Phase 2 — Reward model and reranker

- Extract mRNABERT embeddings; add supervised or pairwise-preference mRNA scoring
  heads (proxy labels first if wet-lab labels are not yet available — see gate #2).
- Multi-objective reward kept component-wise visible (expression, stability,
  translation efficiency, structure-pass, safety, manufacturability) with a **Pareto
  reranker**, not a single hidden scalar.

Set expectations for the first zero-shot scorer: ranking synonymous CDS by mRNABERT
MLM pseudo-log-likelihood rewards "codon-typical" sequences, which is **highly
correlated with a CAI / codon-usage baseline**. So the first experiment may show
mRNABERT ≈ CAI on expression — that is expected, not failure. mRNABERT's real
increment (context, local structure, motif effects) only shows once a head is trained
on labels; don't read an early tie with CAI as "the model is useless".

Exit criterion: mRNABERT-based ranking measurably beats the Phase 1 rule baseline on
held-out labels (top-k enrichment, pairwise accuracy).

### Phase 3 — Reasoning traces

- Every design run writes a reasoning-trace table (state, action, tool revision,
  observation artifact, cost, decision reason).
- Train a next-action / branch-value model from traces so tool calls become
  uncertainty-driven rather than a fixed pipeline.

### Phase 4 — Active learning (requires gate #2)

- Diversity + uncertainty candidate selection into a wet-lab queue; structured assay
  ingestion; periodic reward-model retraining; report hit-rate improvement and cost
  per validated candidate.

### Phase 5 — End-to-end design policy

- Distill expensive expert calls into fast surrogate heads; a learned policy proposes
  candidates directly and calls ESMFold2/ProteinMPNN only for uncertain cases.
  Benchmark against the static pipeline and an LLM baseline in safe design spaces.

## Realistic near term

A focused two-to-three-week slice, achievable with only the encoder in hand and no
external-tool integration yet:

1. Write the design-state and candidate/experiment schemas.
2. Implement the translation-preserving mRNA candidate generator + hard check.
3. Ship the rule-based codon-optimizer baseline and an offline eval harness
   (protein-preservation rate, GC compliance, forbidden-motif compliance, diversity).

The ESMFold2 and ProteinMPNN integrations (each non-trivial) and any reward head
trained on real labels come **after** the two gates are answered. An "8-week
end-to-end MVP" is not realistic while Phases 1–5 are unbuilt and gate #2 is open;
scope to the slice above and expand as the gates clear.

## Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| mRNABERT stays MLM-only | Weak downstream value | Add supervised/ranking heads (Phase 2) |
| No compute/cost model | Batch-throughput claim is unfounded | Gate #1 — measure fold/refold cost per 1,000 candidates before scaling |
| No wet-lab data | No durable moat; proxy-only collapses to a generator | Gate #2 — commit a source with a timeline |
| Design intent lost before ProteinMPNN | Candidates drift from objective | Persist the geometry/axis table; feed constraints explicitly |
| ESMFold2 treated as a trainable asset | Overclaim / reproducibility risk | Keep it a pinned frozen evaluator with provenance |
| Biosecurity / dual use | Product & legal risk | Safety-feasibility gate before any optimization phase |
| Doc-vs-code gap misread as delivered | Overclaim to stakeholders | This roadmap and the README label built vs planned explicitly |
| General LLMs improve | Single-shot optimization commoditized | Compete on constraints, batch hit rate, auditability, and feedback loop |

## Positioning

Do **not** position mRNABERT as "a small model that beats GPT/Claude at codon
optimization." Position the project as a controlled protein-to-mRNA design pipeline —
frozen structural evaluators + trainable sequence/mRNA modules + wet-lab feedback —
that produces auditable, constraint-satisfying candidates. That framing is both more
honest and more defensible.
