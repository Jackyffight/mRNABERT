# Stage 6 public codon-usage input

## Purpose

Stage 6B requires a versioned target-context codon table before it can generate
translation-preserving synonymous CDS designs. For the three-protein Mock project,
the supplied project answer identifies cattle as the translation target. The
recombinant-protein CHO branch remains a separate context and must not reuse this
table implicitly.

The cattle table is derived from the official NCBI RefSeq annotation for:

- species: `Bos taurus` (`taxon_id=9913`);
- assembly: `GCF_002263795.3_ARS-UCD2.0`;
- annotation release: `RS_2024_12`;
- source artifact: `GCF_002263795.3_ARS-UCD2.0_cds_from_genomic.fna.gz`;
- NCBI-published MD5: `f7a8f7cf1c230de8d136b4ca1a067a06`.

The source is public reference data. That removes private-data and project-secret
concerns, but public provenance does not make the result an experimentally proven
expression optimum.

## Reproducible derivation

Run:

```bash
/data00/home/wangzhi.wit/models/mRNABERT/design-flow/scripts/build_bos_taurus_codon_usage.sh
```

The script downloads the fixed RefSeq CDS artifact to the external runtime, verifies
the NCBI MD5, and invokes the dependency-free `vaxflow build-codon-usage` command.
It emits:

- a `vaxflow.codon-usage.v1` 61-sense-codon table accepted by Stage 6;
- a separate audit JSON containing source hashes, filter counts, selected gene count,
  selected codon count, and the canonical table identity.

The derivation keeps only standard nuclear-code CDS records that are complete,
unambiguous, divisible by three, free of internal stops, and linked to an NCBI
GeneID. Pseudogenes, partial CDS, non-standard genetic codes, explicit translation
exceptions, rearrangements, frameshifts, and unclassified translation discrepancies
are excluded. NCBI's benign `annotated by transcript or proteomic data` evidence note
is retained. To prevent genes with many annotated isoforms from dominating, one
longest valid CDS is selected per GeneID with a deterministic record-ID tie break.
Terminal stop codons are not counted.

## Interpretation boundary

This table is a transparent baseline prior for synonymous generation and the current
CAI proxy. It is not:

- a tissue-specific cattle expression model;
- an IVT manufacturability model;
- a replacement for GC, forbidden-motif, repeat, or RNA-structure constraints;
- equivalent to the unspecified company's proprietary Mock optimization;
- evidence that a generated construct will express well in vivo.

The exact 5' UTR, 3' UTR, cap chemistry, modified nucleoside, and delivery platform
remain separate product inputs. Missing non-coding elements do not block CDS-only
generation or CDS-level Evo 2 scoring; they block assembly and claims about the full
mRNA product.

## Three-protein Mock verification

The pinned build performed on 2026-07-16 produced the following deterministic audit:

- `64,900` RefSeq CDS FASTA records inspected;
- `63,853` standard, complete CDS records accepted before per-gene selection;
- `20,821` unique GeneIDs represented by one longest valid CDS each;
- `11,510,766` standard sense codons counted;
- `219` partial records, `816` special-translation records, `8` internal-stop
  records, and `4` frame-length records excluded;
- canonical codon-table SHA256:
  `28862a5d047895379dbf8d09c416f2d7aa5a0da27ec62a62985d1e3ed802b6df`.

A read-only preview against the current `384` routed amino-acid candidates generated
exactly four synonymous CDS designs per candidate (`1,536` generated designs). With
the six source CDS controls and one provided Mock company control, the preview held
`1,543` coding-only designs, all of which passed exact retranslation. The existing
default hard constraints rejected `33,811` trial sequences before Pareto selection;
this is an engineering preview, not approval of those default constraint values.

To materialize that exact exploratory Mock round and preserve the previous mRNA
specification in `input/stage6/history`, run:

```bash
/data00/home/wangzhi.wit/models/mRNABERT/design-flow/scripts/run_stage6_mrna_cds_mock.sh
```

The command only binds the cattle table and enables coding-sequence generation. It
does not approve candidate release, non-coding elements, delivery assumptions, or the
default GC/motif policy as a scientific policy.

The resulting Mock run contained `1,543` audited coding designs. The expensive-model
routing selected `52` candidates: `27` structure-confidence priority candidates, `6`
forced source/manual controls, and `19` additional representatives required to cover
all `53/53` configured diversity features. Their four generated CDS designs plus the
seven controls produced a `215`-record Evo 2 job. The number `52` is therefore an
emergent minimum representative set under the current cost policy, not a scientific
sample-size constant.

When this changes the Stage 6 candidate/design batch, refresh the checksum-bound
Stage 7 candidate set explicitly. The old ranking policy is archived and migrated;
the old candidate binding is never reused silently:

```bash
/data00/home/wangzhi.wit/models/mRNABERT/design-flow/vaxflow init-stage7 \
  /data00/home/wangzhi.wit/models/mRNABERT/design-flow/projects/three-protein/project.json \
  --from-run /absolute/path/to/new-stage6-run \
  --refresh-candidate-set
```

The pre-Evo-2 Stage 7 baseline bound the exact `384` Stage 6 product candidates,
produced `768` protein/mRNA ranking rows, and selected four provisional portfolio
members per modality. The run remains `needs_data` and emits no formal portfolio.
This baseline is retained specifically so a later checksum-bound Evo 2 import can be
compared against it rather than silently changing the evidence set.
