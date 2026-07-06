#!/usr/bin/env python
"""mRNABERT command line entrypoint."""

from __future__ import annotations

import sys
from typing import Optional, Sequence


def usage() -> str:
    return (
        "Usage:\n"
        "  python main.py pretrain [args]\n"
        "  python main.py preprocess [args]\n\n"
        "Examples:\n"
        "  python main.py pretrain --model_name_or_path YYLY66/mRNABERT --train_file sample_data/pre.txt --do_train\n"
        "  python main.py preprocess --raw-dir raw --output-dir data/pretrain\n"
    )


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(usage())
        return

    command = args.pop(0)
    if command == "pretrain":
        from mrnabert.pretrain import main as pretrain_main

        pretrain_main(args)
        return
    if command == "preprocess":
        from data_process.process_pretrain_data_stream import main as preprocess_main

        preprocess_main(args)
        return

    raise SystemExit(f"Unknown command: {command}\n{usage()}")


if __name__ == "__main__":
    main()
