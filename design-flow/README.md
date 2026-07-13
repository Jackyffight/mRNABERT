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
- immutable JSON, CSV, and Markdown run artifacts with input hashes.

It does **not** yet predict antigenicity, safety, expression, structure, or vaccine
efficacy. Those stages remain explicitly marked `not_evaluated` in every manifest.

## First Three Proteins

From this directory, create the real input files from the tracked templates:

```bash
cp projects/three-protein/input/proteins_aa.fasta.example projects/three-protein/input/proteins_aa.fasta
cp projects/three-protein/input/proteins_cds.fasta.example projects/three-protein/input/proteins_cds.fasta
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

The command prints the exact run path. Each run contains:

- `manifest.json`: provenance, hashes, stage states, and counts;
- `proteins.json`: complete normalized sequences, metrics, and issues;
- `proteins.csv`: one flat row per original protein;
- `qc_issues.csv`: machine-readable errors and warnings;
- `report.md`: concise human-readable report.

The pointer `projects/three-protein/runs/latest.json` identifies the latest run.
Input FASTA files and run outputs are intentionally ignored by Git; only reusable
code, schemas, and templates are committed.

## New Project

To create another project without copying files by hand:

```bash
./vaxflow init projects/my-project --project-id my-project --expected-count 3
```

The generated FASTA files contain deliberately invalid placeholders, so a project
cannot accidentally pass validation before real sequences are supplied.

## Development

The package has no third-party runtime dependency for this milestone:

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design-build-test-learn path.
The initial supplied archive review is recorded in
[docs/supplied-data-audit.md](docs/supplied-data-audit.md); raw sequences are not
stored in Git.
