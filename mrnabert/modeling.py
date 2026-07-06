"""Model loading adapters for mRNABERT training."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from transformers import AutoConfig, AutoModelForMaskedLM, AutoTokenizer


logger = logging.getLogger(__name__)

ATTENTION_BACKENDS = {"remote-safe", "remote-triton"}


@dataclass(frozen=True)
class ModelRuntimeConfig:
    model_name_or_path: str
    config_name: Optional[str] = None
    tokenizer_name: Optional[str] = None
    cache_dir: Optional[str] = None
    model_revision: str = "main"
    use_auth_token: bool = False
    use_fast_tokenizer: bool = True
    model_max_length: int = 1024
    low_cpu_mem_usage: bool = False
    attention_backend: str = "remote-safe"
    triton_fallback_attention_dropout: float = 1e-12
    ignore_mismatched_sizes: bool = True

    def __post_init__(self) -> None:
        if self.attention_backend not in ATTENTION_BACKENDS:
            allowed = ", ".join(sorted(ATTENTION_BACKENDS))
            raise ValueError(f"attention_backend must be one of: {allowed}")
        if not self.model_name_or_path:
            raise ValueError("model_name_or_path is required")


@dataclass(frozen=True)
class ModelBundle:
    config: object
    tokenizer: object
    model: object


def _auth_kwargs(use_auth_token: bool) -> dict:
    if not use_auth_token:
        return {}
    return {"use_auth_token": True}


def _pretrained_kwargs(runtime: ModelRuntimeConfig) -> dict:
    kwargs = {
        "cache_dir": runtime.cache_dir,
        "revision": runtime.model_revision,
    }
    kwargs.update(_auth_kwargs(runtime.use_auth_token))
    return kwargs


def _force_pytorch_attention_if_needed(config: object, runtime: ModelRuntimeConfig) -> None:
    if runtime.attention_backend != "remote-safe":
        return
    if not hasattr(config, "attention_probs_dropout_prob"):
        return

    attention_dropout = getattr(config, "attention_probs_dropout_prob")
    fallback_dropout = runtime.triton_fallback_attention_dropout
    if attention_dropout == 0 and fallback_dropout > 0:
        logger.warning(
            "Using remote-safe attention backend: setting attention_probs_dropout_prob=%s "
            "to bypass the legacy Triton kernel in YYLY66/mRNABERT remote code.",
            fallback_dropout,
        )
        setattr(config, "attention_probs_dropout_prob", fallback_dropout)


def load_mlm_model_and_tokenizer(runtime: ModelRuntimeConfig) -> ModelBundle:
    """Load the mRNABERT masked-LM model behind a small, explicit interface.

    The HuggingFace model relies on remote code. Its bundled Triton flash-attn
    kernel is brittle on modern PyTorch/Triton stacks, so the default backend is
    ``remote-safe``: keep the remote architecture and weights, but force its
    PyTorch attention fallback. Use ``remote-triton`` only on a proven compatible
    runtime.
    """

    pretrained_kwargs = _pretrained_kwargs(runtime)
    config_source = runtime.config_name or runtime.model_name_or_path
    tokenizer_source = runtime.tokenizer_name or runtime.model_name_or_path

    # YYLY66/mRNABERT ships a remote BertForMaskedLM whose config_class points
    # at Transformers' built-in BertConfig. Loading the config through remote
    # code creates a different Python class and AutoModel registration fails.
    config = AutoConfig.from_pretrained(config_source, trust_remote_code=False, **pretrained_kwargs)
    _force_pytorch_attention_if_needed(config, runtime)

    tokenizer = AutoTokenizer.from_pretrained(
        tokenizer_source,
        use_fast=runtime.use_fast_tokenizer,
        model_max_length=runtime.model_max_length,
        trust_remote_code=True,
        **pretrained_kwargs,
    )

    model = AutoModelForMaskedLM.from_pretrained(
        runtime.model_name_or_path,
        from_tf=bool(".ckpt" in runtime.model_name_or_path),
        config=config,
        trust_remote_code=True,
        low_cpu_mem_usage=runtime.low_cpu_mem_usage,
        ignore_mismatched_sizes=runtime.ignore_mismatched_sizes,
        **pretrained_kwargs,
    )

    embedding_size = model.get_input_embeddings().weight.shape[0]
    if len(tokenizer) > embedding_size:
        logger.info("Resizing token embeddings from %s to %s", embedding_size, len(tokenizer))
        model.resize_token_embeddings(len(tokenizer))

    return ModelBundle(config=config, tokenizer=tokenizer, model=model)
