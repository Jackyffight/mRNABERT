# mRNABERT Roadmap

This is the authoritative plan. The design essays under `docs/reports/` are the
thinking behind it; where they and this file disagree, **this file wins**, and
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) is the canonical target architecture.

Last updated: 2026-07-11.

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

## Immediate next step (post-600k pretraining run)

The scratch run reached global step 600000 on 3×A100 and now has persistent streaming
lineage. On the leaked MLM proxy, the public checkpoint wins clearly (`2.0461` vs
`2.2949`). On the exact-de-duplicated mRFP task, an equal learning-rate sweep shows
that the internal and public checkpoints have comparable ranking performance at
their best lower-LR settings (Spearman `0.865 +/- 0.007` vs `0.870 +/- 0.012`). The
public checkpoint has better calibrated regression (`R2 0.805`, `MSE 0.108`), while
the internal checkpoint is less sensitive to the tested learning rate and random
initialization collapses. The sweep is exploratory because test metrics were
inspected at every LR. See the
[full baseline report](docs/reports/baseline-experiment-20260711.md). Freeze 600k and
proceed in this order:

1. **Make model selection confirmatory.** Use dev metrics, not the already inspected
   test metrics, to choose LR/model; then confirm once on a new untouched split or
   external set. Run the frozen-encoder linear probe to isolate representation
   quality from full-fine-tuning optimization behavior.
2. **Establish the honest feature floor.** Add GC, CAI, 64-codon-frequency, and
   k-mer regression baselines. The Transformer must beat these before we claim
   useful learned codon-design signal.
3. **Audit pretraining leakage and cross-protein transfer.** Search mRFP CDS/protein
   identities against the 36M corpus and add a second-protein or multi-protein task.
4. **Build a genuinely clean holdout.** Expand the annotation-first 2026 RefSeq
   corpus, exact- and near-deduplicate it against the 36M training source, and keep a
   date/species-stratified external set. Re-evaluate retained internal checkpoints
   and the public model there.
5. **Move toward product evidence.** Use checkpoint 600k as the current encoder in
   Phase 1 candidate-ranking experiments, but reserve product claims for cross-
   protein and wet-lab-linked results.

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

### Phase 0 — mRNA encoder — IMPLEMENTED; VALIDATION OPEN

The scratch run reached 600k steps; checkpoint-level streaming cursor and shard/
reader topology are now persisted and validated. The internal encoder has a
competitive three-seed mRFP result, but the tuned public checkpoint is at least as
strong on this single-protein task. Cross-protein, pretraining-leakage, and clean
model-selection checks remain Phase 0 exit evidence rather than optional polish.

Current training stance after the 600k run and baseline comparison:

- Keep the formal baseline at `max_seq_length=1024`; do not switch production
  pretraining to 512 merely for speed. A 512 run is useful only as a diagnostic
  ablation for attention cost and context-length sensitivity.
- Use the measured NAS/DDP sweet spot for continuation runs: 3 GPUs, per-device
  batch 32, file-shard streaming, bounded shuffle, and 4 dataloader workers. The
  GPU `dmon` trace showed high SM utilization, so the next large speedups are likely
  model-kernel/architecture work rather than NAS read tuning.
- Track ModernBERT-style encoder upgrades and long-sequence Transformer variants as
  architecture iteration candidates, but keep them behind the current 1024-BERT MLM
  baseline until they beat it on the same validation set.
- Do not continue blind pretraining from 600k. First use dev-selected recipes, run
  the frozen linear probe, add feature baselines, and complete cross-protein/leakage
  checks.

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
| Architecture churn before baseline | Unclear wins and lost comparability | Freeze the current 1024 BERT baseline; test ModernBERT/long-sequence variants only as measured PoCs |

## Architecture iteration backlog

These are not Phase 0 blockers. They are follow-on model-family experiments once the
current 1024-context BERT baseline has a reliable validation curve.

1. **ModernBERT-style encoder PoC.** Preserve the MLM objective and mRNA tokenizer
   first, but test a more modern encoder block: efficient fused attention where
   available, pre-norm stability, modern feed-forward variants, and longer-context
   position handling. Success means same validation set, same context length, higher
   throughput or lower validation loss at equal compute.
2. **Long-sequence Transformer PoC.** Test local/global or sparse attention variants
   for mRNA records where 1024 context remains limiting. This is a model-family
   change, not a drop-in speed flag; compare against the 1024 BERT baseline on
   protein-preserving and downstream ranking metrics before adopting it.
3. **Do not treat 512 context as the target.** A 512 run can locate the attention
   bottleneck and provide an ablation, but it may remove biologically useful long
   context. It should not replace the 1024 baseline unless validation and downstream
   task metrics prove the tradeoff is acceptable.

## Positioning

Do **not** position mRNABERT as "a small model that beats GPT/Claude at codon
optimization." Position the project as a controlled protein-to-mRNA design pipeline —
frozen structural evaluators + trainable sequence/mRNA modules + wet-lab feedback —
that produces auditable, constraint-satisfying candidates. That framing is both more
honest and more defensible.
