# Stage 2 Candidate Specification

Status: implemented as a provisional, audited multi-fidelity candidate search

## Purpose / 目的

Stage 2 converts released source records, supplied controls, and explicit generator
outputs into one versioned candidate batch. It separates sequence-derived facts from
names, claims, generator provenance, and human intent. Deterministic generation can
run on CPU; scientific generator adapters remain separately attributed.

第二阶段把源序列、手工构建体和明确的生成器输出转换为版本化候选批次。系统以实际
序列作为身份来源，文件名、人工声明和模型提案均保留独立来源。本轮组合生成可在 CPU
运行，不会把尚未执行的科学模型伪装成已有结果。

## Input Contract / 输入合同

The project configuration points to an external runtime file:

```json
{
  "inputs": {
    "candidate_specification": "input/candidate_specification.json"
  }
}
```

The specification declares:

- source controls inherited from the verified Stage 1 run;
- one AA source and optional CDS source per candidate, either FASTA-backed or inline;
- claimed source ranges for truncations;
- claimed component order for fusions;
- annotation approval state;
- generation-grammar state and model length limits.

Real sequences and the specification remain under the external runtime root. They
are snapshotted into each continuation run and are not committed to Git.

## Deterministic Processing / 确定性处理

The implementation:

1. verifies the complete Stage 1 parent run;
2. preserves its source snapshots and node artifacts under the parent artifact-index seal;
3. parses and hashes every explicitly declared Stage 2 input;
4. classifies AA/CDS translation as exact, terminal additions, or mismatch;
5. maps candidate AA segments back to exact A33, B5, and L1 source intervals;
6. derives the actual component order of supplied fusions;
7. compares sequence-derived annotations with supplied claims;
8. deduplicates model execution while preserving candidate aliases and evidence;
9. materializes approved component/order/linker grammars under a content identity;
10. consumes checksum-bound Stage 3-5 evidence to propose alternative source boundaries;
11. explores declared architectures with independent per-junction linkers;
12. preserves the full bounded eligible pool separately from the materialized panel;
13. emits a checksum-bound, diversity-preserving Stage 3 selection;
14. emits pinned external-model jobs and validates returned substitutions before
    creating child candidates;
15. exports a provisional structure-model FASTA without silently releasing it.

No candidate sequence is generated implicitly. `generate-stage2-proposals` requires
an approved or Mock-approved grammar, a verified seed run, explicit component slots,
linker sequences, order policy, length limit, and candidate budget. It snapshots
these inputs and writes a self-verifying proposal directory before the expanded
specification can enter Stage 2.

## Current Three-Protein Result / 当前结果

The seed Stage 2 run contains nine records:

- 3 full-length source controls: A33, B5, and L1;
- 4 supplied truncations;
- 2 supplied fusion constructs: ALAB and ALAL.

The first grammar-bounded baseline expansion considers 184 technical combinations across
pair, triple, and four-component templates and four linker hypotheses. One direct
four-component sequence is identical to supplied `manual-alab`, so it is skipped.
The expanded Stage 2 run therefore contains 192 records: 9 seeds and 183 generated
proposals. All pass digital sequence/lineage checks; only the three immutable source
controls are formally structure-ready. The 189 manual/generated records remain
quarantined for review and are suitable only for exploratory screening.

The evidence-guided expansion keeps all 192 baseline records and adds a second,
explicitly bounded search layer:

- 36 topology-safe source-boundary candidates selected around manual, signal, TM,
  and structure-confidence anchors;
- 270,240 configurations considered after parent-order and linker-pattern budgets;
- 24,464 unique fusion sequences admitted to the bounded eligible pool after
  duplicate and length checks;
- 2,048 new fusions materialized into the canonical candidate specification;
- 2,276 canonical Stage 2 records total;
- 384 Stage 3 selections: 9 source/manual controls, 48 stratified generated-baseline
  controls, 36 boundary candidates, and 291 new fusion candidates.

The eligible pool, materialized panel, and expensive-folding selection are separate
artifacts. Therefore a compute budget cannot silently erase a searched hypothesis.
The counts are outputs of `stage2-search-policy.json`, not claims that the space is
exhaustive or biologically optimal.

The supplied `b5-trunc` remains visible in the 192-record baseline, but it overlaps
the versioned TM interval at residue 189 and is therefore excluded as a parent of
new search-generated fusions. New B5 boundary candidates end before that interval.
This illustrates the distinction between retaining a historical control and using
it to expand the next candidate family.

## Why Nine, Not One Hundred? / 为什么是 9 个而不是 100 个？

Nine is an input-derived inventory count, not an optimized scientific target. The
current batch contains exactly the three immutable source controls, four explicitly
supplied truncations, and two explicitly supplied fusion constructs. No model chose
the count, and no combinatorial search established that these nine cover the best
design space.

当前的 9 个来自输入清单，而不是科学优化结果：3 个全长来源对照、4 个明确提供的截短体、
2 个明确提供的融合体。没有模型选择“9”这个数量，也没有组合搜索证明它们覆盖了最佳设计空间。

The original nine were an input-derived inventory count. Expansion is now enabled
only through versioned rules for:

- allowed source segments and truncation boundaries;
- allowed component order, repetition, and orientation;
- linker, signal-peptide, tag, and cleavage-site libraries;
- maximum length, topology, and expression constraints;
- duplicate handling, diversity targets, and control requirements;
- the cheap filters and selection rule used to reduce a larger pool to a declared
  expensive-compute panel.

Without those rules, 100 candidates would be arbitrary permutations that consume
compute without improving scientific coverage. The current grammar enumerates 183
new records because that is its exact bounded space, not because 183 is inherently
optimal. Expensive model execution should still use a declared funnel and budget.

The expanded pool exercises full-length controls, truncations, pairwise fusions,
three-component fusions, four-component fusions, orientation, and linker variation.
It still does not establish biological quality or exhaustive design-space coverage.

Sequence-derived findings include:

- A33.1 maps exactly to `A33:77-193`;
- A33.2 maps to `A33:101-193`, not the filename's `101-93` claim;
- the supplied B5 truncation maps to `B5:18-189`, not `18-238`;
- the supplied L1 truncation maps to `L1:3-181`, while its CDS adds an N-terminal
  methionine and the C-terminal `DYKDDDDK` FLAG tag;
- ALAB sequence order is `A33.2 -> L1 truncation -> A33.1 -> B5 truncation`, not
  the order declared in its filename;
- ALAL sequence order is `A33.2 -> L1 truncation -> A33.1 -> L1 truncation`, not
  the order declared in its filename;
- both fusion CDS records add an N-terminal methionine to the supplied AA sequence;
- no linker residues are present between the sequence-derived components in either
  supplied fusion AA sequence.

These are deterministic sequence findings. They do not establish whether the
observed constructs are biologically desirable or whether the labels were intended.

## Commands / 命令

Validate without writing a run:

```bash
./vaxflow validate-stage2 projects/three-protein/project.json \
  --from-run /absolute/path/to/verified-stage1-run
```

Generate and verify the expanded proposal pool:

```bash
./vaxflow generate-stage2-proposals projects/three-protein/project.json \
  --from-run /absolute/path/to/verified-stage2-seed-run \
  --grammar projects/three-protein/stage2-proposal-grammar.json

./vaxflow verify-stage2-proposals \
  /absolute/path/to/input/stage2/proposals/<generation-identity>
```

Generate and verify the evidence-guided multi-family pool:

```bash
./vaxflow search-stage2 projects/three-protein/project.json \
  --from-run /absolute/path/to/verified-192-candidate-stage2-run \
  --evidence-run /absolute/path/to/verified-stage4-5-run \
  --policy projects/three-protein/stage2-search-policy.json

./vaxflow verify-stage2-search \
  /absolute/path/to/input/stage2/searches/<search-identity>
```

After an external model executes a `ready_for_external_execution` job, import its
result through the residue-mask contract:

```bash
./vaxflow import-stage2-model-proposals projects/three-protein/project.json \
  --search-dir /absolute/path/to/input/stage2/searches/<search-identity> \
  --job-id esm3-constrained-junction-<identity-prefix> \
  --results /absolute/path/to/model-results.json
```

Create an immutable continuation run:

```bash
./vaxflow run-stage2 projects/three-protein/project.json \
  --from-run /absolute/path/to/verified-stage1-run \
  --specification /absolute/path/to/candidate_specification.generated.json
```

Verify the real Stage 2 run:

```bash
./vaxflow verify-run \
  /absolute/path/to/expanded-stage2-run
```

## Artifacts / 产物

The `nodes/candidate_specification/` directory contains:

- `candidate_batch.json`: canonical candidate sequences, IDs, component maps, and findings;
- `candidates.csv` and `candidate_components.csv`: review tables;
- `structure_candidates.fasta`: exact provisional ESMFold2 AA input;
- `model_inputs.json`: model ownership and readiness;
- `summary.json` and bilingual `report.html`;
- input, process, output, human-action, and handoff audit records;
- a snapshot of the specification plus every file-backed AA/CDS input; inline
  sequences are sealed directly by the specification hash.

The proposal-generation directory separately contains frozen grammar and seed
snapshots, `proposal_batch.json`, an inline generated specification, proposal
CSV/FASTA, a bilingual report, and an artifact index.

The multi-family search directory additionally contains:

- `atomic_components.*`: evidence-guided source-boundary panel;
- `candidate_pool.*`: all 24,464 unique sequences admitted by the bounded scoring policy;
- `materialized_fusion_panel.*`: the 2,048 fusions entering the canonical Stage 2 batch;
- `stage3_selection.json` and FASTA: the exact 384-candidate expensive-compute panel;
- `external_model_jobs.json`: pinned ESM3 and paired ProteinMPNN requests with
  mutable/protected residue masks;
- `inputs/evidence_bundle.json`: frozen Stage 3-5 evidence and hashes;
- `search_summary.json`, bilingual `report.html`, and a self-verifying artifact index.

## Model Order / 模型顺序

1. The deterministic grammar baseline and evidence-guided boundary/linker/architecture
   search are the generators executed locally in this expansion.
2. **ESMFold2** evaluates the checksum-bound 384-candidate panel in Stage 3.
3. **NetMHCpan/NetMHCIIpan** evaluate declared immune-presentation evidence in Stage 4.
4. **TMBed/metapredict** evaluate topology and disorder in Stage 5.
5. **ESM3** has a pinned constrained-junction proposal job for 64 selected parents.
   The request is ready, but ESM3 has not executed until a validated result is imported.
6. **ProteinMPNN** remains deferred until a verified backbone exists. Official and
   custom checkpoints are represented as paired, structure-backed experimental arms.
7. **Evo2** and **mRNABERT** belong to constrained nucleotide exploration and
   scoring in Stage 6B, after an exact protein candidate is selected.
8. Unconstrained backbone or antigen rewriting remains outside the current
   source-antigen preservation contract.

This separation allows many tools to contribute without letting one model generate,
score, and approve its own proposal.
