# Stage 2 Candidate Specification

Status: implemented as a provisional, audited candidate batch

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
10. exports a provisional structure-model FASTA without silently releasing it.

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

The first grammar-bounded expansion considers 184 technical combinations across
pair, triple, and four-component templates and four linker hypotheses. One direct
four-component sequence is identical to supplied `manual-alab`, so it is skipped.
The expanded Stage 2 run therefore contains 192 records: 9 seeds and 183 generated
proposals. All pass digital sequence/lineage checks; only the three immutable source
controls are formally structure-ready. The 189 manual/generated records remain
quarantined for review and are suitable only for exploratory screening.

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
- the cheap filters and selection rule used to reduce a larger pool to 100.

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

## Model Order / 模型顺序

1. The deterministic enumerator is the only generator executed in this expansion.
2. **ESMFold2** evaluates the frozen protein pool in Stage 3.
3. **NetMHCpan/NetMHCIIpan** evaluate declared immune-presentation evidence in Stage 4.
4. **TMBed/metapredict** evaluate topology and disorder in Stage 5.
5. **ProteinMPNN** remains deferred until a backbone and authorized residue mask
   exist; it may not rewrite antigen residues in round-000.
6. **Evo2** and **mRNABERT** belong to constrained nucleotide exploration and
   scoring in Stage 6B, after an exact protein candidate is selected.
7. Free protein/backbone generation such as ESM3 or RFdiffusion is not applicable
   to the current source-antigen preservation contract.

This separation allows many tools to contribute without letting one model generate,
score, and approve its own proposal.
