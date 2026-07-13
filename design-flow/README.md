# Vaccine Design Flow

`design-flow` is the traceable workflow around candidate vaccine constructs. It is
kept separate from the mRNABERT training code so protein design, structure models,
mRNA design, ranking, and wet-lab feedback can evolve without coupling their state
to a single model implementation.

The current milestone implements sequence intake only:

- strict amino-acid and CDS FASTA parsing;
- one-to-one matching by FASTA ID;
- standard genetic-code translation and AA/CDS consistency checks;
- frame, stop-codon, alphabet, and start-codon QC;
- descriptive AA/CDS metrics;
- exact input snapshots, indexed immutable artifacts, and cross-file verification;
- JSON/CSV audit records and self-contained bilingual HTML reports.

It does **not** yet predict antigenicity, safety, expression, structure, or vaccine
efficacy. Those stages remain explicitly marked `not_evaluated` in every manifest.

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
