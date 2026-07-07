"""mRNABERT training package.

Public surface:

- ``sequence_codec``: FASTA parsing, longest-ORF CDS detection, and the codon/UTR
  tokenization used for both pretraining and fine-tuning input. Torch-free.
- ``streaming``: torch-free DDP shard/shuffle/cap helpers for local-text streaming.
- ``modeling``: masked-LM model/tokenizer loading (scratch vs pretrained).
- ``pretrain``: stage-1 MLM Trainer orchestration (imports torch/transformers).

``sequence_codec`` and ``streaming`` import only the standard library, so they can
be imported and unit-tested without torch/transformers installed. ``modeling`` and
``pretrain`` are imported lazily by ``main.py`` to keep that guarantee.
"""

from . import sequence_codec, streaming
from .sequence_codec import (
    CDSRegion,
    encode_mrna_sequence,
    find_longest_cds,
    normalize_sequence,
    split_sequence_by_option,
)

__all__ = [
    "sequence_codec",
    "streaming",
    "CDSRegion",
    "encode_mrna_sequence",
    "find_longest_cds",
    "normalize_sequence",
    "split_sequence_by_option",
]
