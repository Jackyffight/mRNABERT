# Protein-to-mRNA Design Pipeline Report

Date: 2026-07-06

> **Status: design exploration, superseded.** This is the earlier of two design
> essays. It is kept for provenance; the later
> [`integrated-protein-mrna-reasoning-system.md`](integrated-protein-mrna-reasoning-system.md)
> extends it. The canonical target architecture is [`../ARCHITECTURE.md`](../ARCHITECTURE.md)
> and the authoritative plan is [`../../ROADMAP.md`](../../ROADMAP.md).

## Executive Summary

单独把 mRNABERT 做成“密码子优化模型”，很容易被通用大模型和现有规则工具挤压。更有竞争力的定位是把它放进一个分层设计系统：

```text
structure intent -> protein sequence expansion -> structure refolding filter -> mRNA/codon regulation
```

这条路线的核心资产不是某一个模型，而是：

- 可审计的结构设计意图表示
- 可控的 ProteinMPNN-derived sequence designer
- mRNABERT-based mRNA/codon regulator
- 安全筛查、约束满足、实验反馈和 benchmark

ESMFold2 可以作为 frozen evaluator 和几何工具使用，但不应被包装成我们可从零训练复现的核心资产。真正可迭代、可训练、可沉淀数据飞轮的是 ProteinMPNN 层和 mRNA 调控层。

## Strategic Question

问题不是“小模型能不能打过 GPT/Mythos”。如果任务只是“给我一个看起来合理的 CDS”，通用大模型会越来越强，差距很可能被磨平。

真正的问题是：我们能不能做出一个通用大模型不容易直接替代的生产级系统。

生产级系统需要同时满足：

- 结构和功能约束可显式表达
- 生成候选可批量、可复现、可筛选
- mRNA 层约束可验证
- 输入风险可筛查
- 每个候选的 provenance 可审计
- 有湿实验闭环数据持续更新打分器

如果只做 stage-1 mRNA MLM 预训练，价值有限。如果做成“结构设计 + 蛋白设计 + mRNA 设计 + 实验闭环”的联合优化平台，机会明显更大。

## Proposed Four-Step Pipeline

### 1. ESMFold2 Defines Geometric Intent

Role: frozen structure oracle / geometry extractor.

Input:

- protein sequence
- optional complex context
- optional target interface or anchor residues

Output:

- predicted structure
- confidence metrics
- geometric axes or regions of interest

Recommended geometry methods:

- PCA axis over selected residues
- inertia axis over full folded structure
- interface normal for complex/interface tasks
- custom axis from user-selected anchor residues

Important boundary:

ESMFold2 should be pinned by code commit, model revision, weight checksum, seed, and inference parameters. It is used as an evaluator and geometry tool, not as a trainable asset we claim to reproduce from scratch.

### 2. ProteinMPNN Expands Sequence Candidates

Role: trainable/controllable protein sequence designer.

ProteinMPNN is the best layer to customize because the sequence-design objective can absorb explicit constraints:

- fixed catalytic or functional residues
- allowed mutation regions
- interface-preserving residues
- geometric-axis constraints
- diversity control
- developability penalties
- organism- or assay-specific priors

The key product risk is losing design intent between step 1 and step 2. ProteinMPNN should not only consume a backbone; it should consume an explicit design specification derived from the geometric table.

### 3. ESMFold2 Refolds and Filters

Role: frozen evaluator.

For each generated protein candidate, refold and score:

- fold confidence
- structural similarity to intended scaffold
- interface/axis preservation
- local geometry around anchor residues
- gross fold failure

This step turns ProteinMPNN from a generator into a controlled candidate search system. It also gives a reproducible artifact trail: input candidate -> structure prediction -> pass/fail reason.

### 4. mRNABERT Handles mRNA/Codon Regulation

Role: trainable mRNA/codon regulator and ranking model.

mRNABERT should not be framed as “a general model that knows biology”. Its useful role is narrower:

- represent mRNA/CDS sequences cheaply
- score codon choices under host/cell-line constraints
- rank synonymous CDS candidates
- predict expression/stability/translation-efficiency proxies
- penalize manufacturability or motif risks

This stage should receive protein candidates that have already passed structure filters, then operate under hard translation constraints: output CDS must translate to the chosen protein sequence.

## Architecture: Two Layers

### Frozen Expert Layer

Components:

- ESMFold2 for initial geometry extraction
- ESMFold2 for refolding validation

Properties:

- pinned code and weights
- deterministic inference configuration where possible
- provenance recorded for every output
- not claimed as a fully reproducible trainable foundation model

### Controllable Training Layer

Components:

- ProteinMPNN-derived protein sequence designer
- mRNABERT-based mRNA regulator
- downstream scorers and rerankers

Properties:

- can be fine-tuned or retrained
- can absorb proprietary wet-lab feedback
- can be benchmarked against public and internal baselines
- becomes the main model asset

## Required Intermediate Table

The geometry step must produce a durable, auditable table. Suggested schema:

| Field | Description |
| --- | --- |
| `protein_id` | Stable target identifier |
| `input_sequence_hash` | Hash of input amino-acid sequence |
| `input_sequence_path` | Path or object reference for sequence |
| `fold_model_name` | ESMFold2 or selected frozen fold model |
| `fold_model_revision` | Model revision or checkpoint ID |
| `fold_code_commit` | Code commit used for inference |
| `fold_weight_checksum` | Checksum of model weights |
| `fold_seed` | Inference seed, if applicable |
| `fold_parameters` | JSON blob of inference parameters |
| `structure_path` | PDB/mmCIF artifact path |
| `axis_method` | `PCA`, `inertia`, `interface_normal`, or `custom` |
| `axis_vector` | 3D vector in structure coordinates |
| `axis_origin` | Origin point for axis |
| `anchor_residues` | Residues defining intent or functional constraints |
| `fixed_residues` | Residues ProteinMPNN must not mutate |
| `mutable_regions` | Allowed mutation spans |
| `interface_residues` | Interface-critical residues, if applicable |
| `confidence_plddt` | Structure confidence summary |
| `confidence_ptm` | Global confidence, if available |
| `confidence_iptm` | Interface confidence, if available |
| `qc_status` | `pass`, `warn`, or `fail` |
| `qc_reason` | Human-readable QC explanation |

This table is the interface between structure intent and sequence generation. If it is missing, the design objective will be implicit and hard to debug.

## Benchmark Plan

The first benchmark should be small, safe, and decision-oriented. It should compare pipeline variants, not only final model loss.

### Candidate Baselines

- Baseline A: ProteinMPNN only
- Baseline B: ProteinMPNN + ESMFold2 refold filter
- Baseline C: ProteinMPNN + ESMFold2 filter + rule-based codon optimizer
- Baseline D: ProteinMPNN + ESMFold2 filter + mRNABERT ranking
- Baseline E: general LLM suggestion, evaluated only as an external baseline when allowed and safe

### Metrics

Protein/structure metrics:

- sequence diversity
- preservation of fixed residues
- fold confidence
- structural similarity to intended scaffold
- interface/axis preservation
- percentage passing structural QC

mRNA/CDS metrics:

- exact protein preservation after translation
- GC range compliance
- codon usage score
- forbidden motif removal
- repetitive sequence penalty
- local structure proxy
- manufacturability proxy

Experimental metrics, when available:

- expression level
- mRNA half-life
- protein yield
- translation efficiency
- cell toxicity
- batch variance
- failure rate

The benchmark should report not only best candidate quality, but hit rate per 1,000 generated candidates. High-throughput hit rate is where a specialized system can beat general-purpose text generation.

## Wet-Lab Data Moat

The main long-term moat is not model size. It is experiment-linked data.

Most valuable data:

- paired design -> measurement records
- host/cell-line/task-specific expression results
- failed designs and failure annotations
- repeated measurements for variance estimation
- assay conditions and batch metadata
- negative examples from designs that looked good computationally but failed experimentally

The flywheel should be:

```text
generate candidates -> screen computationally -> test experimentally -> assign failure modes -> update scorers -> regenerate
```

Without this loop, the pipeline is only a better candidate generator. With this loop, it becomes an improving design system.

## Safety and Governance

Codon and mRNA optimization are dual-use. The system should not be designed as an unrestricted sequence optimizer.

Recommended safety layer:

- input screening against restricted or high-risk protein families
- deny or review toxic, pathogenic, virulence, immune-evasion, and resistance-associated targets
- whitelist safe benchmark proteins and assay contexts
- keep all optimization runs auditable
- separate educational/demo modes from production optimization
- record user, target, constraints, model versions, and output provenance

The product opportunity is not bypassing general LLM refusals. The opportunity is building a narrower, auditable system that can safely operate in allowed design spaces.

## Risk Register

| Risk | Impact | Mitigation |
| --- | --- | --- |
| ESMFold2 treated as a trainable asset | Overclaiming and reproducibility risk | Document it as frozen evaluator only |
| Geometry intent lost before ProteinMPNN | Generated candidates drift from objective | Persist geometric intent table and feed constraints explicitly |
| mRNABERT only trained as MLM | Weak downstream value | Add supervised/ranking objectives from expression/stability data |
| No wet-lab feedback | No durable advantage over public tools | Prioritize closed-loop assay data collection |
| Data cache and preprocessing cost | Slow iteration and storage pressure | Keep dataset cache in run workspace; support streaming for exploratory runs |
| Biosecurity concerns | Product/legal risk | Add screening, whitelist, provenance, and review flows |
| General LLM capability improves | Demo-level optimization commoditized | Focus on constraints, batch throughput, auditability, and experimental feedback |

## Near-Term Roadmap

### Phase 0: Stabilize mRNABERT Training

- Keep stage-1 pretraining smoke green.
- Add dataset cache isolation.
- Add a clean single-GPU smoke path and a separate distributed path.
- Record run manifests for every training job.

### Phase 1: Build Benchmark Harness

- Define safe protein target set.
- Implement translation-preserving CDS checks.
- Add rule-based codon optimizer baseline.
- Add ProteinMPNN-only baseline.
- Add ESMFold2 refold scoring artifacts.

### Phase 2: Add mRNA Ranking

- Generate synonymous CDS candidates for fixed protein outputs.
- Score candidates with mRNABERT-derived embeddings.
- Compare against rule-based codon optimization.
- Start with proxy labels if wet-lab labels are unavailable.

### Phase 3: Close the Experimental Loop

- Record wet-lab measurements in a structured table.
- Train expression/stability/ranking heads.
- Use failure annotations to improve candidate generation.
- Report hit rate and cost per validated candidate.

## Recommended Positioning

Do not position this project as:

> A small model that beats GPT/Mythos at codon optimization.

Position it as:

> A controlled protein-to-mRNA design pipeline that combines frozen structural evaluators, trainable sequence-design modules, mRNA regulation models, and wet-lab feedback to produce auditable, constraint-satisfying candidates.

That positioning is technically stronger and more defensible.

## Source Notes

- Biohub ESM repository: https://github.com/Biohub/esm
- Biohub ESMFold2 overview: https://biohub.ai/esm/protein/about
  (ESMFold2 is Biohub's May 2026 release — ESMC + ESMFold2 + ESM Atlas — MIT-licensed.)
- ProteinMPNN repository: https://github.com/dauparas/ProteinMPNN
- ProteinMPNN paper: https://www.science.org/doi/10.1126/science.add2187
