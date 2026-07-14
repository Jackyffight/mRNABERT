# Stage 5 Model Toolchain

Status: executable local GPU profile implemented for three of five external evidence
categories. Solubility and aggregation remain deliberately `not_evaluated`.

Stage 5 is not constrained to CPU. A provider may be a local GPU model, a local CPU
tool, or a commercial API when it is the best scientific fit. The selection order is:

1. task and expression-context validity;
2. residue- or construct-level output that can be audited;
3. a pinned model/version and stable batch interface;
4. acceptable license, privacy, and sequence-egress policy;
5. runtime and hardware cost.

Hardware is therefore a deployment property, not a scientific selection rule.

## Implemented Profile

The current local installation lives outside the repository and project runtime:

```text
/data00/home/wangzhi.wit/models/design-flow-tools/stage5
```

| Evidence adapter | Provider | Pinned identity | Why selected | Interpretation boundary |
|---|---|---|---|---|
| `signal_peptide` | TMbed | 1.0.2, commit `8cee893523eb655bc9485c00c65336d27a236191`, ProtT5-XL-U50 model inventory hash | Open Apache-2.0 package, local GPU execution, explicit residue labels, and one run can be shared with topology prediction | A predicted signal segment is context, not proof of secretion or cleavage in CHO |
| `transmembrane_topology` | TMbed | Same pinned run | Predicts alpha-helical and beta-strand membrane segments with direction labels and preserves exact residue coordinates | A membrane segment is not automatically a defect; product topology and expression policy must decide that |
| `disorder` | metapredict | V3, commit `34ddeefba8285c57fb5307792ce5f6789f860bef` | Open MIT package, current V3 network, GPU batching, per-residue scores, and explicit IDR boundaries | An IDR is structural context, not automatically an aggregation or manufacturing failure |
| `solubility` | none | `not_evaluated` | No provider has yet been accepted for the declared CHO context | NetSolP is explicitly trained for proteins expressed in *E. coli* and is not relabeled as CHO evidence |
| `aggregation` | none | `not_evaluated` | A structure-aware provider should consume the Stage 3 structures; packaging, license, and output contract remain to be validated | Intrinsic hydrophobic windows are retained separately and are not called an aggregation prediction |

TMbed and metapredict were chosen as the first executable profile, not declared the
permanent best models. SignalP 6.0 and DeepTMHMM are valid stronger provider
candidates where their package/API terms and exact output contract are acceptable.
The adapter schema intentionally permits replacement without changing Stage 5.

## Install And Run

Install the pinned source revisions and download TMbed's approximately 2.25 GB
ProtT5 encoder:

```bash
/data00/home/wangzhi.wit/models/mRNABERT/design-flow/scripts/install_stage5_sequence_models.sh
```

The installer writes `toolchain.json` and a full `requirements.freeze.txt`. Verify it:

```bash
/data00/home/wangzhi.wit/models/mRNABERT/design-flow/scripts/verify_stage5_sequence_models.sh
```

Run all nine current candidates on the present CPU host, register the three evidence
files, rerun the combined Stage 4/5 node, and verify the immutable run:

```bash
/data00/home/wangzhi.wit/models/mRNABERT/design-flow/scripts/run_stage5_sequence_models.sh
```

Where the same absolute toolchain and runtime paths are mounted on a GPU host, select
the GPU explicitly:

```bash
/data00/home/wangzhi.wit/models/mRNABERT/design-flow/scripts/run_stage5_sequence_models.sh cuda:0
```

The model runner is sequential because both models share one small candidate batch.
A requested GPU failure does not silently fall back to CPU. The current `/data00`
host has no visible GPU; using the CPU there changes runtime, not the model or evidence
contract.

## Runtime Artifacts

```text
input/stage5/sequence-models/<input-tool-parameter-identity>/
  candidates.fasta
  signal_peptide.json
  transmembrane_topology.json
  disorder.json
  manifest.json
  raw/tmbed.pred
  raw/tmbed.log
  raw/metapredict.json
  raw/metapredict.log
```

Every evidence file uses `vaxflow.residue-evidence.v1`, binds to the immutable
candidate-batch SHA256 and candidate sequence SHA256, and carries 1-based residue
coordinates. The manifest identity also includes source revisions, TMbed model-file
digest, worker checksum, device, and model parameters. Repeating an identical call
reuses only a fully hash-verified output. Failed jobs are preserved beside the final
directory with `failure.json` and raw logs.

The runner updates only the three corresponding entries in
`input/stage5/developability_specification.json`. It never marks `solubility` or
`aggregation` as evaluated. The subsequent Stage 5 report will therefore contain
real model evidence while correctly remaining `needs_data` until the remaining
providers and human-owned expression/policy decisions are supplied.

## API Provider Contract

A future commercial API provider must be imported through the same evidence schema.
Its adapter must retain the request model/version, parameters, response body,
candidate and sequence hashes, submission time, and provider job ID. API output may
not be used if the provider cannot pin a model revision or if sequence-egress policy
does not permit sending project sequences. An API result never receives additional
authority merely because it is commercial.

## Official Sources

- [TMbed source and usage](https://github.com/BernhoferM/TMbed)
- [TMbed publication](https://doi.org/10.1186/s12859-022-04873-x)
- [metapredict source and V3 documentation](https://github.com/idptools/metapredict)
- [SignalP 6.0 official service](https://services.healthtech.dtu.dk/services/SignalP-6.0/)
- [NetSolP 1.0 scope](https://services.healthtech.dtu.dk/services/NetSolP-1.0/)
