# Three-Protein Mock Project Policy / 三蛋白 Mock 项目策略

Status: project-owner declarations recorded on 2026-07-14

## Purpose / 目的

This project validates the executable workflow, data contracts, audit trail, model
adapters, and reports. It does not claim that the current inputs are suitable for a
real vaccine program or experimental release.

本项目用于验证可执行工作流、数据合同、审计链、模型适配器和报告。当前输入不用于
主张真实疫苗设计有效，也不得作为实验放行依据。

Machine-readable project settings:

- `project_mode = mock_workflow_validation`;
- `scientific_release_allowed = false`;
- intended vaccinated species: cattle (`Bos taurus`);
- recombinant-protein expression host assumption: CHO cells;
- mRNA manufacturing method: in vitro transcription (IVT).

IVT is a manufacturing method, not a delivery platform. Target cell type, delivery
platform, exact UTRs, cap, poly(A), formulation, vector, secretion compartment, and
purification process remain separate unresolved fields.

IVT 是制备方式，不是递送平台。目标细胞、递送平台、精确 UTR、帽结构、poly(A)、
制剂、载体、分泌区室和纯化工艺继续作为互相独立的未决字段。

## Declared Input Semantics / 已声明输入语义

1. Source provenance is waived for this Mock run. Public-literature inspiration is
   declared, but exact accessions, isolates, and custody are not asserted. Local
   sequence hashes define identity.
2. Full-length A33, B5, and L1 AA/CDS pairs are immutable **source controls**.
   Literature-inspired extracellular truncations are separate provisional Mock
   reference constructs. They are not renamed as full-length controls.
3. Manual construct annotations remain deferred. Affected constructs stay
   quarantined for experimental release; unrelated deterministic computation may
   continue.
4. The replacement B5 optimized CDS is stored outside Git under
   `input/reference-controls/b5_full_length_company_optimized_mock.fasta`. Its
   SHA-256 is
   `b914cca16948af42730717920951a3df563ac9ec3b9a5f141a8fdebc6af438c7`.
   Deterministic code verifies 678 nt, a terminal stop, and exact translation to the
   225-aa B5 source sequence. This establishes sequence consistency only.

## Decision Authority / 决策权限

The deterministic core owns parsing, hashes, translation, transformations,
constraints, state, and replay. Pinned specialist models or small models produce
structured evidence through adapters. The LLM may explain records or propose an
open question, but it must not invent population genetics, expression assumptions,
delivery choices, efficacy, safety, or release decisions.

确定性核心负责解析、哈希、翻译、变换、约束、状态和重放；固定版本的小模型或专用
模型通过适配器产生结构化证据。LLM 可以解释记录或提出待确认问题，但不得虚构群体
遗传、表达条件、递送选择、有效性、安全性或放行结论。

Every input and output must identify one of these provenance classes:

- deterministic computation;
- pinned model prediction;
- external supplied evidence;
- project-owner Mock declaration;
- LLM proposal;
- human approval or waiver.

Only deterministic rules and explicit human authority may change a hard gate.

