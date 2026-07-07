# Integrated Protein-to-mRNA Reasoning System

Date: 2026-07-07

## Executive Summary

目标不是做一个“会说密码子优化”的小模型，而是做一个面向特定设计空间的闭环系统：

```text
protein intent -> structure reasoning -> protein expansion -> structure validation
              -> synonymous mRNA design -> multi-objective reward -> wet-lab feedback
```

这个系统里，ESMFold2 和 ProteinMPNN 不只是普通前后处理工具。更强的设计是把它们变成模型 reasoning 的可调用专家，并把专家调用轨迹、结构特征、失败样本和湿实验结果蒸馏进自有模型。最终资产应该是：

- 一个结构感知的 design state representation
- 一个可调用 ESMFold2/ProteinMPNN/mRNABERT 的 design reasoner
- 一个多目标 reward model
- 一个受硬约束的 protein-preserving mRNA generator
- 一个主动学习闭环

通用大模型可以给建议，但很难天然具备这套可审计、可批量、可约束、可实验反馈更新的窄域优化能力。

## System Thesis

### Why This Can Beat General LLMs in a Narrow Domain

通用大模型的优势是知识广、解释强、接口自然。但在 mRNA/codon/protein design 这个窄域里，胜负点不是聊天能力，而是：

- 是否严格保证蛋白序列不变
- 是否能批量生成和筛选上万候选
- 是否能利用结构几何约束
- 是否能把失败设计也作为负样本学习
- 是否有细分 host/cell/assay context 的湿实验 reward
- 是否能复现每个设计决策的 provenance

因此系统应避免和 GPT/Mythos 在“单次回答”上硬拼，而是在“可重复设计闭环”上建立优势。

## Top-Level Architecture

```text
                          +----------------------+
User / target spec  ----> |  Safety & feasibility |
                          +----------+-----------+
                                     |
                                     v
                          +----------------------+
                          | Design State Builder |
                          +----------+-----------+
                                     |
             +-----------------------+-----------------------+
             |                                               |
             v                                               v
 +------------------------+                       +------------------------+
 | ESMFold2 geometry node |                       | Protein intent encoder |
 | frozen expert          |                       | trainable              |
 +-----------+------------+                       +-----------+------------+
             |                                                |
             v                                                |
 +------------------------+                                   |
 | Axis / interface table |                                   |
 +-----------+------------+                                   |
             |                                                |
             v                                                v
 +---------------------------------------------------------------+
 |                 Design Reasoner / Policy Model                 |
 |  tool-augmented controller + structural/mRNA latent memory      |
 +----------------------+-------------------+--------------------+
                        |                   |
                        v                   v
          +--------------------------+   +-----------------------+
          | ProteinMPNN expansion    |   | constrained mRNA gen  |
          | trainable or fine-tuned  |   | mRNABERT conditioned  |
          +------------+-------------+   +-----------+-----------+
                       |                             |
                       v                             v
          +--------------------------+   +-----------------------+
          | ESMFold2 refold filter   |   | mRNA reward/scorers   |
          | frozen evaluator         |   | trainable             |
          +------------+-------------+   +-----------+-----------+
                       |                             |
                       +-------------+---------------+
                                     v
                          +----------------------+
                          | Pareto reranker      |
                          +----------+-----------+
                                     |
                                     v
                          +----------------------+
                          | Wet-lab queue / AL   |
                          +----------------------+
```

## Components

### 1. Safety and Feasibility Gate

This is not a model-quality feature; it is a product requirement.

Inputs:

- protein target
- host organism or cell line
- assay context
- intended function class
- optimization objective
- user/project metadata

Outputs:

- `allow`, `review`, or `deny`
- reason codes
- allowed optimization modes
- audit record

Recommended checks:

- restricted protein family screen
- pathogenicity, toxin, virulence, immune evasion, resistance screens
- exact provenance logging
- safe benchmark whitelist for internal R&D

The system should optimize only allowed design spaces.

### 2. Design State Builder

Everything downstream should operate on a typed design state, not raw strings.

```json
{
  "target_id": "stable id",
  "protein_sequence": "...",
  "host_context": "human_HEK293 | CHO | E_coli | custom",
  "objective": {
    "expression": 0.35,
    "stability": 0.25,
    "translation_efficiency": 0.20,
    "low_immunogenicity": 0.10,
    "manufacturability": 0.10
  },
  "hard_constraints": {
    "preserve_protein": true,
    "forbidden_motifs": [],
    "gc_window_range": [0.35, 0.70],
    "allowed_mutation_regions": [],
    "fixed_residues": []
  },
  "soft_constraints": {
    "preferred_codon_table": "host-specific",
    "avoid_repeats": true,
    "avoid_extreme_local_structure": true
  }
}
```

### 3. ESMFold2 Geometry Node

Role:

- frozen structure oracle
- geometry extractor
- refold evaluator
- representation source for the reasoner

It should not be positioned as our trainable foundation model unless we later have complete training data, code, compute, and reproducibility. Instead, use it as a pinned expert.

Outputs:

- predicted PDB/mmCIF
- pLDDT / pTM / ipTM-like confidence metrics if available
- residue-level structure embeddings if exposed
- chain/interface confidence
- geometric axes
- local frames for anchor residues
- contact/interface graph

Recommended axis methods:

- PCA axis over selected residues
- inertia axis over full structure
- interface normal over contact residues
- functional anchor axis from user-selected residues

The axis table is a first-class artifact:

| Field | Purpose |
| --- | --- |
| `axis_id` | stable geometry identifier |
| `protein_id` | target sequence id |
| `fold_model_revision` | pinned ESMFold2 revision |
| `fold_weight_checksum` | reproducibility |
| `structure_path` | PDB/mmCIF artifact |
| `axis_method` | PCA / inertia / interface_normal / anchor |
| `axis_origin` | 3D origin |
| `axis_vector` | normalized direction |
| `anchor_residues` | residues defining intent |
| `fixed_residues` | must not mutate |
| `mutable_regions` | allowed design regions |
| `interface_residues` | interface-critical residues |
| `confidence_summary` | fold confidence |
| `qc_status` | pass / warn / fail |

### 4. ProteinMPNN Expansion Node

Role:

- broaden protein sequence candidates while preserving backbone intent
- inject diversity before mRNA optimization
- provide candidate distribution for the reasoner

Inputs:

- backbone or folded structure
- fixed residues
- mutable residues
- chain masks
- interface masks
- design temperature
- bias vectors or allowed amino acids

Outputs:

- protein candidates
- per-position log probability
- design temperature
- mutation map
- diversity cluster id

Why ProteinMPNN matters:

- it is controllable
- it can be fine-tuned
- it turns structural intent into a large candidate pool
- it is narrower and more reliable than asking a text model to invent sequences

### 5. ESMFold2 Refold Validation Node

Role:

- evaluate generated protein candidates
- reject fold drift
- enforce geometric intent preservation

Scores:

- fold confidence
- RMSD/TM-score to intended scaffold
- interface residue contact preservation
- axis preservation angle
- anchor local-frame deviation
- clash/developability warnings

This is where the system becomes a search engine rather than a single generator.

### 6. mRNABERT mRNA Regulation Node

Role:

- represent and score mRNA/CDS candidates
- condition codon choice on host/context
- integrate expression/stability/translation labels when available

Current stage:

- BERT-style encoder
- MLM pretraining
- codon-aware tokenization

Needed next:

- supervised ranking heads
- pairwise preference training
- host-specific adapters
- protein-conditioned codon generator
- reward model integration

## Making ESMFold2 and ProteinMPNN Part of Model Reasoning

There are three levels of integration. We should build them in order.

### Level 1: Tool-Augmented Reasoner

The reasoner is a controller that calls tools and updates a structured design state.

```text
state_0 = target spec
action_1 = call ESMFold2.fold(target)
observation_1 = structure + confidence + axis table
action_2 = call ProteinMPNN.sample(backbone, fixed_residues, temp)
observation_2 = protein candidates + logprobs
action_3 = call ESMFold2.refold(candidates)
observation_3 = refold scores
action_4 = call mRNA generator / scorer
observation_4 = mRNA candidates + reward scores
action_5 = rank / request more candidates / send to wet lab
```

Training data for this reasoner:

- successful design trajectories
- failed trajectories
- tool outputs
- human decisions
- wet-lab outcomes

Training objective:

- imitate expert trajectories
- predict next best action
- predict whether extra tool calls are worth the cost
- rank candidate branches by expected reward

This is the fastest path to “model reasoning” without pretending that ESMFold2 is differentiable or retrainable.

### Level 2: Cross-Modal Design State Model

Build a trainable model that fuses:

- protein sequence tokens
- structure graph tokens
- axis/interface tokens
- ProteinMPNN logit/proposal features
- mRNA/codon tokens
- host/context tokens
- reward labels

Suggested representation:

```text
[CTX] host/objective/constraints
[PROT] amino acid sequence embeddings
[STRUCT] residue frames, contacts, confidence, axis tokens
[MPNN] proposal logits, mutation masks, design temperature
[MRNA] codon/base tokens from candidate CDS
[REWARD] supervised heads
```

Architecture:

- protein encoder
- structure graph encoder
- mRNA encoder
- cross-attention fusion block
- multi-task heads

Heads:

- fold-pass probability
- expression score
- stability score
- translation efficiency score
- immunogenicity/motif risk
- manufacturability score
- pairwise preference score
- candidate uncertainty

This model does not replace ESMFold2/ProteinMPNN initially. It learns to predict their useful outcomes and wet-lab outcomes, then gradually reduces expensive tool calls.

### Level 3: Distilled End-to-End Design Policy

After enough trajectories:

- distill ESMFold2 refold pass/fail into a fast structure-surrogate head
- distill ProteinMPNN proposal quality into a protein-edit policy
- distill mRNABERT/reward scores into a candidate generator
- train a policy to propose candidates directly, then selectively call tools for uncertain cases

This becomes the actual moat:

```text
fast learned design policy + selective expert calls + experimental reward loop
```

## Training Objectives

### Foundation Objectives

For mRNA:

- MLM on mRNA tokens
- span corruption
- synonymous codon denoising
- UTR/CDS boundary prediction
- codon recovery conditioned on amino acid
- host-conditioned codon distribution modeling

For protein:

- amino acid MLM
- structure-conditioned sequence recovery
- mutation effect contrastive learning
- interface residue prediction

### Paired Objectives

Critical paired training examples:

```text
protein sequence, mRNA sequence, host/context, measured outcome
```

Objectives:

- protein-preserving mRNA ranking
- pairwise preference loss among synonymous candidates
- expression/stability regression
- top-k enrichment
- failure mode classification

### Tool-Trajectory Objectives

For reasoning:

- next action prediction
- branch value prediction
- expected improvement prediction
- tool-call budget optimization
- uncertainty calibration

Example:

```text
Given structure confidence low near interface, should the policy:
1. resample ProteinMPNN with lower temperature
2. expand mutable residues
3. reject target
4. request human review
```

### Reward Model Objective

Reward should be multi-objective, not a scalar hidden inside code.

```text
R = w_expr * R_expression
  + w_stab * R_stability
  + w_te * R_translation_efficiency
  + w_struct * R_structure_pass
  + w_safe * R_safety
  + w_mfg * R_manufacturability
  - penalties
```

But keep each component separately visible for Pareto analysis.

## Inference Algorithm

### Batch Design Mode

```text
1. Validate target and constraints.
2. Fold target with ESMFold2.
3. Extract geometry axes, anchors, interfaces, mutable regions.
4. Generate N protein candidates with ProteinMPNN.
5. Refold candidates with ESMFold2.
6. Keep candidates passing structure/geometric QC.
7. For each protein candidate, generate K synonymous mRNA candidates.
8. Score mRNA candidates with reward model.
9. Rank by Pareto frontier, not only scalar score.
10. Select candidates for wet-lab queue using diversity + uncertainty.
```

### Reasoning Mode

The reasoner chooses actions:

```text
while budget remains:
    observe design_state
    choose action:
        fold | extract_axis | mpnn_expand | refold | generate_mrna | score | rerank | stop
    update design_state
return candidate set + full trace
```

This is where ESMFold2 and ProteinMPNN become reasoning steps rather than hardcoded pipeline stages.

## Data Schema

### Candidate Table

| Field | Description |
| --- | --- |
| `candidate_id` | stable id |
| `target_id` | parent target |
| `protein_sequence` | generated or original protein |
| `protein_mutations` | mutation list |
| `mrna_sequence` | candidate mRNA/CDS |
| `translated_protein_hash` | exact preservation check |
| `source_policy` | rule / MPNN / reasoner |
| `mpnn_logprob` | proposal quality |
| `fold_score` | ESMFold2 confidence |
| `axis_deviation` | geometric intent preservation |
| `mrna_reward` | total mRNA score |
| `reward_components` | JSON scores |
| `safety_status` | allow/review/deny |
| `selected_for_lab` | bool |

### Experimental Result Table

| Field | Description |
| --- | --- |
| `experiment_id` | assay id |
| `candidate_id` | candidate link |
| `host_context` | cell/host |
| `construct_context` | UTR/vector/delivery metadata |
| `expression` | measured expression |
| `stability` | measured half-life or proxy |
| `translation_efficiency` | measured or proxy |
| `toxicity` | toxicity/readout |
| `batch_id` | batch metadata |
| `replicate_id` | replicate |
| `failure_mode` | if failed |
| `measurement_quality` | QC |

### Reasoning Trace Table

| Field | Description |
| --- | --- |
| `trace_id` | full design run id |
| `step_index` | action index |
| `state_hash_before` | state before action |
| `action_type` | tool or model action |
| `action_parameters` | JSON |
| `tool_revision` | tool version |
| `observation_artifact` | output path |
| `state_hash_after` | state after action |
| `cost` | compute/time |
| `decision_reason` | human/model explanation |

## Evaluation

### Offline Evaluation

Metrics:

- protein preservation rate
- structural pass rate
- axis/interface preservation
- synonymous candidate diversity
- forbidden motif compliance
- GC/window-GC compliance
- reward calibration
- top-k enrichment on historical labels
- pairwise ranking accuracy

### Wet-Lab Evaluation

Metrics:

- hit rate per 100 candidates
- top-k enrichment over rule baseline
- measured expression uplift
- stability uplift
- assay variance
- failure mode reduction
- cost per validated candidate

### General LLM Comparison

Compare only in allowed, safe benchmark spaces.

LLM baseline should be evaluated as:

- candidate generation quality
- constraint violation rate
- exact protein preservation
- score after our same filters
- wet-lab hit rate if allowed

The specialized system should win by lower violation rate, higher batch hit rate, better provenance, and better improvement after feedback.

## Roadmap

### Phase 0: Stabilize mRNA Encoder

- finish stable mRNABERT streaming training
- add validation split and loss curve dashboards
- build tokenized dataset path for speed
- add run manifest and checkpoint lineage

### Phase 1: Build Tool Pipeline

- add ESMFold2 wrapper
- add geometry extraction table
- add ProteinMPNN wrapper
- add refold scoring
- add mRNA candidate generator with hard translation checks
- add rule-based codon optimizer baseline

### Phase 2: Build Reward and Reranker

- mRNABERT embedding extraction
- supervised heads if labels exist
- proxy reward heads if labels are limited
- pairwise ranking dataset
- Pareto reranker

### Phase 3: Build Reasoning Traces

- every design run writes a trace table
- train next-action/value model from traces
- add uncertainty-driven tool calls
- distill expensive tools into fast surrogate heads

### Phase 4: Active Learning

- diversity + uncertainty sample selection
- wet-lab queue construction
- assay result ingestion
- periodic reward model retraining
- report hit-rate improvements

### Phase 5: End-to-End Design Policy

- protein edit policy
- mRNA generator policy
- reward-guided decoding
- selective ESMFold2/ProteinMPNN calls
- benchmark against static pipeline and LLM baseline

## MVP Build Plan

### Week 1-2

- stabilize training scripts
- define schemas
- implement translation-preserving mRNA candidate generation
- build rule-based baseline

### Week 3-4

- wrap ESMFold2 inference
- implement PCA/inertia/interface axes
- write geometry table
- add ProteinMPNN candidate generation

### Week 5-6

- add ESMFold2 refold scoring
- build structure QC report
- connect mRNA candidate scoring
- output full design trace

### Week 7-8

- train first mRNA reward/ranking head
- evaluate against rule baseline
- produce wet-lab candidate queue

## Key Design Decisions

1. Treat ESMFold2 as a frozen expert first.
2. Treat ProteinMPNN as both an expert tool and a future fine-tuning target.
3. Treat mRNABERT as encoder plus reward/generator backbone, not only MLM.
4. Store design traces; they are future training data.
5. Optimize through Pareto ranking, not one hidden scalar.
6. Keep exact protein preservation as a hard check.
7. Build safety gates before production optimization.

## Open Technical Questions

- Do we train a protein-side encoder ourselves, or rely on ESMC/ESMFold2 embeddings?
- Which wet-lab labels arrive first: expression, stability, TE, or protein yield?
- Is host context fixed or multi-host?
- Are we optimizing CDS only, or UTR + CDS + polyA/context later?
- Should ProteinMPNN be fine-tuned on internal protein classes?
- How much compute budget can each candidate spend on ESMFold2 refolding?
- What is the minimum candidate set size for reliable wet-lab enrichment?

## Recommended Near-Term Choice

For the current project, do this:

```text
1. Finish mRNABERT encoder pretraining enough to get stable embeddings.
2. Implement ESMFold2 geometry/refold wrapper as frozen evaluator.
3. Implement ProteinMPNN expansion wrapper.
4. Implement translation-preserving mRNA candidate generator.
5. Build reward/reranker as the first trainable integration layer.
6. Log every design trace so the reasoner can be trained later.
```

Do not start by training a monolithic end-to-end model. Start with an auditable expert-system pipeline, then train the reasoner from its traces and wet-lab outcomes.

## Source Notes

- Biohub ESMFold2 / ESMC overview: https://biohub.ai/esm/protein/about
- Biohub ESM repository: https://github.com/Biohub/esm
- ProteinMPNN repository: https://github.com/dauparas/ProteinMPNN
- ProteinMPNN paper: https://www.science.org/doi/10.1126/science.add2187

