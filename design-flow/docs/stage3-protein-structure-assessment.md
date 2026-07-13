# Stage 3 Protein Structure Assessment

Status: implemented for deterministic exploratory ESMFold2-Fast assessment

## System Boundary

Stage 3 is a code-generated workflow. The only human action before execution is
moving one checksum-bound job archive to the GPU server and returning one
checksum-bound result archive. Manual archive extraction, metric transcription,
and LLM-authored structure conclusions are not authoritative workflow steps.

The stage has three code paths:

1. `vaxflow prepare-stage3` freezes exact Stage 2 candidate sequences, lineage,
   model revisions, and inference parameters into a two-file job archive.
2. `ProteinMPNN/design_flow_stage3/run_stage3_esmfold2.sh` validates the job,
   runs the already deployed pinned ESMFold2-Fast runtime, and returns a bounded,
   resumable result archive.
3. `vaxflow import-stage3` safely imports that archive, independently verifies
   every identity and artifact, recomputes metrics, and writes an immutable
   continuation run and bilingual HTML report.

## Versioned Rules

Ruleset `structure-exploratory-rules-v1` computes:

- exact PDB-to-candidate residue correspondence;
- normalized residue pLDDT, pTM, and low-confidence segments;
- C-alpha centroid, radius of gyration, end-to-end distance, maximum distance,
  principal-axis variances/vectors/extents, shape anisotropy, and nonlocal clashes;
- per-component confidence and geometry using the immutable Stage 2 component map;
- component-boundary confidence and junction C-alpha distance;
- alignment-free C-alpha distance-matrix RMSD between each source-derived
  component and the matching region of its predicted source control.

The fixed thresholds emit review flags only. They do not classify experimental
folding, immunogenicity, safety, or efficacy, and they do not rank or reject a
candidate.

## LLM Role

No LLM is required to reproduce Stage 3 artifacts or conclusions. An LLM may
later act as a reviewer by proposing explanations or follow-up hypotheses, but
that review must be labeled, versioned, and stored separately from deterministic
pipeline evidence.
