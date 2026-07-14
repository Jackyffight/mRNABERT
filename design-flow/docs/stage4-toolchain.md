# Stage 4 CPU Toolchain

Status: open CPU tools installed locally; licensed DTU predictors pending manual download.

This document records why each external tool exists, what evidence it owns, and why
the existing ESMFold2, Evo 2, and mRNABERT models do not replace it. Tool output is
always imported through checksum-bound adapters. No external tool may directly
change candidate release status or an integrated score.

## Installation Location

The current-machine installation is outside both the repository and project runtime:

```text
/data00/home/wangzhi.wit/models/design-flow-tools/stage4
```

Install and verify the open CPU tools with:

```bash
/data00/home/wangzhi.wit/models/mRNABERT/design-flow/scripts/install_stage4_cpu_tools.sh
/data00/home/wangzhi.wit/models/mRNABERT/design-flow/scripts/verify_stage4_cpu_tools.sh
```

The scripts use absolute paths, pin download SHA256 values, install without root,
and do not modify shell profiles. Pipeline code must invoke tools by their absolute
paths under `design-flow-tools/stage4/bin`.

## Selection Decisions

Tools are selected only when they satisfy all four constraints:

1. They close a named Stage 4 evidence gap rather than duplicate a deployed model.
2. They expose a deterministic batch or command-line interface suitable for adapters.
3. Their inputs, versions, parameters, and raw outputs can be checksum-bound.
4. They run on CPU for this workload, preserving the limited GPU allocation for model jobs.

| Tool | Pinned version | Stage 4 responsibility | Why selected | Why an existing model is insufficient |
|---|---|---|---|---|
| NCBI Datasets and Dataformat | Datasets 18.33.1, checksum-pinned Dataformat companion | Download versioned LSDV genomes, proteins, CDS, and metadata | Authoritative accession-oriented acquisition with machine-readable metadata | Evo 2 can represent a supplied sequence but cannot define or retrieve the authoritative isolate panel |
| NCBI BLAST+ | 2.17.0+ | Map A33/B5/L1 source controls to downloaded proteins and search candidates against the cattle proteome | Mature CPU search, transparent tabular output, sufficient for three sources and nine candidates | Embedding similarity is not an auditable replacement for explicit local sequence alignments and coverage |
| MAFFT | 7.525, official source package | Produce one gapped amino-acid alignment for each source protein family | Stable CPU MSA, no root dependency, appropriate for a small viral sequence panel | ESMFold2 predicts one structure and does not estimate isolate-level sequence conservation |
| IPD-MHC BoLA data | snapshot to be pinned when downloaded | Define valid cattle class I and class II allele sequences and names | Maintained source for BoLA nomenclature and polymorphism | None of the deployed models defines the target cattle population or allele panel |
| NetMHCpan | 4.2e, pending manual download | Predict peptide binding to BoLA class I | Current DTU pan-allelic predictor explicitly covering cattle BoLA | Evo 2 scores general sequence context; it is not trained or calibrated as a BoLA-I binding predictor |
| NetMHCIIpan | 4.3k, pending manual download | Predict peptide binding to BoLA-DRB3 class II | Current DTU predictor explicitly supporting BoLA-DRB3 | ESMFold2 structure confidence and mRNABERT embeddings do not predict BoLA-II presentation |

BLAST+ is preferred over DIAMOND for the first implementation because the current
workload is very small. DIAMOND becomes useful only when the sequence panel grows
enough for BLAST runtime to become material. BepiPred and DiscoTope remain optional
future evidence sources; they are not required to validate the Stage 4 control flow.

## Hardware Boundary

All selected core tools run on CPU. They do not require CUDA or reserve an A100.
The alignment and search workload for the current nine candidates should run on the
same CPU host as `design-flow`. ESMFold2 remains the GPU structure stage, while Evo 2
and mRNABERT remain separate representation and mRNA-model components.

## Result Flow

After installation, adapter jobs will produce these runtime artifacts:

```text
input/stage4/alignments/A33.fasta
input/stage4/alignments/B5.fasta
input/stage4/alignments/L1.fasta
input/stage4/bola-panel.json
input/stage4/evidence/mhc_binding.json
input/stage4/evidence/host_similarity.json
```

The intended computation is:

1. NCBI Datasets downloads a checksum-bound LSDV sequence and metadata snapshot.
2. BLASTP maps each immutable source control to one homolog per accepted genome.
3. MAFFT aligns each source control with its homolog panel.
4. NetMHCpan and NetMHCIIpan scan candidate peptides against the approved BoLA panel.
5. BLASTP compares candidates with the pinned Bos taurus reference proteome.
6. Adapters convert raw outputs to `vaxflow.residue-evidence.v1` and Stage 4 reruns.

Installation alone does not change Stage 4 from `not_evaluated`. A category changes
to `evaluated` only after its raw result, tool version, input identities, and adapter
JSON are all present and checksum-bound.

## Pending Licensed Downloads

The DTU packages require the project owner to use the official request pages and
accept their terms. Download the Linux x86-64 packages without renaming them, then
record their source URL and SHA256 before installation:

- NetMHCpan 4.2e Linux
- NetMHCIIpan 4.3k Linux

The local installer for those packages will be finalized against the actual archive
layout after the files are available. It must not guess paths or bypass the request
process.

## Meaning of Missing-Evidence Messages

The current repeated message, "provide and confirm the versioned input or external
prediction result; keep it not evaluated while missing," is a generic governance
message. It means missing evidence is `unknown`, never a favorable zero or an
automatic failure. It is not an instruction for a particular tool. Future reports
should render the specific requirement description and tool command separately.

## Official Sources

- [NCBI Datasets virus download documentation](https://www.ncbi.nlm.nih.gov/datasets/docs/v2/how-tos/virus/virus-download/)
- [NCBI BLAST command-line documentation](https://blast.ncbi.nlm.nih.gov/doc/blast-help/downloadblastdata.html)
- [MAFFT official software page](https://mafft.cbrc.jp/alignment/software/)
- [NetMHCpan 4.2 service and request page](https://services.healthtech.dtu.dk/services/NetMHCpan-4.2/)
- [NetMHCIIpan 4.3 service and request page](https://services.healthtech.dtu.dk/services/NetMHCIIpan-4.3/)
- [IPD-MHC BoLA database](https://www.ebi.ac.uk/ipd/mhc/group/BoLA/)
- [UniProt Bos taurus reference proteome UP000009136](https://www.uniprot.org/proteomes/UP000009136)
