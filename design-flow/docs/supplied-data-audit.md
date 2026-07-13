# Supplied Sequence Archive Audit

## Scope

- Source: `datasets/亚单位疫苗设计.rar`
- Archive SHA-256:
  `ce2baaf7d83faf6ed12bff3604d9fdeeb606a64eb74970d3edf7c7857d5ad5ad`
- Audit date: 2026-07-13
- Archive format: RAR5, integrity test passed
- Contents: A33, B5, and L1 source/variant FASTA files, ALAB and ALAL
  fusion FASTA files, and SnapGene `.dna` files

Raw sequences and plasmid files are intentionally not committed. Results below
come from standard-code translation of the FASTA files in the supplied archive.
They are data-integrity findings, not biological efficacy conclusions.

## Pair Audit

| Pair | AA length | CDS nt | Exact translation | Finding |
|---|---:|---:|---:|---|
| A33 full, original CDS | 196 | 591 | yes | Suitable as an M0 source pair |
| A33 full, optimized CDS | 196 | 591 | yes | Sequence-consistent alternative CDS |
| B5 full, original CDS | 225 | 678 | yes | Suitable as an M0 source pair |
| B5 full, optimized CDS | 225 | 591 | no | Hard failure; translated CDS exactly equals supplied A33 full AA |
| L1 full, original CDS | 245 | 738 | yes | Suitable as an M0 source pair |
| L1 full, optimized CDS | 245 | 738 | yes | Sequence-consistent alternative CDS |
| A33.1 extracellular truncation | 117 | 354 | yes | No ATG start; segment is not a standalone expression CDS as supplied |
| A33.2 extracellular truncation | 93 | 282 | yes | No ATG start; filename range `S101-R93` is internally inconsistent |
| B5 extracellular truncation | 172 | 519 | yes | No ATG start; segment is not a standalone expression CDS as supplied |
| L1 extracellular truncation | 179 | 567 | no, annotated difference | CDS translates to `M` + supplied AA + `DYKDDDDK` FLAG tag |
| ALAB fusion | 561 | 1689 | no, annotated difference | CDS translates to `M` + supplied AA |
| ALAL fusion | 568 | 1710 | no, annotated difference | CDS translates to `M` + supplied AA |

## Decisions

1. Use A33, B5, and L1 full-length AA plus their original, unoptimized CDS as the
   first three M0 records. They pass exact sequence consistency.
2. Quarantine the supplied B5 optimized CDS until it is replaced or confirmed;
   it must not enter an optimization or synthesis workflow under the B5 label.
3. Preserve truncations and manual fusions as M1 comparison candidates, not as the
   three original M0 records.
4. Represent initiator methionine, tags, linkers, signal peptides, and truncation
   boundaries explicitly in candidate lineage. Strict AA/CDS equality remains the
   default when no such transformation is declared.
5. Treat filenames and headers as labels only. Sequence hashes and explicit
   candidate manifests are the source of identity.
6. SnapGene plasmid records were inventoried but not interpreted in M0. A later
   plasmid adapter must extract features and the intended expression cassette
   before those files can be compared with standalone FASTA records.

The next executable action is to place the three approved full-length source pairs
under `/data00/home/wangzhi.wit/models/design-flow-runtime/three-protein/input/`,
run `vaxflow validate`, and freeze the first sequence-audit node before generating
any new construct.
