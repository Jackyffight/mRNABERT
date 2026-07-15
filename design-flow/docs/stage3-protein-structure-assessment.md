# Stage 3 Protein Structure Assessment

Status: implemented for deterministic exploratory ESMFold2-Fast assessment

## System Boundary

Stage 3 is a code-generated workflow. The only human action before execution is
moving one checksum-bound job archive to the GPU server and returning one
checksum-bound result archive. Manual archive extraction, metric transcription,
and LLM-authored structure conclusions are not authoritative workflow steps.

The stage has three code paths:

1. `vaxflow prepare-stage3` freezes exact Stage 2 candidate sequences, lineage,
   model revisions, inference parameters, and the optional multi-fidelity selection
   into a checksum-bound job archive. Search jobs contain `job-manifest.json`,
   `sequences.fasta`, and `selection.json`.
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

For the current multi-family search, Stage 3 does not fold all 2,276 materialized
records. It consumes the exact 384-record `stage3_selection.json`; the manifest
binds every candidate key, AA hash, length, selection tier, search identity, and
budget. The importer revalidates the same snapshot against the immutable Stage 2
candidate batch before accepting any PDB.

## Three-Protein Mock Result

Run `20260715T153913241225Z-stage3-7d0bd68b` imported all 384 selected
ESMFold2-Fast results with 384 successes, zero failures, and a clean 17-check
immutable-run verification. Ruleset `structure-exploratory-rules-v1` assigned:

- 9 higher-confidence structures;
- 18 mixed-confidence structures;
- 357 low-confidence structures.

These bands are deterministic combinations of global mean pLDDT and pTM. They are
model-confidence labels, not experimental stability, immunogenicity, efficacy, or
release decisions.

The nine original source/manual recipes produced one higher-confidence candidate
(`b5-trunc`), two mixed-confidence candidates, and six low-confidence candidates.
The other eight higher-confidence candidates were newly enumerated B5 boundary
variants from the `atomic_boundary_panel`; none of the evaluated fusion candidates
entered the higher-confidence band. This supports expanding boundary hypotheses,
but it does not establish that B5 is the best antigen or that fusion candidates are
biologically inferior. Global pTM and single-sequence folding confidence can
systematically penalize long, flexible, or multi-domain fusion constructs.

In the central-kitchen analogy, the original nine records were handwritten recipes.
ESMFold2 judged whether each trial dish had a clearly predictable shape. One original
B5 cut and eight machine-enumerated B5 cuts held a clearer predicted shape. This says
which dishes the plating station understands; it does not say which dish tastes best,
serves the intended diner, or can be manufactured reliably.

## Downstream Routing

Stage 4 immune evidence and Stage 5 sequence/developability assessment process all
384 candidates because they are comparatively inexpensive and provide evidence that
is complementary to structure confidence. Stage 3 low confidence alone never deletes
a candidate. Expensive structure reruns, model redesign, and eventual experimental
budgets use the three-lane policy in ADR 0004: priority, diversity rescue, and archive.

## LLM Role

No LLM is required to reproduce Stage 3 artifacts or conclusions. An LLM may
later act as a reviewer by proposing explanations or follow-up hypotheses, but
that review must be labeled, versioned, and stored separately from deterministic
pipeline evidence.
