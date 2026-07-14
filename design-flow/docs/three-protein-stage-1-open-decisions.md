# Three-Protein Stage-1 Open Decisions

Status: project-owner adjudication recorded on 2026-07-14

This document preserves the reasoning behind three Stage-1 decisions. Canonical
statuses are in `projects/three-protein/project.json`; the current project is a Mock
workflow-validation run and cannot be scientifically released.

本文保留三个 Stage-1 决策的推理过程。正式状态以
`projects/three-protein/project.json` 为准；当前项目属于 Mock 工作流验证，不能
作为科学或实验放行。

## Scope And Blocking Semantics / 范围与阻塞语义

The later-branch actions now have these dispositions:

- `resolve-b5-optimized-cds`: resolved by a replacement Mock CDS whose exact B5
  translation and hash are checked by code;
- `select-protein-expression-host`: resolved as a CHO-cell Mock assumption;
- mRNA manufacturing is declared as IVT, independently of the still-unresolved
  delivery platform.

后续分支中，B5 误标优化 CDS 已由通过确定性翻译审计的新 Mock 文件替换；蛋白表达
宿主采用 CHO 细胞作为 Mock 假设；mRNA 制备方式声明为 IVT，但递送平台仍未确定。

The word "blocking" is split into three meanings:

- **compute-blocking**: no exploratory calculation can proceed;
- **stage-release-blocking**: calculations may run, but the next formal stage may
  not be accepted or published;
- **candidate-scoped blocking**: only affected candidates must remain quarantined.

这里的“阻塞”分为三类：是否连探索性计算都不能做、是否禁止正式放行下一阶段、
以及是否只隔离受影响的候选。三者不能混为一谈。

## Decision Summary / 决策摘要

| Action | Current conclusion | What the system can infer | Minimum human decision |
|---|---|---|---|
| `confirm-source-provenance` | Waived for Mock workflow validation | Hash-defined local identity only; no accession, isolate, or custody claim | Reopen only before real scientific use |
| `confirm-reference-controls` | Resolved | Full-length AA/CDS pairs are immutable source controls; literature-inspired truncations are separate provisional Mock references | No additional input for exploratory computation |
| `confirm-manual-construct-annotations` | Deferred to `experiment_release` | Alignments may propose components, but cannot recover design intent | Approve or correct only if these manual constructs are selected for release |

## 1. Source Provenance / 来源溯源

**Question:** provide the accession, source version, isolate or strain, and owner
for each A33, B5, and L1 source sequence.

**Current evidence:** the active FASTA pairs establish sequence identity and
AA/CDS consistency, but they do not contain enough trusted metadata to establish
where those sequences came from. A sequence hash proves which bytes entered this
run; it does not prove the biological isolate, database record, or custody chain.

**What can proceed now:** deterministic intake, sequence QC, structure prediction,
and other exploratory calculations may use provisional IDs tied to exact hashes.
All such outputs must retain a visible `provisional_provenance` condition and may
not be represented as strain-specific evidence.

**What can be predicted:** an automated database search can produce ranked likely
accession and isolate matches, including alignment coverage, identity, differences,
database revision, and retrieval date. This is evidence for review, not an
automatic provenance decision: identical sequences may occur in multiple records,
and a close match does not identify the supplied file's actual source.

**Current disposition:** the project owner approved an "unknown provenance,
hash-defined local reference" waiver for this Mock run. Real scientific use must
reopen provenance review.

## 2. Immutable Reference Controls / 不可变参照对照

**Question:** confirm whether the supplied full-length A33, B5, and L1 proteins and
their original CDS files are the immutable reference controls.

**Current evidence:** the archive audit found exact translation consistency for all
three full-length original AA/CDS pairs. They are therefore technically suitable
as the initial M0 controls. Suitability here means identity consistency, not proof
of antigen quality, efficacy, or correct provenance.

**What can proceed now:** the system can generate a proposed reference manifest
containing the six file/record hashes, normalized sequences, translation results,
and immutable candidate IDs. Downstream exploratory candidates can be compared
against that proposal without mutating it.

**What can be predicted:** this item is highly automatable because the deterministic
evidence supports one clear default: use each supplied full-length protein with its
matching original CDS. The system cannot infer whether the project owner intended a
different isolate, truncation, optimized CDS, or manual construct to be the business
reference.

**Current disposition:** the project owner approved full-length AA/CDS pairs as
immutable source controls and classified the literature-inspired truncations as
separate provisional Mock reference constructs. Any sequence change still creates a
new control version and candidate ID.

## 3. Manual Construct Annotations / 手工构建体注释

**Question:** confirm the truncation boundaries, initiator methionines, FLAG tag,
linker identities, and domain order of the supplied truncations and ALAB/ALAL manual
fusions.

**Current evidence:** sequence comparison already shows undeclared or partially
declared transformations, including missing standalone initiator methionines, an
inconsistent filename range, a FLAG-tagged construct, and fusions whose CDS adds an
initiator methionine. Labels alone are therefore not reliable component manifests.

**What can proceed now:** the three full-length source controls and newly generated
system candidates can continue independently. Supplied manual constructs may be
retained as quarantined comparison inputs, but they must not be synthesized,
treated as ground-truth controls, or used to claim a like-for-like comparison until
their component maps are confirmed.

**What can be predicted:** pairwise/local alignment against A33, B5, and L1 can
propose residue ranges and domain order. Exact unmatched segments can identify the
literal linker, initiator methionine, FLAG tag, insertions, and deletions. The
system can attach confidence and ambiguity to each inferred component. It cannot
recover undocumented design intent, prove that a boundary is biologically correct,
or decide whether an observed difference is intentional rather than a labeling
error.

**Current disposition:** this decision is deferred to `experiment_release`.
Unresolved manual constructs remain quarantined while unrelated deterministic
computation may proceed. They cannot be selected for synthesis or used as confirmed
like-for-like controls until their component manifest is approved.

## Required Follow-Up Artifact / 后续应生成的产物

The next implementation should produce a reviewable `stage1_decision_packet.json`
and bilingual HTML view containing:

1. ranked provenance candidates with alignment evidence;
2. the proposed immutable control manifest and hashes;
3. inferred component maps for every supplied manual construct;
4. per-field confidence, ambiguity, and evidence references;
5. explicit `approve`, `correct`, `waive`, and `keep_quarantined` decisions.

下一步不应继续让用户从原始文件中手工猜答案，而应由系统先生成上述预测与证据包，
再让人工做最小确认。批准结果必须回写为版本化结构数据，而不是只留在对话或报告文字中。
