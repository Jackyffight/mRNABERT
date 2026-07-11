"""Stage-1 masked-language-model pretraining for mRNABERT."""

from __future__ import annotations

import inspect
import json
import logging
import math
import os
import sys
from dataclasses import asdict, dataclass, field
from glob import glob
from itertools import chain
from pathlib import Path
from typing import Optional, Sequence

import datasets
import numpy as np
import torch
import transformers
from datasets import load_dataset
try:
    from datasets.distributed import split_dataset_by_node
except ImportError:  # pragma: no cover - older datasets fallback
    split_dataset_by_node = None
from transformers import (
    DataCollatorForLanguageModeling,
    HfArgumentParser,
    Trainer,
    TrainerCallback,
    TrainingArguments as HFTrainingArguments,
    set_seed,
)
from transformers.trainer_utils import get_last_checkpoint
from transformers.utils.versions import require_version

try:  # transformers>=4.44 renamed this to is_torch_xla_available and dropped the old alias
    from transformers import is_torch_tpu_available
except ImportError:  # pragma: no cover - depends on installed transformers version
    try:
        from transformers import is_torch_xla_available as is_torch_tpu_available
    except ImportError:
        def is_torch_tpu_available(*_args, **_kwargs):  # type: ignore[misc]
            return False

from . import streaming
from . import streaming_state
from .modeling import ModelRuntimeConfig, load_mlm_model_and_tokenizer


logger = logging.getLogger(__name__)


def get_dataset_auth_kwargs(use_auth_token: bool) -> dict:
    if not use_auth_token:
        return {}
    if "token" in inspect.signature(load_dataset).parameters:
        return {"token": True}
    return {"use_auth_token": True}


@dataclass
class ModelArguments:
    model_name_or_path: str = field(default="assets/mrnabert-base")
    model_type: Optional[str] = field(default=None, metadata={"help": "Accepted for run_mlm.py compatibility."})
    config_overrides: Optional[str] = field(
        default=None,
        metadata={"help": "Accepted for run_mlm.py compatibility; not used by the packaged loader."},
    )
    config_name: Optional[str] = field(default=None)
    tokenizer_name: Optional[str] = field(default=None)
    cache_dir: Optional[str] = field(default=None)
    init_mode: str = field(
        default="scratch",
        metadata={"help": "scratch initializes from config; pretrained loads model_name_or_path weights."},
    )
    use_fast_tokenizer: bool = field(default=True)
    model_revision: str = field(default="main")
    use_auth_token: bool = field(default=False)
    low_cpu_mem_usage: bool = field(default=False)
    attention_backend: str = field(
        default="pytorch",
        metadata={
            "help": "pytorch uses the built-in Transformers BERT path for scratch training; "
            "remote-safe/remote-triton are only for explicit pretrained baseline runs."
        },
    )
    use_triton_flash_attn: bool = field(
        default=False,
        metadata={"help": "Compatibility alias for --attention_backend remote-triton."},
    )
    triton_fallback_attention_dropout: float = field(default=1e-12)
    ignore_mismatched_sizes: bool = field(default=True)

    def runtime_config(self, model_max_length: int) -> ModelRuntimeConfig:
        if self.config_overrides is not None:
            raise ValueError("--config_overrides is not supported by the packaged mRNABERT loader.")
        backend = "remote-triton" if self.use_triton_flash_attn else self.attention_backend
        return ModelRuntimeConfig(
            model_name_or_path=self.model_name_or_path,
            config_name=self.config_name,
            tokenizer_name=self.tokenizer_name,
            cache_dir=self.cache_dir,
            init_mode=self.init_mode,
            model_revision=self.model_revision,
            use_auth_token=self.use_auth_token,
            use_fast_tokenizer=self.use_fast_tokenizer,
            model_max_length=model_max_length,
            low_cpu_mem_usage=self.low_cpu_mem_usage,
            attention_backend=backend,
            triton_fallback_attention_dropout=self.triton_fallback_attention_dropout,
            ignore_mismatched_sizes=self.ignore_mismatched_sizes,
        )


@dataclass
class DataTrainingArguments:
    dataset_name: Optional[str] = field(default=None)
    dataset_config_name: Optional[str] = field(default=None)
    train_file: Optional[str] = field(default=None)
    validation_file: Optional[str] = field(default=None)
    dataset_cache_dir: Optional[str] = field(
        default=None,
        metadata={"help": "Cache directory for HuggingFace datasets/Arrow files."},
    )
    overwrite_cache: bool = field(default=False)
    validation_split_percentage: int = field(default=5)
    max_seq_length: int = field(default=1024)
    preprocessing_num_workers: Optional[int] = field(default=8)
    mlm_probability: float = field(default=0.15)
    line_by_line: bool = field(
        default=True,
        metadata={"help": "Keep one mRNA record per training example. This is the correct default for pre.txt."},
    )
    pad_to_max_length: bool = field(
        default=False,
        metadata={"help": "Use dynamic padding by default for better throughput."},
    )
    max_train_samples: Optional[int] = field(default=None)
    max_eval_samples: Optional[int] = field(default=None)
    streaming: bool = field(default=False)
    streaming_reader: str = field(
        default="line-stride",
        metadata={
            "help": "line-stride sequentially reads local text and shards by rank/worker; "
            "file-shard assigns matched files by distributed rank; "
            "byte-range uses seek-based sharding; hf uses HuggingFace datasets streaming."
        },
    )
    streaming_shuffle_buffer: int = field(
        default=0,
        metadata={"help": "Bounded per-rank line shuffle buffer for local text streaming. 0 disables shuffle."},
    )
    streaming_shuffle_seed: int = field(
        default=42,
        metadata={"help": "Base seed for local streaming shuffle buffers."},
    )
    streaming_resume_skip_samples: Optional[int] = field(
        default=None,
        metadata={
            "help": "Global raw-example count to skip before local streaming training. "
            "A checkpoint streaming_state.json takes precedence over the legacy global-step fallback."
        },
    )
    streaming_resume_mode: str = field(
        default="exact-replay",
        metadata={
            "help": "Streaming resume strategy: exact-replay reconstructs the bounded shuffle by scanning "
            "the prefix; fast-seek seeks file shards to the approximate corpus byte offset."
        },
    )
    streaming_resume_global_step: int = field(default=0)
    streaming_resume_cursor_source: str = field(default="fresh")
    streaming_shard_manifest: Optional[str] = field(
        default=None,
        metadata={"help": "Shard manifest used to verify corpus identity across resumes."},
    )

    def __post_init__(self) -> None:
        if self.streaming:
            require_version("datasets>=2.0.0", "The streaming feature requires datasets>=2.0.0")
        if self.streaming_reader not in {"line-stride", "file-shard", "byte-range", "hf"}:
            raise ValueError("--streaming_reader must be line-stride, file-shard, byte-range, or hf.")
        if self.streaming_shuffle_buffer < 0:
            raise ValueError("--streaming_shuffle_buffer must be >= 0.")
        if self.streaming_resume_skip_samples is not None and self.streaming_resume_skip_samples < 0:
            raise ValueError("--streaming_resume_skip_samples must be >= 0.")
        if self.streaming_resume_mode not in {"exact-replay", "fast-seek"}:
            raise ValueError("--streaming_resume_mode must be exact-replay or fast-seek.")
        if self.streaming_resume_global_step < 0:
            raise ValueError("--streaming_resume_global_step must be >= 0.")
        if self.dataset_name is None and self.train_file is None and self.validation_file is None:
            raise ValueError("Need either a dataset name or a train/validation file.")
        for value, label in ((self.train_file, "train_file"), (self.validation_file, "validation_file")):
            if value is None:
                continue
            extension = Path(value).suffix.lower().lstrip(".")
            if extension not in {"csv", "json", "txt"}:
                raise ValueError(f"{label} must be a csv, json, or txt file.")


@dataclass
class TrainingArguments(HFTrainingArguments):
    num_train_epochs: float = field(default=10.0)
    gradient_accumulation_steps: int = field(default=4)
    model_max_length: int = field(default=1024)
    weight_decay: float = field(default=0.01)
    optim: str = field(default="adamw_torch")
    save_safetensors: bool = field(default=False)


class TrainingSummaryCallback(TrainerCallback):
    def on_train_begin(self, args, state, control, **kwargs):  # noqa: D401
        if not getattr(state, "is_local_process_zero", True):
            return
        world_size = max(1, getattr(args, "world_size", 1))
        effective_batch = args.train_batch_size * args.gradient_accumulation_steps * world_size
        logger.info(
            "Effective batch size: %s = train_batch(%s) * grad_accum(%s) * world_size(%s)",
            effective_batch,
            args.train_batch_size,
            args.gradient_accumulation_steps,
            world_size,
        )

        # Log the *effective* LR at train start. On resume the LR is driven by the
        # restored optimizer/scheduler state and the recomputed schedule (new
        # max_steps/warmup), not by the configured --learning_rate, so print the
        # real value to remove the resume-LR ambiguity. The per-step `learning_rate`
        # in the Trainer logs then tracks the actual trajectory.
        lr_scheduler = kwargs.get("lr_scheduler")
        optimizer = kwargs.get("optimizer")
        effective_lr = None
        if lr_scheduler is not None and hasattr(lr_scheduler, "get_last_lr"):
            try:
                effective_lr = lr_scheduler.get_last_lr()[0]
            except Exception:  # pragma: no cover - scheduler may be uninitialized
                effective_lr = None
        if effective_lr is None and optimizer is not None:
            try:
                effective_lr = optimizer.param_groups[0]["lr"]
            except Exception:  # pragma: no cover
                effective_lr = None
        logger.info(
            "LR/schedule at train start: effective_lr=%s configured_lr=%s scheduler=%s "
            "warmup_steps=%s max_steps=%s global_step=%s",
            effective_lr,
            getattr(args, "learning_rate", None),
            getattr(args, "lr_scheduler_type", None),
            getattr(args, "warmup_steps", None),
            getattr(args, "max_steps", None),
            getattr(state, "global_step", None),
        )


class StreamingStateCallback(TrainerCallback):
    """Persist the logical raw-example cursor beside every Trainer checkpoint."""

    def __init__(self, data_args: DataTrainingArguments) -> None:
        self.resume_global_step = data_args.streaming_resume_global_step
        self.resume_sample_cursor = int(data_args.streaming_resume_skip_samples or 0)
        self.resume_cursor_source = data_args.streaming_resume_cursor_source
        self.resume_mode = data_args.streaming_resume_mode
        self.streaming_reader = data_args.streaming_reader
        self.shuffle_buffer = data_args.streaming_shuffle_buffer
        self.shuffle_seed = data_args.streaming_shuffle_seed
        self.shard_manifest_path = data_args.streaming_shard_manifest

    def _write(self, args, state, directory: Path) -> None:
        if not getattr(state, "is_world_process_zero", True):
            return
        world_size = max(1, getattr(args, "world_size", 1))
        batch_size = streaming_state.effective_batch_size(
            args.per_device_train_batch_size,
            args.gradient_accumulation_steps,
            world_size,
        )
        checkpoint_state = streaming_state.build_checkpoint_state(
            global_step=int(state.global_step),
            resume_global_step=self.resume_global_step,
            resume_sample_cursor=self.resume_sample_cursor,
            effective_batch=batch_size,
            streaming_reader=self.streaming_reader,
            shuffle_buffer=self.shuffle_buffer,
            shuffle_seed=self.shuffle_seed,
            world_size=world_size,
            dataloader_num_workers=args.dataloader_num_workers,
            shard_manifest_path=self.shard_manifest_path,
            resume_cursor_source=self.resume_cursor_source,
            resume_mode=self.resume_mode,
        )
        path = streaming_state.write_checkpoint_state(directory, checkpoint_state)
        logger.info(
            "Saved streaming cursor state to %s: cursor=%s corpus_pass=%s corpus_offset=%s",
            path,
            checkpoint_state.next_sample_cursor,
            checkpoint_state.corpus_pass,
            checkpoint_state.corpus_offset,
        )

    def on_save(self, args, state, control, **kwargs):  # noqa: D401
        self._write(args, state, Path(args.output_dir) / f"checkpoint-{state.global_step}")

    def on_train_end(self, args, state, control, **kwargs):  # noqa: D401
        self._write(args, state, Path(args.output_dir))


def get_distributed_rank_info() -> tuple[int, int]:
    if torch.distributed.is_available() and torch.distributed.is_initialized():
        return torch.distributed.get_rank(), torch.distributed.get_world_size()
    return int(os.environ.get("RANK", "0")), int(os.environ.get("WORLD_SIZE", "1"))


def resolve_text_files(pattern: str) -> list[str]:
    files = sorted(glob(pattern))
    if not files and Path(pattern).is_file():
        files = [pattern]
    if not files:
        raise ValueError(f"No text files matched: {pattern}")
    return files


class _StreamingTokenizedTextDataset(torch.utils.data.IterableDataset):
    """Base for local-text streaming readers.

    The sharding, bounded shuffle, and sample cap live in torch-free helpers in
    ``mrnabert.streaming`` (unit-testable without torch); each subclass only names
    which reader it is. ``max_samples`` is a GLOBAL cap: it is divided across the
    rank x dataloader-worker partitions so the total examples consumed is
    ~= max_samples regardless of world size, matching the non-streaming
    ``.select`` semantics rather than the old per-partition multiplication.
    """

    reader_name: str = ""

    def __init__(
        self,
        files: Sequence[str],
        tokenizer,
        max_seq_length: int,
        pad_to_max_length: bool,
        max_samples: Optional[int] = None,
        shuffle_buffer: int = 0,
        shuffle_seed: int = 42,
        resume_skip_samples: int = 0,
        resume_start_fraction: float = 0.0,
        resume_stream_epoch: int = 0,
        repeat: bool = False,
    ) -> None:
        self.files = list(files)
        self.tokenizer = tokenizer
        self.max_seq_length = max_seq_length
        self.pad_to_max_length = pad_to_max_length
        self.max_samples = max_samples
        self.shuffle_buffer = shuffle_buffer
        self.shuffle_seed = shuffle_seed
        self.resume_skip_samples = resume_skip_samples
        self.resume_start_fraction = resume_start_fraction
        self.resume_stream_epoch = resume_stream_epoch
        self.repeat = repeat

    def _tokenize(self, line: str) -> dict:
        padding = "max_length" if self.pad_to_max_length else False
        return self.tokenizer(
            line,
            padding=padding,
            truncation=True,
            max_length=self.max_seq_length,
            return_special_tokens_mask=True,
        )

    def __iter__(self):
        rank, world_size = get_distributed_rank_info()
        worker_info = torch.utils.data.get_worker_info()
        worker_id = worker_info.id if worker_info is not None else 0
        num_workers = worker_info.num_workers if worker_info is not None else 1

        partition_id, num_partitions = streaming.partition_id_and_count(rank, world_size, worker_id, num_workers)
        cap = streaming.per_partition_cap(self.max_samples, num_partitions)
        skip_remaining = streaming.partition_skip(self.resume_skip_samples, partition_id, num_partitions)

        yielded = 0
        stream_epoch = self.resume_stream_epoch
        start_fraction = self.resume_start_fraction
        while True:
            raw_lines = streaming.iter_reader_lines(
                self.reader_name,
                self.files,
                rank,
                world_size,
                worker_id,
                num_workers,
                start_fraction=start_fraction,
            )
            seed = self.shuffle_seed + partition_id + (stream_epoch * num_partitions)
            saw_lines = False
            for line in streaming.iter_bounded_shuffle(raw_lines, self.shuffle_buffer, seed):
                saw_lines = True
                if skip_remaining > 0:
                    skip_remaining -= 1
                    continue
                if cap is not None and yielded >= cap:
                    return
                yielded += 1
                yield self._tokenize(line)

            if skip_remaining > 0 and self.repeat:
                stream_epoch += 1
                continue
            if cap is not None or not self.repeat:
                return
            # A fast-seek pivot can land after the last complete line in a small
            # partition. Continue from the next full pass instead of exhausting
            # that worker and starving a DDP rank.
            if not saw_lines and start_fraction == 0.0:
                return
            stream_epoch += 1
            start_fraction = 0.0


class LineStrideTokenizedTextDataset(_StreamingTokenizedTextDataset):
    """Every rank scans each file, keeping lines by (line_index % num_partitions)."""

    reader_name = "line-stride"


class FileShardTokenizedTextDataset(_StreamingTokenizedTextDataset):
    """Whole files assigned by rank, then lines within a file by dataloader worker."""

    reader_name = "file-shard"


class ByteRangeTokenizedTextDataset(_StreamingTokenizedTextDataset):
    """Each partition reads a contiguous seek-based byte range of every file."""

    reader_name = "byte-range"


def setup_logging(training_args: TrainingArguments) -> None:
    logging.basicConfig(
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        datefmt="%m/%d/%Y %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    if training_args.should_log:
        transformers.utils.logging.set_verbosity_info()

    log_level = training_args.get_process_log_level()
    logger.setLevel(log_level)
    datasets.utils.logging.set_verbosity(log_level)
    transformers.utils.logging.set_verbosity(log_level)
    transformers.utils.logging.enable_default_handler()
    transformers.utils.logging.enable_explicit_format()

    logger.warning(
        "Process rank: %s, device: %s, n_gpu: %s, distributed training: %s, fp16: %s, bf16: %s",
        training_args.local_rank,
        training_args.device,
        training_args.n_gpu,
        bool(training_args.local_rank != -1),
        training_args.fp16,
        training_args.bf16,
    )
    logger.info("Training/evaluation parameters %s", training_args)


def configure_torch_runtime(training_args: TrainingArguments) -> None:
    local_rank = int(os.environ.get("LOCAL_RANK", "-1"))
    if local_rank >= 0 and torch.cuda.is_available():
        torch.cuda.set_device(local_rank)
    if training_args.tf32 is not None and torch.cuda.is_available():
        torch.backends.cuda.matmul.allow_tf32 = bool(training_args.tf32)
        torch.backends.cudnn.allow_tf32 = bool(training_args.tf32)


def detect_last_checkpoint(training_args: TrainingArguments) -> Optional[str]:
    if not os.path.isdir(training_args.output_dir) or not training_args.do_train or training_args.overwrite_output_dir:
        return None

    last_checkpoint = get_last_checkpoint(training_args.output_dir)
    if last_checkpoint is None and len(os.listdir(training_args.output_dir)) > 0:
        raise ValueError(
            f"Output directory ({training_args.output_dir}) already exists and is not empty. "
            "Use --overwrite_output_dir to train from scratch."
        )
    if last_checkpoint is not None and training_args.resume_from_checkpoint is None:
        logger.info("Checkpoint detected, resuming from %s", last_checkpoint)
    return last_checkpoint


def _file_extension(path: str) -> str:
    extension = Path(path).suffix.lower().lstrip(".")
    return "text" if extension == "txt" else extension


def load_raw_datasets(data_args: DataTrainingArguments, model_args: ModelArguments, do_eval: bool):
    dataset_auth_kwargs = get_dataset_auth_kwargs(model_args.use_auth_token)
    dataset_cache_dir = data_args.dataset_cache_dir or model_args.cache_dir
    logger.info("Using datasets cache dir: %s", dataset_cache_dir or "default")

    if data_args.dataset_name is not None:
        raw_datasets = load_dataset(
            data_args.dataset_name,
            data_args.dataset_config_name,
            cache_dir=dataset_cache_dir,
            streaming=data_args.streaming,
            **dataset_auth_kwargs,
        )
        if do_eval and "validation" not in raw_datasets.keys():
            raw_datasets["validation"] = load_dataset(
                data_args.dataset_name,
                data_args.dataset_config_name,
                split=f"train[:{data_args.validation_split_percentage}%]",
                cache_dir=dataset_cache_dir,
                streaming=data_args.streaming,
                **dataset_auth_kwargs,
            )
            raw_datasets["train"] = load_dataset(
                data_args.dataset_name,
                data_args.dataset_config_name,
                split=f"train[{data_args.validation_split_percentage}%:]",
                cache_dir=dataset_cache_dir,
                streaming=data_args.streaming,
                **dataset_auth_kwargs,
            )
        return raw_datasets

    data_files = {}
    extension = None
    if data_args.train_file is not None:
        data_files["train"] = data_args.train_file
        extension = _file_extension(data_args.train_file)
    if data_args.validation_file is not None:
        data_files["validation"] = data_args.validation_file
        extension = _file_extension(data_args.validation_file)
    if extension is None:
        raise ValueError("No local data files were provided.")

    raw_datasets = load_dataset(
        extension,
        data_files=data_files,
        cache_dir=dataset_cache_dir,
        streaming=data_args.streaming,
    )
    if do_eval and "validation" not in raw_datasets.keys():
        raw_datasets["validation"] = load_dataset(
            extension,
            data_files=data_files,
            split=f"train[:{data_args.validation_split_percentage}%]",
            cache_dir=dataset_cache_dir,
            streaming=data_args.streaming,
        )
        raw_datasets["train"] = load_dataset(
            extension,
            data_files=data_files,
            split=f"train[{data_args.validation_split_percentage}%:]",
            cache_dir=dataset_cache_dir,
            streaming=data_args.streaming,
        )
    return raw_datasets


def shard_streaming_datasets(raw_datasets, data_args: DataTrainingArguments):
    if not data_args.streaming:
        return raw_datasets
    if not torch.distributed.is_available() or not torch.distributed.is_initialized():
        return raw_datasets

    world_size = torch.distributed.get_world_size()
    rank = torch.distributed.get_rank()
    if world_size <= 1:
        return raw_datasets
    if split_dataset_by_node is None:
        logger.warning("datasets.distributed.split_dataset_by_node is unavailable; streaming data is not rank-sharded.")
        return raw_datasets

    logger.info("Sharding streaming datasets by distributed rank: rank=%s world_size=%s", rank, world_size)
    for split in list(raw_datasets.keys()):
        raw_datasets[split] = split_dataset_by_node(raw_datasets[split], rank=rank, world_size=world_size)
    return raw_datasets


def can_use_local_text_streaming(data_args: DataTrainingArguments) -> bool:
    text_file = data_args.train_file or data_args.validation_file
    return (
        data_args.streaming
        and data_args.streaming_reader in {"line-stride", "file-shard", "byte-range"}
        and data_args.dataset_name is None
        and data_args.line_by_line
        and text_file is not None
        and _file_extension(text_file) == "text"
    )


def build_local_text_streaming_datasets(tokenizer, data_args: DataTrainingArguments):
    rank, world_size = get_distributed_rank_info()
    max_seq_length = resolve_max_seq_length(tokenizer, data_args)
    if data_args.streaming_reader == "byte-range":
        dataset_cls = ByteRangeTokenizedTextDataset
    elif data_args.streaming_reader == "file-shard":
        dataset_cls = FileShardTokenizedTextDataset
    else:
        dataset_cls = LineStrideTokenizedTextDataset
    logger.info(
        "Using %s streaming reader, rank=%s, world_size=%s, shuffle_buffer=%s",
        data_args.streaming_reader,
        rank,
        world_size,
        data_args.streaming_shuffle_buffer,
    )

    train_dataset = None
    if data_args.train_file is not None:
        train_files = resolve_text_files(data_args.train_file)
        streaming.validate_reader_partitions(data_args.streaming_reader, len(train_files), world_size)
        logger.info("Using %s train file(s) for local streaming", len(train_files))
        logical_resume_cursor = int(data_args.streaming_resume_skip_samples or 0)
        physical_resume_skip = logical_resume_cursor
        resume_start_fraction = 0.0
        resume_stream_epoch = 0
        if logical_resume_cursor and data_args.streaming_resume_mode == "fast-seek":
            if data_args.streaming_reader != "file-shard":
                raise ValueError("--streaming_resume_mode fast-seek requires --streaming_reader file-shard.")
            manifest_path = Path(data_args.streaming_shard_manifest) if data_args.streaming_shard_manifest else None
            corpus_samples = streaming_state.load_corpus_samples(manifest_path)
            if not corpus_samples:
                raise ValueError(
                    "--streaming_resume_mode fast-seek requires a shard manifest with total_lines."
                )
            corpus_offset = logical_resume_cursor % corpus_samples
            resume_stream_epoch = logical_resume_cursor // corpus_samples
            resume_start_fraction = corpus_offset / corpus_samples
            physical_resume_skip = 0
            logger.warning(
                "Fast-seek streaming resume: logical_cursor=%s corpus_pass=%s corpus_offset=%s/%s "
                "start_fraction=%.6f. This avoids prefix replay; variable-length records make the "
                "physical position approximate.",
                logical_resume_cursor,
                resume_stream_epoch,
                corpus_offset,
                corpus_samples,
                resume_start_fraction,
            )
        elif logical_resume_cursor:
            logger.info(
                "Skipping %s global raw training examples for streaming resume",
                logical_resume_cursor,
            )
        train_dataset = dataset_cls(
            files=train_files,
            tokenizer=tokenizer,
            max_seq_length=max_seq_length,
            pad_to_max_length=data_args.pad_to_max_length,
            max_samples=data_args.max_train_samples,
            shuffle_buffer=data_args.streaming_shuffle_buffer,
            shuffle_seed=data_args.streaming_shuffle_seed,
            resume_skip_samples=physical_resume_skip,
            resume_start_fraction=resume_start_fraction,
            resume_stream_epoch=resume_stream_epoch,
            repeat=data_args.max_train_samples is None,
        )

    eval_dataset = None
    if data_args.validation_file is not None:
        validation_files = resolve_text_files(data_args.validation_file)
        # Same guard as the train files: a file-shard eval with fewer validation
        # files than ranks would starve some ranks and hang the eval all-gather.
        streaming.validate_reader_partitions(data_args.streaming_reader, len(validation_files), world_size)
        logger.info("Using %s validation file(s) for local streaming", len(validation_files))
        eval_dataset = dataset_cls(
            files=validation_files,
            tokenizer=tokenizer,
            max_seq_length=max_seq_length,
            pad_to_max_length=data_args.pad_to_max_length,
            max_samples=data_args.max_eval_samples,
            shuffle_buffer=0,
            shuffle_seed=data_args.streaming_shuffle_seed,
            resume_skip_samples=0,
            repeat=False,
        )
    return train_dataset, eval_dataset


def resolve_max_seq_length(tokenizer, data_args: DataTrainingArguments) -> int:
    tokenizer_limit = tokenizer.model_max_length
    if tokenizer_limit and tokenizer_limit > 0:
        if data_args.max_seq_length > tokenizer_limit:
            logger.warning(
                "max_seq_length=%s is larger than tokenizer.model_max_length=%s; using tokenizer limit.",
                data_args.max_seq_length,
                tokenizer_limit,
            )
        return min(data_args.max_seq_length, tokenizer_limit)
    return data_args.max_seq_length


def tokenize_datasets(raw_datasets, tokenizer, data_args: DataTrainingArguments, training_args: TrainingArguments):
    dataset_for_columns = raw_datasets["train"] if training_args.do_train else raw_datasets["validation"]
    features = getattr(dataset_for_columns, "features", None)
    column_names = list(features) if features else ["text"]
    text_column_name = "text" if "text" in column_names else column_names[0]
    max_seq_length = resolve_max_seq_length(tokenizer, data_args)

    if data_args.line_by_line:
        padding = "max_length" if data_args.pad_to_max_length else False

        def tokenize_function(examples):
            lines = [
                line
                for line in examples[text_column_name]
                if isinstance(line, str) and len(line) > 0 and not line.isspace()
            ]
            return tokenizer(
                lines,
                padding=padding,
                truncation=True,
                max_length=max_seq_length,
                return_special_tokens_mask=True,
            )

        with training_args.main_process_first(desc="dataset map tokenization"):
            if data_args.streaming:
                return raw_datasets.map(tokenize_function, batched=True, remove_columns=column_names)
            return raw_datasets.map(
                tokenize_function,
                batched=True,
                num_proc=data_args.preprocessing_num_workers,
                remove_columns=column_names,
                load_from_cache_file=not data_args.overwrite_cache,
                desc="Tokenizing mRNA records",
            )

    def tokenize_function(examples):
        return tokenizer(examples[text_column_name], return_special_tokens_mask=True)

    with training_args.main_process_first(desc="dataset map tokenization"):
        if data_args.streaming:
            tokenized_datasets = raw_datasets.map(tokenize_function, batched=True, remove_columns=column_names)
        else:
            tokenized_datasets = raw_datasets.map(
                tokenize_function,
                batched=True,
                num_proc=data_args.preprocessing_num_workers,
                remove_columns=column_names,
                load_from_cache_file=not data_args.overwrite_cache,
                desc="Tokenizing text",
            )

    def group_texts(examples):
        concatenated = {key: list(chain(*examples[key])) for key in examples.keys()}
        total_length = len(concatenated[list(examples.keys())[0]])
        total_length = (total_length // max_seq_length) * max_seq_length
        return {
            key: [tokens[i : i + max_seq_length] for i in range(0, total_length, max_seq_length)]
            for key, tokens in concatenated.items()
        }

    with training_args.main_process_first(desc="grouping tokenized texts"):
        if data_args.streaming:
            return tokenized_datasets.map(group_texts, batched=True)
        return tokenized_datasets.map(
            group_texts,
            batched=True,
            num_proc=data_args.preprocessing_num_workers,
            load_from_cache_file=not data_args.overwrite_cache,
            desc=f"Grouping text into chunks of {max_seq_length}",
        )


def select_dataset_splits(tokenized_datasets, data_args: DataTrainingArguments, training_args: TrainingArguments):
    train_dataset = None
    eval_dataset = None

    if training_args.do_train:
        if "train" not in tokenized_datasets:
            raise ValueError("--do_train requires a train dataset")
        train_dataset = tokenized_datasets["train"]
        if data_args.max_train_samples is not None and not data_args.streaming:
            train_dataset = train_dataset.select(range(min(len(train_dataset), data_args.max_train_samples)))

    if training_args.do_eval:
        if "validation" not in tokenized_datasets:
            raise ValueError("--do_eval requires a validation dataset")
        eval_dataset = tokenized_datasets["validation"]
        if data_args.max_eval_samples is not None and not data_args.streaming:
            eval_dataset = eval_dataset.select(range(min(len(eval_dataset), data_args.max_eval_samples)))

    return train_dataset, eval_dataset


def build_metrics():
    def preprocess_logits_for_metrics(logits, labels):
        if isinstance(logits, tuple):
            logits = logits[0]
        return logits.argmax(dim=-1)

    def compute_metrics(eval_preds):
        preds, labels = eval_preds
        labels = labels.reshape(-1)
        preds = preds.reshape(-1)
        mask = labels != -100
        labels = labels[mask]
        preds = preds[mask]
        if labels.size == 0:
            return {"accuracy": 0.0}
        return {"accuracy": float(np.mean(preds == labels))}

    return compute_metrics, preprocess_logits_for_metrics


def write_run_manifest(
    output_dir: str,
    model_args: ModelArguments,
    data_args: DataTrainingArguments,
    training_args: TrainingArguments,
    tokenizer,
) -> None:
    manifest = {
        "model_args": asdict(model_args),
        "data_args": asdict(data_args),
        "training_args": training_args.to_dict(),
        "tokenizer": {
            "class": tokenizer.__class__.__name__,
            "model_max_length": tokenizer.model_max_length,
            "vocab_size": len(tokenizer),
        },
        "versions": {
            "python": sys.version,
            "torch": torch.__version__,
            "torch_cuda": torch.version.cuda,
            "transformers": transformers.__version__,
            "datasets": datasets.__version__,
        },
    }
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    with Path(output_dir, "run_manifest.json").open("w") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)


def prepare_streaming_resume_state(
    data_args: DataTrainingArguments,
    training_args: TrainingArguments,
    checkpoint: Optional[str],
) -> None:
    if not training_args.do_train or not can_use_local_text_streaming(data_args):
        return

    if checkpoint is None:
        data_args.streaming_resume_skip_samples = int(data_args.streaming_resume_skip_samples or 0)
        data_args.streaming_resume_global_step = 0
        data_args.streaming_resume_cursor_source = "fresh"
        return

    effective_batch = streaming_state.effective_batch_size(
        training_args.per_device_train_batch_size,
        training_args.gradient_accumulation_steps,
        max(1, training_args.world_size),
    )
    resolved = streaming_state.resolve_resume_state(
        checkpoint=Path(checkpoint),
        fallback_effective_batch=effective_batch,
        override_sample_cursor=data_args.streaming_resume_skip_samples,
        current_shard_manifest_path=data_args.streaming_shard_manifest,
        current_streaming_reader=data_args.streaming_reader,
        current_shuffle_buffer=data_args.streaming_shuffle_buffer,
        current_shuffle_seed=data_args.streaming_shuffle_seed,
        current_world_size=max(1, training_args.world_size),
        current_dataloader_num_workers=training_args.dataloader_num_workers,
    )
    data_args.streaming_resume_global_step = resolved.global_step
    data_args.streaming_resume_skip_samples = resolved.next_sample_cursor
    if data_args.streaming_resume_cursor_source == "fresh":
        data_args.streaming_resume_cursor_source = resolved.source
    logger.info(
        "Resolved streaming resume cursor: global_step=%s cursor=%s source=%s",
        resolved.global_step,
        resolved.next_sample_cursor,
        data_args.streaming_resume_cursor_source,
    )


def build_trainer(
    model_args: ModelArguments,
    data_args: DataTrainingArguments,
    training_args: TrainingArguments,
) -> tuple[Trainer, object, object, object]:
    runtime = model_args.runtime_config(model_max_length=data_args.max_seq_length or training_args.model_max_length)
    bundle = load_mlm_model_and_tokenizer(runtime)

    if can_use_local_text_streaming(data_args):
        train_dataset, eval_dataset = build_local_text_streaming_datasets(bundle.tokenizer, data_args)
    else:
        raw_datasets = load_raw_datasets(data_args, model_args, do_eval=training_args.do_eval)
        raw_datasets = shard_streaming_datasets(raw_datasets, data_args)
        tokenized_datasets = tokenize_datasets(raw_datasets, bundle.tokenizer, data_args, training_args)
        train_dataset, eval_dataset = select_dataset_splits(tokenized_datasets, data_args, training_args)

    pad_to_multiple_of = None
    if not data_args.pad_to_max_length and (training_args.fp16 or training_args.bf16):
        pad_to_multiple_of = 8
    data_collator = DataCollatorForLanguageModeling(
        tokenizer=bundle.tokenizer,
        mlm_probability=data_args.mlm_probability,
        pad_to_multiple_of=pad_to_multiple_of,
    )

    compute_metrics = None
    preprocess_logits_for_metrics = None
    if training_args.do_eval and not training_args.prediction_loss_only and not is_torch_tpu_available():
        compute_metrics, preprocess_logits_for_metrics = build_metrics()

    callbacks = [TrainingSummaryCallback()]
    if training_args.do_train and can_use_local_text_streaming(data_args):
        callbacks.append(StreamingStateCallback(data_args))

    trainer = Trainer(
        model=bundle.model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        tokenizer=bundle.tokenizer,
        data_collator=data_collator,
        compute_metrics=compute_metrics,
        preprocess_logits_for_metrics=preprocess_logits_for_metrics,
        callbacks=callbacks,
    )
    return trainer, train_dataset, eval_dataset, bundle.tokenizer


def run_pretrain(model_args: ModelArguments, data_args: DataTrainingArguments, training_args: TrainingArguments) -> None:
    setup_logging(training_args)
    configure_torch_runtime(training_args)
    last_checkpoint = detect_last_checkpoint(training_args)
    set_seed(training_args.seed)
    checkpoint = training_args.resume_from_checkpoint or last_checkpoint
    prepare_streaming_resume_state(data_args, training_args, checkpoint)

    trainer, train_dataset, eval_dataset, tokenizer = build_trainer(model_args, data_args, training_args)
    if trainer.is_world_process_zero():
        write_run_manifest(training_args.output_dir, model_args, data_args, training_args, tokenizer)

    if training_args.do_train:
        train_result = trainer.train(resume_from_checkpoint=checkpoint)
        trainer.save_model()
        metrics = train_result.metrics
        if train_dataset is not None and hasattr(train_dataset, "__len__"):
            max_train_samples = data_args.max_train_samples or len(train_dataset)
            metrics["train_samples"] = min(max_train_samples, len(train_dataset))
        trainer.log_metrics("train", metrics)
        trainer.save_metrics("train", metrics)
        trainer.save_state()

    if training_args.do_eval:
        logger.info("*** Evaluate ***")
        metrics = trainer.evaluate()
        if eval_dataset is not None and hasattr(eval_dataset, "__len__"):
            max_eval_samples = data_args.max_eval_samples or len(eval_dataset)
            metrics["eval_samples"] = min(max_eval_samples, len(eval_dataset))
        try:
            metrics["perplexity"] = math.exp(metrics["eval_loss"])
        except OverflowError:
            metrics["perplexity"] = float("inf")
        trainer.log_metrics("eval", metrics)
        trainer.save_metrics("eval", metrics)

    kwargs = {"tasks": "fill-mask"}
    if model_args.init_mode == "pretrained":
        kwargs["finetuned_from"] = model_args.model_name_or_path
    if data_args.dataset_name is not None:
        kwargs["dataset_tags"] = data_args.dataset_name
        kwargs["dataset"] = data_args.dataset_name
        if data_args.dataset_config_name is not None:
            kwargs["dataset_args"] = data_args.dataset_config_name
            kwargs["dataset"] = f"{data_args.dataset_name} {data_args.dataset_config_name}"

    if training_args.push_to_hub:
        trainer.push_to_hub(**kwargs)
    elif trainer.is_world_process_zero():
        trainer.create_model_card(**kwargs)


def parse_args(argv: Optional[Sequence[str]] = None):
    parser = HfArgumentParser((ModelArguments, DataTrainingArguments, TrainingArguments))
    if argv is None:
        argv = sys.argv[1:]
    argv = list(argv)
    if len(argv) == 1 and argv[0].endswith(".json"):
        return parser.parse_json_file(json_file=os.path.abspath(argv[0]))
    return parser.parse_args_into_dataclasses(args=argv)


def cleanup_distributed() -> None:
    if torch.distributed.is_available() and torch.distributed.is_initialized():
        torch.distributed.destroy_process_group()


def main(argv: Optional[Sequence[str]] = None) -> None:
    model_args, data_args, training_args = parse_args(argv)
    try:
        run_pretrain(model_args, data_args, training_args)
    finally:
        cleanup_distributed()
