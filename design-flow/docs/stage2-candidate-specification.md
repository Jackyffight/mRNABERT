# Stage 2 Candidate Specification

Status: implemented as a provisional, audited candidate batch

## Purpose / 目的

Stage 2 converts released source records and explicitly supplied manual constructs
into one versioned candidate batch. It separates sequence-derived facts from names,
claims, and human intent. No scientific model is needed to perform this stage.

第二阶段把源序列和明确提供的手工构建体转换为版本化候选批次。系统以实际序列为
身份来源，文件名和人工声明只作为待核对信息。本阶段不需要启动科学模型。

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
- one AA FASTA and optional CDS FASTA per manual candidate;
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
9. exports a provisional structure-model FASTA without silently releasing it.

No candidate sequence is generated implicitly. New fusion generation remains off
until an allowed grammar for boundaries, ordering, linkers, tags, and limits is
approved.

## Current Three-Protein Result / 当前结果

The first real Stage 2 run contains nine records:

- 3 full-length source controls: A33, B5, and L1;
- 4 supplied truncations;
- 2 supplied fusion constructs: ALAB and ALAL.

All nine have computationally valid AA sequences and are eligible for exploratory
structure inference. None is formally released because the source/control,
annotation, grammar, and batch-approval gates remain open.

## Why Nine, Not One Hundred? / 为什么是 9 个而不是 100 个？

Nine is an input-derived inventory count, not an optimized scientific target. The
current batch contains exactly the three immutable source controls, four explicitly
supplied truncations, and two explicitly supplied fusion constructs. No model chose
the count, and no combinatorial search established that these nine cover the best
design space.

当前的 9 个来自输入清单，而不是科学优化结果：3 个全长来源对照、4 个明确提供的截短体、
2 个明确提供的融合体。没有模型选择“9”这个数量，也没有组合搜索证明它们覆盖了最佳设计空间。

Automatic expansion is intentionally disabled because the project has not approved
a construct grammar. Generating 100 records responsibly first requires versioned
rules for:

- allowed source segments and truncation boundaries;
- allowed component order, repetition, and orientation;
- linker, signal-peptide, tag, and cleavage-site libraries;
- maximum length, topology, and expression constraints;
- duplicate handling, diversity targets, and control requirements;
- the cheap filters and selection rule used to reduce a larger pool to 100.

Without those rules, 100 candidates would be arbitrary permutations that consume
compute without improving scientific coverage. A future generation round should
enumerate a larger grammar-bounded pool, apply inexpensive hard checks, cluster for
sequence and architecture diversity, and then select a declared number for expensive
structure and downstream evaluation. One hundred is therefore a possible execution
budget, not an inherently better scientific number.

For the current Mock milestone, nine is sufficient to exercise all three important
software paths: full-length controls, truncations, and multi-component fusions. It is
not sufficient to claim that candidate generation or design-space exploration is
complete.

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
  --from-run /data00/home/wangzhi.wit/models/design-flow-runtime/three-protein/runs/20260713T100542093738Z-25cb00ab
```

Create an immutable continuation run:

```bash
./vaxflow run-stage2 projects/three-protein/project.json \
  --from-run /data00/home/wangzhi.wit/models/design-flow-runtime/three-protein/runs/20260713T100542093738Z-25cb00ab
```

Verify the real Stage 2 run:

```bash
./vaxflow verify-run \
  /data00/home/wangzhi.wit/models/design-flow-runtime/three-protein/runs/20260713T120430674353Z-stage2-b184a407
```

## Artifacts / 产物

The `nodes/candidate_specification/` directory contains:

- `candidate_batch.json`: canonical candidate sequences, IDs, component maps, and findings;
- `candidates.csv` and `candidate_components.csv`: review tables;
- `structure_candidates.fasta`: exact provisional ESMFold2 AA input;
- `model_inputs.json`: model ownership and readiness;
- `summary.json` and bilingual `report.html`;
- input, process, output, human-action, and handoff audit records;
- snapshots of the specification and every manual AA/CDS FASTA.

## Model Order / 模型顺序

1. **ESMFold2** is the next executable model adapter. It consumes
   `structure_candidates.fasta` in `protein_structure_assessment`. The current file
   is explicitly exploratory because the batch is not formally approved.
2. **Evo2** is not a folding backend. It should enter later through a pinned
   sequence-evidence adapter after the candidate batch is frozen, with a declared
   task and calibration rather than a generic score.
3. **mRNABERT** belongs to `mrna_product_design`. It should not score protein
   construct quality before the exact protein product and coding policy are fixed.

The immediate engineering task after candidate review is therefore the Stage 3
ESMFold2 adapter, structure artifact contract, and residue-level structure audit.
