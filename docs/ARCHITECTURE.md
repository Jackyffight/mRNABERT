# mRNABERT Design-System Architecture

Canonical target architecture for the protein-to-mRNA design system that mRNABERT
anchors. This is the **target**; only the mRNA encoder (Phase 0) exists in code today
— see [`../ROADMAP.md`](../ROADMAP.md) for status and sequencing. The long-form
design essays that this distills are under [`reports/`](reports/).

Last updated: 2026-07-08.

## Design goals

A production design system, not a chat model, must simultaneously:

- express structural and functional constraints explicitly;
- generate candidates in batch, reproducibly, and screenably;
- verify mRNA-layer constraints (exact protein preservation is a hard check);
- screen input risk (dual-use safety);
- record per-candidate provenance for audit;
- close a wet-lab loop that keeps updating the scorers.

A general LLM can advise, but does not natively have this auditable, batchable,
constraint-bound, experiment-updated capability in the narrow mRNA/codon/protein
design space. That gap — not single-shot answer quality — is where the system wins.

## Two layers

**Frozen expert layer** — pinned, not retrained, provenance recorded per output:

- **ESMFold2** as structure oracle and refold evaluator (geometry extraction, fold
  confidence, refold scoring). Pinned by revision, weight checksum, seed, and
  inference parameters. It is used as an evaluator, **not** claimed as a trainable
  foundation model we reproduce from scratch. *(Provenance: ESMFold2 is Biohub's
  May-2026 release — ESMC + ESMFold2 + ESM Atlas — MIT-licensed; see Source Notes in
  the reports.)*
- **ProteinMPNN** for constrained protein-sequence expansion (frozen tool first, a
  fine-tuning target later).

**Controllable training layer** — the actual model asset, fine-tunable and
benchmarkable:

- mRNABERT-based mRNA/codon regulator and ranker;
- multi-objective reward model and rerankers;
- (later) a distilled design policy.

## The pipeline

```text
target spec
  -> safety & feasibility gate        (allow / review / deny, provenance)
  -> design-state builder             (typed state, not raw strings)
  -> ESMFold2 geometry                (structure + axes + interface table)
  -> ProteinMPNN expansion            (constrained protein candidates)
  -> ESMFold2 refold filter           (reject fold / geometry drift)
  -> constrained mRNA generation      (mRNABERT-conditioned; hard translation check)
  -> multi-objective reward + Pareto reranker
  -> wet-lab queue (active learning)  -> results feed back into the scorers
```

### Design state

Everything downstream operates on a typed design state, e.g.:

```json
{
  "target_id": "stable id",
  "protein_sequence": "...",
  "host_context": "human_HEK293 | CHO | E_coli | custom",
  "objective": {"expression": 0.35, "stability": 0.25, "translation_efficiency": 0.20,
                "low_immunogenicity": 0.10, "manufacturability": 0.10},
  "hard_constraints": {"preserve_protein": true, "forbidden_motifs": [],
                        "gc_window_range": [0.35, 0.70], "allowed_mutation_regions": [],
                        "fixed_residues": []},
  "soft_constraints": {"preferred_codon_table": "host-specific", "avoid_repeats": true,
                        "avoid_extreme_local_structure": true}
}
```

### mRNABERT's role

Narrow and honest: represent and score mRNA/CDS candidates cheaply, condition codon
choice on host/context, rank synonymous candidates, and predict
expression/stability/translation proxies — under the hard constraint that the output
CDS translates to the chosen protein. Current stage is a BERT MLM encoder; the path
forward adds supervised ranking heads, pairwise preference training, host-specific
adapters, a protein-conditioned codon generator, and reward-model integration.

## Making the experts part of reasoning (to be built, in this order)

1. **Tool-augmented reasoner** — a controller that calls ESMFold2 / ProteinMPNN /
   mRNABERT and updates the design state, trained from successful and failed design
   trajectories and wet-lab outcomes. Fastest path to "reasoning" without pretending
   the experts are differentiable.
2. **Cross-modal design-state model** — a trainable model fusing protein-sequence,
   structure-graph, ProteinMPNN-proposal, mRNA/codon, and host/context tokens with
   multi-task heads (fold-pass, expression, stability, TE, immunogenicity,
   manufacturability, pairwise preference, uncertainty). It learns to predict the
   experts' useful outputs and wet-lab outcomes, gradually reducing expensive calls.
3. **Distilled end-to-end policy** — distills refold pass/fail, proposal quality, and
   reward into fast surrogates; a learned policy proposes candidates directly and
   calls the experts only for uncertain cases. This is the moat:
   `fast learned policy + selective expert calls + experimental reward loop`.

## Training objectives

- **mRNA foundation:** MLM, span corruption, synonymous-codon denoising, UTR/CDS
  boundary prediction, amino-acid-conditioned codon recovery, host-conditioned codon
  distribution.
- **Paired:** on `(protein, mRNA, host/context, measured outcome)` — protein-
  preserving mRNA ranking, pairwise preference among synonymous candidates,
  expression/stability regression, top-k enrichment, failure-mode classification.
- **Tool-trajectory (reasoner):** next-action prediction, branch-value, expected
  improvement, tool-call budget optimization, uncertainty calibration.
- **Reward:** multi-objective and component-wise visible for Pareto analysis, never a
  single hidden scalar.

## Data schemas (auditable tables)

The four tables are first-class artifacts, not logs: **geometry/axis** (fold model
revision, weight checksum, axis method/vector, anchor/fixed/mutable residues, QC),
**candidate** (protein/mRNA, translated-protein hash for the preservation check,
source policy, MPNN logprob, fold score, axis deviation, reward components, safety
status), **experiment** (host/construct context, measured expression/stability/TE/
toxicity, batch/replicate, failure mode, measurement QC), and **reasoning-trace**
(state hashes before/after, action + parameters, tool revision, observation
artifact, cost, decision reason). Full field lists are in
[`reports/integrated-protein-mrna-reasoning-system.md`](reports/integrated-protein-mrna-reasoning-system.md).

## Evaluation

- **Offline:** protein-preservation rate, structural pass rate, axis/interface
  preservation, synonymous diversity, forbidden-motif and GC compliance, reward
  calibration, top-k enrichment on historical labels, pairwise ranking accuracy.
- **Wet-lab:** hit rate per 100 candidates, top-k enrichment over rule baseline,
  measured expression/stability uplift, assay variance, failure-mode reduction, cost
  per validated candidate.
- **LLM comparison** (safe design spaces only): candidate quality, constraint-
  violation rate, exact protein preservation, score after our own filters, wet-lab
  hit rate where allowed. The specialized system should win on lower violation rate,
  higher batch hit rate, better provenance, and better improvement after feedback.

## Safety and governance

The safety-and-feasibility gate is a product requirement, not a model feature:
restricted-protein-family screening; pathogenicity / toxin / virulence / immune-
evasion / resistance screens; a safe-benchmark whitelist; deny/review flows; and
provenance logging of user, target, constraints, model versions, and outputs. The
system optimizes only allowed design spaces. The opportunity is a narrower,
auditable, governable system — not bypassing general-model refusals.

## Key decisions

1. ESMFold2 is a frozen expert first, not our foundation model.
2. ProteinMPNN is both an expert tool and a future fine-tuning target.
3. mRNABERT is encoder + reward/generator backbone, not only MLM.
4. Store design traces — they are future training data.
5. Optimize through Pareto ranking, not one hidden scalar.
6. Exact protein preservation is a hard check.
7. Safety gates precede production optimization.
8. The current 1024-context BERT MLM is the baseline. ModernBERT-style encoders and
   long-sequence Transformers are future measured PoCs, not replacements until they
   beat the baseline on the same validation and downstream metrics.

## Future encoder families

The immediate training path keeps the current 1024-token BERT encoder because it is
now measurable and comparable. Two model-family iterations are explicitly on the
backlog:

- **ModernBERT-style encoder:** retain the MLM/tokenizer setup while testing a more
  efficient modern encoder block and attention kernel. The goal is equal or better
  validation loss at higher throughput, without changing the biological context
  length target.
- **Long-sequence Transformer:** test sparse/local/global attention variants only if
  1024 context proves limiting for downstream mRNA regulation tasks. This is a
  scientific tradeoff, not only a speed optimization, and must be benchmarked
  against exact protein preservation and wet-lab-linked ranking metrics.

A 512-token run is allowed as an ablation to diagnose attention cost, but it is not
the default product training setting.

## Open questions

Protein-side encoder ours vs ESMC embeddings; which wet-lab labels arrive first;
single- vs multi-host; CDS-only vs UTR+CDS+context; whether to fine-tune ProteinMPNN
on internal classes; per-candidate ESMFold2 refold budget (this is compute gate #1 in
the roadmap); minimum candidate-set size for reliable wet-lab enrichment.
