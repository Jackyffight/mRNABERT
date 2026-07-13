# Three-Protein Stage-1 Open Decisions

Status: discussion record, pending human adjudication

This document records the three unresolved decisions that are relevant before the
three-protein project formally releases a `candidate_specification` batch. It does
not resolve those actions and does not replace the canonical statuses in
`projects/three-protein/project.json`.

本文记录三个蛋白项目在正式放行 `candidate_specification` 候选批次前仍需处理的
三个决策。本文不代表这些事项已经解决；正式状态仍以
`projects/three-protein/project.json` 为准。

## Scope And Blocking Semantics / 范围与阻塞语义

Two other open actions are deliberately deferred because they are required only by
later branches:

- `resolve-b5-optimized-cds`: the mislabeled supplied sequence remains quarantined
  and must be resolved before `mrna_product_design` uses a B5 optimized CDS;
- `select-protein-expression-host`: this is required before
  `developability_assessment` and the recombinant-protein product branch.

另外两项在本轮明确延后：B5 误标的优化 CDS 继续隔离，到 mRNA 产品设计前处理；
重组蛋白表达宿主到可开发性评估和重组蛋白产品分支前确定。二者不属于本文所说的
三个当前问题。

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
| `confirm-source-provenance` | Not compute-blocking; stage-release-blocking under the current v1 contract | Generate exact sequence hashes and search for likely accessions or strains, but sequence similarity cannot prove provenance or ownership | Confirm accession, source revision, isolate/strain, and data owner for A33, B5, and L1 |
| `confirm-reference-controls` | Not compute-blocking; stage-release-blocking and likely low-effort to resolve | Propose the supplied full-length AA plus matching original CDS as immutable controls because all three pairs pass exact translation audit | Approve that proposal or nominate replacement controls, then freeze their hashes |
| `confirm-manual-construct-annotations` | Not globally compute-blocking; manual candidates are release-blocked until confirmed | Align constructs to source proteins and infer ranges, order, initiator methionine, FLAG tag, and literal linker sequence with confidence/evidence | Approve or correct the generated component manifest and declare intended construct semantics |

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

**Release gate:** under the current v1 project contract this action blocks formal
release into `candidate_specification`. A human must confirm the selected record or
explicitly approve an "unknown provenance, hash-defined local reference" waiver.

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

**Release gate:** a human must approve the proposed manifest or provide replacements.
After approval, any sequence change creates a new control version and candidate ID;
it must never silently overwrite the approved reference.

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

**Release gate:** a human must approve or correct the inferred component manifest.
The current v1 configuration models this as a whole-stage gate before
`candidate_specification`. The intended finer policy is candidate-scoped: unresolved
manual constructs remain quarantined while unrelated, fully specified candidates
may proceed. That narrower behavior is a proposed implementation change, not yet a
property of the current executable gate.

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
