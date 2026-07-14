# Vaccine Design Flow

`design-flow` is the traceable workflow around candidate vaccine constructs. It is
kept separate from the mRNABERT training code so protein design, structure models,
mRNA design, ranking, and wet-lab feedback can evolve without coupling their state
to a single model implementation.

The current milestone implements the reproducible computational path from source
intake through provisional integrated ranking:

- strict amino-acid and CDS FASTA parsing;
- one-to-one matching by FASTA ID;
- standard genetic-code translation and AA/CDS consistency checks;
- frame, stop-codon, alphabet, and start-codon QC;
- descriptive AA/CDS metrics;
- exact input snapshots, indexed immutable artifacts, and cross-file verification;
- JSON/CSV audit records and self-contained bilingual HTML reports.
- a Stage 2 candidate specification contract for source controls, truncations, and fusions;
- deterministic AA/CDS terminal-addition, source-range, and component-order inference;
- immutable continuation runs with sealed Stage 1 provenance;
- provisional, deduplicated ESMFold2 FASTA export and explicit model handoff state;
- checksum-bound ESMFold2-Fast GPU jobs and resumable remote execution;
- deterministic Stage 3 result import, residue confidence, principal-axis geometry,
  component/boundary analysis, source-geometry comparisons, and bilingual reports.
- Stage 4 residue-mapped conservation, structure-surface proxy, and pinned immune
  evidence adapters with missing evidence preserved as `not_evaluated`;
- Stage 5 intrinsic developability descriptors and pinned external predictor adapters;
- parallel Stage 6A recombinant-protein and Stage 6B mRNA product specifications;
- exact antigen/expression/final-product separation and CDS translation auditing;
- deterministic synonymous CDS search, hard constraints, Pareto selection, and exact
  full-mRNA assembly once versioned codon and non-coding inputs are supplied;
- Stage 7 hard gates, transparent feature contributions, coverage penalties,
  control-aware diversity selection, and weight-sensitivity analysis;
- semantic verifiers that recompute Stage 4-7 results after integrity checks.

It does **not** claim antigenicity, safety, expression success, manufacturing success,
or vaccine efficacy. Stage 3-7 outputs are computational hypotheses and technical
prioritization. Missing external evidence remains `not_evaluated`; Stage 7 always
leaves the formal portfolio empty, so experiment release still requires a later
human-controlled node.

## First Three Proteins

Source code and runtime data are deliberately separated. The tracked project uses
this external runtime root:

```text
/data00/home/wangzhi.wit/models/design-flow-runtime/three-protein
```

From this directory, create the runtime input files from the tracked templates:

```bash
mkdir -p /data00/home/wangzhi.wit/models/design-flow-runtime/three-protein/input
cp projects/three-protein/input/proteins_aa.fasta.example /data00/home/wangzhi.wit/models/design-flow-runtime/three-protein/input/proteins_aa.fasta
cp projects/three-protein/input/proteins_cds.fasta.example /data00/home/wangzhi.wit/models/design-flow-runtime/three-protein/input/proteins_cds.fasta
```

Replace every placeholder with the real sequence. The two files must contain the
same three IDs (`A33`, `B5`, and `L1`). Use the original coding
sequence in the CDS file, in 5-prime to 3-prime orientation. DNA (`T`) and RNA (`U`)
are accepted; RNA is normalized to DNA for translation checks.

Validate without writing a run:

```bash
./vaxflow validate projects/three-protein/project.json
```

When validation is understandable, calculate and record the first run:

```bash
./vaxflow run projects/three-protein/project.json
```

The command prints the exact run path. The run root contains:

- `inputs/`: exact project and FASTA snapshots used by this run;
- `artifact_index.json`: SHA-256 and byte size for every run artifact;
- `manifest.json`: run identity, context, current node, and artifact pointers;
- `workflow.json`: complete future UI node graph and every node's audit contract;
- `nodes/program_and_source_intake/summary.json`: compact UI node summary;
- `nodes/program_and_source_intake/report.html`: bilingual current-node detail report;
- `nodes/program_and_source_intake/input_audit.json`: audited node inputs;
- `nodes/program_and_source_intake/process_record.json`: processing provenance;
- `nodes/program_and_source_intake/output_audit.json`: audited node outputs;
- `nodes/program_and_source_intake/human_actions.json`: human questions and decisions;
- `nodes/program_and_source_intake/handoff.json`: next-node payload;
- sequence JSON/CSV details under the same node directory.

As the workflow advances, each completed system node adds its own `summary.json`
and `report.html` plus the same audit envelope. The workflow snapshot is not repeated
inside every node report.

The pointer
`/data00/home/wangzhi.wit/models/design-flow-runtime/three-protein/runs/latest.json`
identifies the latest run. Real input FASTA files and all run outputs remain outside
the Git repository; only reusable code, schemas, and templates are committed.
The pointer is published only after hashes and cross-file semantics pass verification.

Verify any run independently:

```bash
./vaxflow verify-run /absolute/path/to/runs/<run-id>
```

Run candidate specification against a verified Stage 1 run:

```bash
./vaxflow validate-stage2 projects/three-protein/project.json --from-run /absolute/path/to/stage1-run
./vaxflow run-stage2 projects/three-protein/project.json --from-run /absolute/path/to/stage1-run
```

The Stage 2 node writes `candidate_batch.json`, review CSV files, a bilingual node
report, and `structure_candidates.fasta`. The latter is an explicitly provisional
ESMFold2 input until the human release gates are resolved.

Prepare the checksum-bound exploratory Stage 3 transfer archive:

```bash
./vaxflow prepare-stage3 projects/three-protein/project.json \
  --from-run /absolute/path/to/verified-stage2-run
```

Only the resulting `.tar.gz` archive must be transferred to the GPU server. The
24.4 GiB pinned ESMFold2-Fast/ESMC-6B runtime remains on that server.

Import the returned result archive through code, not through manual extraction or
interpretation:

```bash
./vaxflow import-stage3 projects/three-protein/project.json \
  --from-run /absolute/path/to/verified-stage2-run \
  --results /absolute/path/to/checksum-bound-results.tar.gz
```

The importer safely extracts the bounded archive, verifies all remote identities
and PDB checksums, requires exact PDB/candidate residue correspondence, recomputes
the versioned geometry rules, and writes an immutable bilingual Stage 3 run.

Initialize and run the combined Stage 4/5 evidence assessment:

```bash
./vaxflow init-stage4-5 projects/three-protein/project.json \
  --from-run /absolute/path/to/verified-stage3-run
./vaxflow run-stage4-5 projects/three-protein/project.json \
  --from-run /absolute/path/to/verified-stage3-run
```

The first command creates editable specifications under the external runtime root.
The second command succeeds when optional datasets or model results are absent, but
affected categories and node status remain explicit `not_evaluated`/`needs_data`.

Initialize and run both Stage 6 product branches:

```bash
./vaxflow init-stage6 projects/three-protein/project.json \
  --from-run /absolute/path/to/verified-stage4-5-run
./vaxflow run-stage6 projects/three-protein/project.json \
  --from-run /absolute/path/to/verified-stage4-5-run
```

Stage 6A writes exact protein constructs, coding sequences, and an ESMFold2 structure
recheck payload for constructs changed by expression additions. Stage 6B retains
source-CDS controls and, after a versioned 61-codon table is configured, creates
translation-safe Pareto designs. No sequence with a translation mismatch can enter
the output batch.

Initialize and run Stage 7 provisional ranking:

```bash
./vaxflow init-stage7 projects/three-protein/project.json \
  --from-run /absolute/path/to/verified-stage6-run
./vaxflow run-stage7 projects/three-protein/project.json \
  --from-run /absolute/path/to/verified-stage6-run
```

The ranking policy is a runtime input, not hidden code. Features with weight zero are
reported but cannot affect rank. Missing positive-weight evidence is penalized, hard
gates run before ranking, excluded candidates remain visible, and the output is a
provisional portfolio only.

## New Project

To create another project without copying files by hand:

```bash
./vaxflow init projects/my-project \
  --runtime-root /data00/home/wangzhi.wit/models/design-flow-runtime/my-project \
  --project-id my-project \
  --expected-count 3
```

The project specification is written under `projects/`, while input placeholders
and later runs are written under the external runtime root. The generated FASTA
files contain deliberately invalid placeholders, so a project cannot accidentally
pass validation before real sequences are supplied.

## Development

The package has no third-party runtime dependency for this milestone:

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design-build-test-learn path.
Stage 4-7 implementation details and external input contracts are recorded in
[docs/stage4-7-computational-pipeline.md](docs/stage4-7-computational-pipeline.md).
The frozen route is recorded in
[docs/workflow-v1.md](docs/workflow-v1.md) and
`docs/workflow-v1.json`; CI-style tests prevent those contracts from drifting
away from the executable DAG.
The boundary between deterministic rules, LLM review, and human authority is
defined in
[docs/audit-automation-and-llm-governance.md](docs/audit-automation-and-llm-governance.md).
The initial supplied archive review is recorded in
[docs/supplied-data-audit.md](docs/supplied-data-audit.md); raw sequences are not
stored in Git.
The three stage-1 decisions that remain open after deferring later-branch questions
are recorded in
[docs/three-protein-stage-1-open-decisions.md](docs/three-protein-stage-1-open-decisions.md),
including their blocking scope, machine-inference boundary, and minimum human input.
The current project-owner declarations, Mock-only release policy, CHO/IVT context,
and strict boundary on LLM decisions are recorded in
[docs/three-protein-mock-project-policy.md](docs/three-protein-mock-project-policy.md).
The implemented Stage 2 contract, current deterministic findings, artifacts, and
model launch order are documented in
[docs/stage2-candidate-specification.md](docs/stage2-candidate-specification.md).
