#!/usr/bin/env python3
"""Download and verify the pinned Evo 2 7B checkpoint."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


REPO_ID = "arcinstitute/evo2_7b"
FILENAME = "evo2_7b.pt"
REVISION = "bda0089f92582d5baabf0f22d9fc85f3588f6b58"
EXPECTED_SIZE = 13_766_621_200
EXPECTED_SHA256 = "c66645929dc1b9c631f5be656da8726f38946315dc9167000a615dd626fcecf4"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(path: Path) -> bool:
    return path.is_file() and path.stat().st_size == EXPECTED_SIZE and sha256(path) == EXPECTED_SHA256


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    destination = args.output_dir / FILENAME
    if verify(destination):
        print(f"Evo 2 checkpoint is ready: {destination}")
        return

    from huggingface_hub import hf_hub_download

    args.output_dir.mkdir(parents=True, exist_ok=True)
    downloaded = Path(
        hf_hub_download(
            repo_id=REPO_ID,
            filename=FILENAME,
            revision=REVISION,
            local_dir=args.output_dir,
        )
    )
    if downloaded != destination and downloaded.is_file():
        raise RuntimeError(f"Unexpected Evo 2 download path: {downloaded}")
    if not verify(destination):
        raise RuntimeError(f"Evo 2 checkpoint failed size/SHA-256 verification: {destination}")
    print(f"Evo 2 checkpoint is ready: {destination}")


if __name__ == "__main__":
    main()
