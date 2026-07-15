# Stage 4 CPU Toolchain

Status: open CPU tools and licensed DTU predictors installed and executable locally.

This document records why each external tool exists, what evidence it owns, and why
the existing ESMFold2, Evo 2, and mRNABERT models do not replace it. Tool output is
always imported through checksum-bound adapters. No external tool may directly
change candidate release status or an integrated score.

## Installation Location

The current-machine installation is outside both the repository and project runtime:

```text
/data00/home/wangzhi.wit/models/design-flow-tools/stage4
```

The licensed packages remain in the exact directories supplied by the project owner:

```text
/data00/home/wangzhi.wit/models/netMHCpan-4.2
/data00/home/wangzhi.wit/models/netMHCIIpan-4.3
```

They are not copied into `design-flow-tools`. The installer validates their archives,
versions, binaries, and checksums, then writes small Bash launchers that reference
those directories in place. The vendor top-level launchers are not used because they
require `tcsh` and contain vendor installation paths that do not exist on this host.

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
| NCBI BLAST+ | 2.17.0+ | Map A33/B5/L1 source controls to downloaded proteins and search candidates against the cattle proteome | Mature CPU search, transparent tabular output, sufficient for three sources and the active candidate set | Embedding similarity is not an auditable replacement for explicit local sequence alignments and coverage |
| MAFFT | 7.525, official source package | Produce one gapped amino-acid alignment for each source protein family | Stable CPU MSA, no root dependency, appropriate for a small viral sequence panel | ESMFold2 predicts one structure and does not estimate isolate-level sequence conservation |
| IPD-MHC BoLA data | snapshot to be pinned when downloaded | Define valid cattle class I and class II allele sequences and names | Maintained source for BoLA nomenclature and polymorphism | None of the deployed models defines the target cattle population or allele panel |
| NetMHCpan | 4.2e, ready in place | Predict peptide binding to BoLA class I | DTU pan-allelic predictor explicitly covering cattle BoLA, with deterministic CPU batch output | Evo 2 scores general sequence context; it is not trained or calibrated as a BoLA-I binding predictor |
| NetMHCIIpan | 4.3k, ready in place | Predict peptide binding to BoLA-DRB3 class II | DTU predictor explicitly supporting BoLA-DRB3, with deterministic CPU batch output | ESMFold2 structure confidence and mRNABERT embeddings do not predict BoLA-II presentation |

BLAST+ is preferred over DIAMOND for the first implementation because the current
workload is very small. DIAMOND becomes useful only when the sequence panel grows
enough for BLAST runtime to become material. BepiPred and DiscoTope remain optional
future evidence sources; they are not required to validate the Stage 4 control flow.

## Hardware Boundary

All selected core tools run on CPU. They do not require CUDA or reserve an A100.
The alignment and search workload for the active Stage 3 candidate set runs on the
same CPU host as `design-flow`. ESMFold2 remains the GPU structure stage, while Evo 2
and mRNABERT remain separate representation and mRNA-model components.

## Result Flow

After installation, the implemented MHC adapter produces these runtime artifacts:

```text
input/stage4/netmhc/<input-and-tool-identity>/
  candidates.fasta
  bola-panel.json
  mhc_binding.json
  manifest.json
  raw/class-i-*.xls
  raw/class-i-*.log
  raw/class-ii-*.xls
  raw/class-ii-*.log
```

The intended computation is:

1. NCBI Datasets downloads a checksum-bound LSDV sequence and metadata snapshot.
2. BLASTP maps each immutable source control to one homolog per accepted genome.
3. MAFFT aligns each source control with its homolog panel.
4. NetMHCpan and NetMHCIIpan scan candidate peptides against the declared BoLA panel.
5. BLASTP compares candidates with the pinned Bos taurus reference proteome.
6. Adapters convert raw outputs to `vaxflow.residue-evidence.v1` and Stage 4 reruns.

The adapter runs one allele per raw table, validates peptide coordinates against the
immutable candidate sequence, retains both supported and unsupported observations,
and uses the predictors' default EL-rank thresholds: class I 0.5/2.0 percent and
class II 1.0/5.0 percent for strong/weak binders. The generated manifest pins the
full candidate batch, active Stage 3 candidate set, executable hashes,
predictor-model hashes, parameters, and every raw/output artifact hash.

The first complete active-set run evaluated 384 candidates and emitted 881,853 raw
peptide-allele observations. These remain in the adapter artifact; the workflow node
stores only checksum-bound candidate-level aggregates, avoiding a second copy of the
large observation array while preserving semantic recomputation.

The legacy predictor binaries receive only short paths relative to the adapter work
directory, while their scratch directory is `/tmp`. This is required because
NetMHCpan 4.2e aborts in its native argument handling when long runtime paths are
passed to `-f` or `-xlsfile`. Final and failed artifacts remain under the configured
project runtime; only predictor scratch files use `/tmp`.

Run the current technical smoke path with:

```bash
/data00/home/wangzhi.wit/models/mRNABERT/design-flow/scripts/run_stage4_mhc_smoke.sh \
  /absolute/path/to/verified-stage3-run
```

This deliberately uses one available allele from each predictor,
`BoLA-1:00901` and `BoLA-DRB3_00101`, to prove executable integration. It does not
approve a target cattle population, claim breed coverage, or make the exploratory
MHC signal a release gate. Stage 4 therefore remains `needs_data` until a justified
population panel, pathogen alignments, the other adapters, and policy approvals are
supplied.

If a predictor exits unsuccessfully, the CLI prints the final 40 log lines and moves
the complete partial job to `input/stage4/netmhc/<identity>.failed`, including a
machine-readable `failure.json`. Failed diagnostics are never deleted before the
operator can inspect them.

## Licensed Package Custody

The licensed archives and extracted package trees stay outside Git and outside the
project runtime. Repository code records only paths and expected checksums:

| Artifact | SHA256 |
|---|---|
| `netMHCpan-4.2estatic.Linux.tar.gz` | `9270ddedfc55bce87f86d129c70a21f5e01db38e6a097eba96dca7c9581ec705` |
| NetMHCpan static binary | `3e7d50f924ed3b9540a6742b2e6bf928d0741b6ba0cc4d5f82cb931c45c6e03d` |
| `netMHCIIpan-4.3kstatic.Linux.tar.gz` | `e9b01db1a956e560d282bd608358f50158021129a83cfe1112a2d939e011382e` |
| NetMHCIIpan static binary | `6f40aa115abbef939f7aedef451578b3813ecb8b08d04cff93d4bb7c863a9c7f` |

The project owner remains responsible for the DTU license terms. Neither archives,
binaries, predictor data, nor generated redistribution bundles may be committed.

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
