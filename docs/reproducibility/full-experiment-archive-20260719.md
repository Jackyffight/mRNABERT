# Full non-Git experiment archive

This runbook freezes the complete **state outside Git** for mRNABERT,
ProteinMPNN, and the VaxFlow Stage 1-7/research path. Committed source code and
tracked documentation are intentionally not duplicated.

## What is archived

Three independently verifiable bundles are required:

| Profile | Contents |
|---|---|
| `control-host` | Git-untracked/meaningful-ignored state from both repositories; all Stage 1-7, research, retrieval, showcase, and report runtime; Mock/project inputs; ProteinMPNN source datasets and tar shards; NetMHC, BLAST/MAFFT, TMbed/metapredict models and environments |
| `mrna-gpu` | Every non-Git child under the mRNA NAS root and existing mRNA HDFS archive: training corpus/shards, checkpoints, logs, downstream evaluations, public baseline weights, Evo2 checkpoint/venv, and benchmark artifacts |
| `mpnn-gpu` | Every non-Git child under the MPNN NAS root: tar-shard datasets, training runs, promoted checkpoints, ESMFold2 runtime, Stage 3 jobs/results, and transfer artifacts |

If an immediate GPU-root child is a Git worktree, only its state outside Git is
archived. For repository worktrees this means:

- untracked files;
- ignored files except disposable `__pycache__`, bytecode, test caches, build
  directories, and egg metadata;
- dirty tracked/index patches as metadata;
- remote URL, branch, and exact HEAD commit as metadata.

No Git object bundle and no committed tracked file is included.

## Bundle format

Each source entry becomes its own tar stream, compressed with zstd level 1 and
split into 16 GiB parts. Every bundle contains:

- `bundle.json`, `archives.tsv`, and original absolute-path mappings;
- SHA-256 for every archive part and every metadata file;
- OS/kernel, CPU, mount, CUDA/driver, compiler, system-package, Python, pip, and
  PyTorch snapshots;
- Git identity/status/patch metadata without Git object data;
- self-contained verification and restoration scripts.

The algorithm virtual environments stored inside project/tool roots are archived
byte-for-byte. They are usually not relocatable, so exact replay should restore
the original absolute paths.

## Before packaging

1. Stop every training, evaluation, model download, and process writing into the
   source roots. A read-only report HTTP server is fine.
2. Mount a backup disk at `/mnt/backup` or substitute another **absolute path** in
   every command below. The destination must not be inside any archived root.
3. The control host currently has about 194 GiB free on `/data00`, less than its
   raw non-Git state. Do not place the control bundle on `/data00` without first
   providing more capacity.
4. Keep the bundles private. NetMHC licensing and supplied Mock inputs do not
   permit treating this as a public redistribution package.

On **each GPU worker**, clone the packaging tool into a neutral temporary path.
Do not checkout or pull the experiment worktree that is about to be archived:

```bash
git clone --single-branch \
  --branch feature/research-showcase-20260717 \
  git@github.com:Jackyffight/mRNABERT.git \
  /tmp/vaxflow-repro-tool-20260719
```

## Preflight

Preflight resolves all entries and sizes but writes no bundle:

```bash
/data00/home/wangzhi.wit/models/mRNABERT/scripts/repro_bundle/package_profile.sh \
  /data00/home/wangzhi.wit/models/mRNABERT/scripts/repro_bundle/profiles/control-host.tsv \
  /mnt/backup/vaxflow-20260719/control-host \
  --dry-run

/tmp/vaxflow-repro-tool-20260719/scripts/repro_bundle/package_profile.sh \
  /tmp/vaxflow-repro-tool-20260719/scripts/repro_bundle/profiles/mrna-gpu.tsv \
  /mnt/backup/vaxflow-20260719/mrna-gpu \
  --dry-run

/tmp/vaxflow-repro-tool-20260719/scripts/repro_bundle/package_profile.sh \
  /tmp/vaxflow-repro-tool-20260719/scripts/repro_bundle/profiles/mpnn-gpu.tsv \
  /mnt/backup/vaxflow-20260719/mpnn-gpu \
  --dry-run
```

The neutral clone prevents packaging setup from changing the Git HEAD or dirty
state of either experiment repository.

## Build

Run one command on each owning machine. Do not run packagers concurrently against
the same source root.

```bash
/data00/home/wangzhi.wit/models/mRNABERT/scripts/repro_bundle/package_profile.sh \
  /data00/home/wangzhi.wit/models/mRNABERT/scripts/repro_bundle/profiles/control-host.tsv \
  /mnt/backup/vaxflow-20260719/control-host

/tmp/vaxflow-repro-tool-20260719/scripts/repro_bundle/package_profile.sh \
  /tmp/vaxflow-repro-tool-20260719/scripts/repro_bundle/profiles/mrna-gpu.tsv \
  /mnt/backup/vaxflow-20260719/mrna-gpu

/tmp/vaxflow-repro-tool-20260719/scripts/repro_bundle/package_profile.sh \
  /tmp/vaxflow-repro-tool-20260719/scripts/repro_bundle/profiles/mpnn-gpu.tsv \
  /mnt/backup/vaxflow-20260719/mpnn-gpu
```

Append `--part-size 8G` or `--part-size 32G` when a different transfer-part size
is required. No environment variables are needed.

## Verify

Run before deleting or moving any source:

```bash
/mnt/backup/vaxflow-20260719/control-host/tools/verify_bundle.sh \
  /mnt/backup/vaxflow-20260719/control-host
/mnt/backup/vaxflow-20260719/mrna-gpu/tools/verify_bundle.sh \
  /mnt/backup/vaxflow-20260719/mrna-gpu
/mnt/backup/vaxflow-20260719/mpnn-gpu/tools/verify_bundle.sh \
  /mnt/backup/vaxflow-20260719/mpnn-gpu
```

After transfer to final storage, append `--deep`. Deep verification decompresses
and parses every tar stream and therefore adds one full sequential read.

## Restore and replay

First clone the repositories listed under each bundle's
`metadata/environment/git/*/repository.tsv` and checkout the recorded HEAD. Then
restore state outside Git:

```bash
/mnt/backup/vaxflow-20260719/control-host/tools/restore_bundle.sh \
  /mnt/backup/vaxflow-20260719/control-host /
/mnt/backup/vaxflow-20260719/mrna-gpu/tools/restore_bundle.sh \
  /mnt/backup/vaxflow-20260719/mrna-gpu /
/mnt/backup/vaxflow-20260719/mpnn-gpu/tools/restore_bundle.sh \
  /mnt/backup/vaxflow-20260719/mpnn-gpu /
```

The restore refuses a wrong Git HEAD, refuses to overwrite normal archive
targets, uses `tar --keep-old-files` for Git-state restoration, and then applies
the captured staged and unstaged patches to the cloned worktrees.

For inspection only, a prefix such as `/srv/vaxflow-inspection` may replace `/`.
Hard-coded launchers will not run there until the original paths are mounted or
mapped.

## Deliberate exclusions

- all committed Git objects and tracked files;
- SSH keys, Git credentials, HF tokens, proxy credentials, and shell history;
- nested VCS metadata inside non-Git algorithm/data roots;
- `.agents`, `.codex`, and arbitrary home-directory state;
- regenerable Python bytecode, test caches, build directories, and egg metadata;
- the OS image and proprietary GPU-driver installer themselves.

The environment snapshot records the rebuild contract. If the platform supports
container/image export, preserve that image separately for the strongest possible
GPU ABI reproduction.
