"""Stage-1 masked-language-model pretraining for mRNABERT."""

from __future__ import annotations

import inspect
import json
import logging
import math
import os
import sys
from dataclasses import asdict, dataclass, field
from itertools import chain
from pathlib import Path
from typing import Optional, Sequence

import datasets
import numpy as np
import torch
import transformers
from datasets import load_dataset
from transformers import (
    DataCollatorForLanguageModeling,
    HfArgumentParser,
    Trainer,
    TrainerCallback,
    TrainingArguments as HFTrainingArguments,
    is_torch_tpu_available,
    set_seed,
)
from transformers.trainer_utils import get_last_checkpoint
from transformers.utils.versions import require_version

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
    model_name_or_path: str = field(default="YYLY66/mRNABERT")
    model_type: Optional[str] = field(default=None, metadata={"help": "Accepted for run_mlm.py compatibility."})
    config_overrides: Optional[str] = field(
        default=None,
        metadata={"help": "Accepted for run_mlm.py compatibility; not used when loading YYLY66/mRNABERT."},
    )
    config_name: Optional[str] = field(default=None)
    tokenizer_name: Optional[str] = field(default=None)
    cache_dir: Optional[str] = field(default=None)
    use_fast_tokenizer: bool = field(default=True)
    model_revision: str = field(default="main")
    use_auth_token: bool = field(default=False)
    low_cpu_mem_usage: bool = field(default=False)
    attention_backend: str = field(
        default="remote-safe",
        metadata={
            "help": "remote-safe uses the remote mRNABERT architecture with PyTorch attention fallback; "
            "remote-triton opts into the legacy remote Triton kernel."
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

    def __post_init__(self) -> None:
        if self.streaming:
            require_version("datasets>=2.0.0", "The streaming feature requires datasets>=2.0.0")
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


def build_trainer(
    model_args: ModelArguments,
    data_args: DataTrainingArguments,
    training_args: TrainingArguments,
) -> tuple[Trainer, object, object, object]:
    raw_datasets = load_raw_datasets(data_args, model_args, do_eval=training_args.do_eval)
    runtime = model_args.runtime_config(model_max_length=data_args.max_seq_length or training_args.model_max_length)
    bundle = load_mlm_model_and_tokenizer(runtime)
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
    if training_args.do_eval and not is_torch_tpu_available():
        compute_metrics, preprocess_logits_for_metrics = build_metrics()

    trainer = Trainer(
        model=bundle.model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        tokenizer=bundle.tokenizer,
        data_collator=data_collator,
        compute_metrics=compute_metrics,
        preprocess_logits_for_metrics=preprocess_logits_for_metrics,
        callbacks=[TrainingSummaryCallback()],
    )
    return trainer, train_dataset, eval_dataset, bundle.tokenizer


def run_pretrain(model_args: ModelArguments, data_args: DataTrainingArguments, training_args: TrainingArguments) -> None:
    setup_logging(training_args)
    configure_torch_runtime(training_args)
    last_checkpoint = detect_last_checkpoint(training_args)
    set_seed(training_args.seed)

    trainer, train_dataset, eval_dataset, tokenizer = build_trainer(model_args, data_args, training_args)
    if trainer.is_world_process_zero():
        write_run_manifest(training_args.output_dir, model_args, data_args, training_args, tokenizer)

    if training_args.do_train:
        checkpoint = training_args.resume_from_checkpoint or last_checkpoint
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

    kwargs = {"finetuned_from": model_args.model_name_or_path, "tasks": "fill-mask"}
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
