# mRNABERT

This repository includes the official implementation of [mRNABERT: advancing mRNA sequence design with a universal language model and comprehensive dataset](https://www.nature.com/articles/s41467-025-65340-8). We provide pre-trained model, examples of pre-training and fine-tuning, and pre-trained datasets.

## 📢 Update

**We have released all downstream datasets on Zenodo** 

You can access and download the datasets [here](https://zenodo.org/records/17786045).

To reproduce our results, please follow the instructions in the **[Fine-tune with pre-trained model](#fine-tuning)** section.

> ⚠️ **Note on Hyperparameters:**
> When fine-tuning, please make sure to adjust parameters such as `model_max_length`, `batch_size`, `epoch`, and `eval_steps` according to the specific dataset you are using.

## Contents

- [Introduction](#introduction)
- [Create Environment with Conda](#create-environment-with-conda)
- [Pre-trained Model and Datasets](#pre-trained-model-and-datasets)
- [Pre-Training](#pre-training)
- [Fine-tuning](#fine-tuning)
- [Reports](#reports)
- [Citation](#citation)
- [Contact](#contact)

## Introduction

mRNABERT is a robust language model pre-trained on over 18 million high-quality mRNA sequences, incorporating contrastive learning to integrate the semantic features of amino acids.

![mRNABERT](figures/mRNABERT.png)

## Create Environment with Conda

    # create and activate a virtual python environment
    conda create -n mrnabert python=3.8
    conda activate mrnabert
    
    # install required packages. PyTorch is intentionally not pinned here;
    # use the CUDA/PyTorch runtime provided by your training machine.
    pip install -r requirements.txt

Furthermore, to streamline the setup process, we have prepared a pre-configured Conda environment containing all mRNABERT dependencies at [Zenodo](https://zenodo.org/records/15051237). You can easily download and extract it into your Conda environments directory, and it will be ready to use immediately.

    mkdir -C /path/to//miniconda3/envs/mrnabert
    tar -xzvf /path/to/mrnabert.tar.gz -C /path/to/miniconda3/envs/mrnabert
    conda activate mrnabert


## Pre-trained Model and Datasets

The pre-trained model is available at [Huggingface](https://huggingface.co/YYLY66/mRNABERT) as `YYLY66/mRNABERT`. 

The mRNA datasets are available on [Zenodo](https://zenodo.org/records/12516160), featuring more than 36 million comprehensive mRNA or CDS sequences from various species.



**Notably, the data needs to be preprocessed.** We use [ORFfinder from NCBI](https://www.ncbi.nlm.nih.gov/orffinder) to predict the CDS regions of the mRNA. Then, please preprocess the data in different ways: use single-letter separation for the UTR regions and three-character separation for the CDS regions. We have provided custom functions and sample data before preprocessing in `data_process`.


### Access Pre-trained Models
You can download the pre-trained models from [Huggingface](https://huggingface.co/YYLY66/mRNABERT), or load the model directly：

```python
import torch
from transformers import AutoTokenizer, AutoModel
from transformers.models.bert.configuration_bert import BertConfig

config = BertConfig.from_pretrained("YYLY66/mRNABERT")
tokenizer = AutoTokenizer.from_pretrained("YYLY66/mRNABERT")
model = AutoModel.from_pretrained("YYLY66/mRNABERT", trust_remote_code=True, config=config)
```

Extract the embeddings of mRNA sequences:

```python
seq = ["A T C G G A GGG CCC TTT", 
       "A T C G", 
       "TTT CCC GAC ATG"]  #Separate the sequences with spaces.

encoding = tokenizer.batch_encode_plus(seq, add_special_tokens=True, padding='longest', return_tensors="pt")

input_ids = encoding['input_ids']
attention_mask = encoding['attention_mask'] 

output = model(input_ids=input_ids, attention_mask=attention_mask)
last_hidden_state = output[0]

attention_mask = attention_mask.unsqueeze(-1).expand_as(last_hidden_state)  # Shape : [batch_size, seq_length, hidden_size]

# Sum embeddings along the batch dimension
sum_embeddings = torch.sum(last_hidden_state * attention_mask, dim=1)  

# Also sum the masks along the batch dimension
sum_masks = attention_mask.sum(1)  

# Compute mean embedding.
mean_embedding = sum_embeddings / sum_masks  #Shape:[batch_size, hidden_size]  

```

The extracted embeddings can be used for contrastive learning pretraining or as a feature extractor for protein-related downstream tasks.



## Pre-Training
### Data processing
Please see the template data at `/sample_data/pre.txt`, you should process your data into the same format as it. The maintained preprocessing path is streaming and can process a directory of FASTA files:

```
python main.py preprocess \
  --raw-dir raw \
  --output-dir data/pretrain \
  --workers 32 \
  --chunksize 256 \
  --progress-interval 30
```

This writes `data/pretrain/pre.txt` by default. It keeps the original repository heuristic: UTR is split into single bases, and the longest in-frame CDS is split into codons.

The original single-file command is still available:

for example:
```
python data_process/process_pretrain_data.py --input_file "data_process/pre-train/pre_input.fasta" --output_file "sample_data/pre.txt"  
```
### Pretraining stage 1
```
python main.py pretrain \
  --output_dir=output/pre/mRNABERT- \
  --model_name_or_path=assets/mrnabert-base \
  --init_mode=scratch \
  --do_train \
  --learning_rate=5e-5 \
  --num_train_epochs=10 \
  --gradient_accumulation_steps=4 \
  --train_file=/sample_data/pre.txt \
  --fp16 \
  --save_steps=1000 \
  --logging_steps=500 \
  --eval_steps=500 \
  --warmup_steps=2000 \
  --mlm_probability=0.15 \
  --line_by_line \
  --per_device_train_batch_size=32

```

`run_mlm.py` is kept as a compatibility wrapper for older commands. New code should call `python main.py pretrain`.

The training entrypoint is split into deeper modules:

- `mrnabert.sequence_codec`: FASTA parsing, ORF detection, and mRNABERT token spacing.
- `mrnabert.modeling`: model/tokenizer loading and attention backend selection.
- `mrnabert.pretrain`: dataset loading, tokenization, Trainer construction, manifests, and metrics.

By default, pretraining is a from-scratch run using the local
`assets/mrnabert-base` config and vocabulary. It does not load
`YYLY66/mRNABERT` checkpoint weights. Use pretrained mode only for explicit
baseline or continuation runs:

```
python main.py pretrain \
  --model_name_or_path YYLY66/mRNABERT \
  --init_mode pretrained \
  --train_file sample_data/pre.txt \
  --do_train \
  --attention_backend remote-safe
```

### Neptune/Merlin launcher
For Neptune/Merlin workers, use `run_train.sh` so environment checks, run
workspace creation, GPU detection, and the `python main.py pretrain` command are
handled in one place. The launcher intentionally uses `python` directly, matching
the default cluster entrypoint style used by `neptune_chat`.

Direct `python` launch defaults to the first visible GPU, avoiding implicit
PyTorch `DataParallel`. Use `--launcher torchrun --devices 0,1,2` for single-node
DDP on three GPUs.

Large training files do not live in GPU memory. GPU memory is mainly determined
by model size, sequence length, batch size, precision, activations, gradients,
and optimizer state. The large-file risk is disk cache: HuggingFace datasets
materializes Arrow/tokenized cache. The launcher therefore defaults
`--dataset-cache-dir` to `<output-root>/cache/datasets`, which is intended to be
an HDFS-mounted training path instead of `~/.cache/huggingface`. Exploratory
runs with `--max-steps` default to `--streaming`, which skips Arrow/tokenized
cache creation. For cached preprocessing, use `--no-streaming` and explicitly
choose `--preprocessing-workers`. `--hf-cache-dir` is only needed for explicit
`--init-mode pretrained` baseline runs.

Smoke test:
```
./run_train.sh \
  --env devbox \
  --smoke \
  --train-file /mnt/hdfs/byte_neptune_ai/mrna/pre.txt
```

Full stage-1 pretraining:
```
./run_train.sh \
  --env devbox \
  --train-file /mnt/hdfs/byte_neptune_ai/mrna/pre.txt \
  --launcher torchrun \
  --devices 0,1,2 \
  --batch-size 32 \
  --grad-accum 4
```

Short exploratory run on the full file:
```
./run_train.sh \
  --env devbox \
  --train-file /mnt/hdfs/byte_neptune_ai/mrna/pre.txt \
  --launcher torchrun \
  --devices 0,1,2 \
  --max-steps 1000 \
  --batch-size 16 \
  --grad-accum 2
```

This uses streaming by default because `--max-steps` is set. For `torchrun` on a
single large local text file, the launcher also defaults to `--auto-shard`: it
streams through `pre.txt` once, randomly writes one shard per process, logs
`shard_progress` with bytes, lines, rate, elapsed time, and ETA, then trains with
the `file-shard` reader. Shards are cached under
`<output-root>/data_shards/<file>-<n>shards-seed<seed>/` and reused when the
manifest still matches the source file. Use `--reshard` to force a rebuild,
`--no-auto-shard` to disable this path, or `--shard-count`, `--shard-seed`, and
`--shard-dir` to control it.

For streaming DDP, the launcher also sets `--dispatch_batches false` so each
process reads its assigned shard directly. This avoids the default IterableDataset
path where the main process becomes the only data reader and can bottleneck or
stall the whole job.

The `file-shard` path also enables a bounded per-rank shuffle buffer by default
(`--streaming-shuffle-buffer 20000`). This keeps training streaming-friendly
while avoiding long ordered stretches from the original FASTA/pre.txt layout.
Use `--streaming-shuffle-buffer 0` to disable it, or increase the value if host
memory and storage throughput are comfortable.

When resuming a streaming run with `--resume`, the launcher sets
`--ignore_data_skip true`. Optimizer, scheduler, model weights, and global step
are still restored, but the trainer does not try to replay tens of thousands of
streaming batches just to recover an exact data cursor. This is the practical
default for large streaming pretraining runs. The launcher also sets
`TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1` for resume runs so PyTorch 2.6+ can load the
old Transformers RNG state files produced by trusted local checkpoints.

The older streaming readers are still available for debugging: `line-stride`
sequentially scans the source file in every rank, `byte-range` uses seek-based
sharding and should only be used on filesystems with fast random seek, and `hf`
falls back to HuggingFace datasets streaming.

The default output workspace is:
```
/mnt/hdfs/byte_neptune_ai/mrna/train/runs/mrnabert-<mode>-<env>-<timestamp>/
```

By default the launcher initializes a standard BERT masked-LM model from
`assets/mrnabert-base`. To compare against the public mRNABERT checkpoint, run
an explicit pretrained baseline:
```
./run_train.sh \
  --env devbox \
  --train-file /mnt/hdfs/byte_neptune_ai/mrna/pre.txt \
  --init-mode pretrained \
  --model YYLY66/mRNABERT
```

Use `./run_train.sh --help` for all launcher options. Unknown arguments are
passed through to `python main.py pretrain`.

### Pretraining stage 2
We used the [OpenAI-CLIP](https://github.com/moein-shariatnia/OpenAI-CLIP) for contrastive learning.You can modify the code using the embedding extraction method mentioned above and reproduce the model training.


## Fine-tuning
### Data processing
Please see the template data at `/sample_data/fine-tune/mRFP` and generate `3 csv files` from your dataset into the same format as it. Each file needs to have two columns with the header row labeled as `sequence` and `label`. Please use `process_finetune_data` for split.

for example:
```
python data_process/process_finetune_data.py  --input_dir "data_process/fine-tune/mRFP"  --output_dir "sample_data/fine-tune/mRFP" --split_option "codon"     
```
 You can specify different split option based on the types of data: `utr` for UTR sequences, `cds` for CDS sequences, and `complete` for complete mRNA sequences. NOTE,please use '[' and ']' to mark CDS if you choose `complete` option.

### Fine-tune with pre-trained model
Then, you are able to finetune mRNABERT with the following code:

```
#For regression tasks

export DATA_PATH=/sample_data/fine-tune/mRFP
python regression.py \
    --model_name_or_path=YYLY66/mRNABERT \
    --data_path ${DATA_PATH} \
    --run_name mRNABERT_${DATA_PATH} \
    --model_max_length 250 \  #set as the number of tokens
    --per_device_train_batch_size 16 \
    --per_device_eval_batch_size 8 \
    --gradient_accumulation_steps 1 \
    --learning_rate 5e-5 \
    --num_train_epochs 50 \
    --save_steps 10 \
    --output_dir output/${DATA_PATH} \
    --evaluation_strategy steps \
    --eval_steps 10 \
    --warmup_steps 10 \
    --logging_steps 10 \
    --overwrite_output_dir True \
    --log_level info \
    --find_unused_parameters False     
```
```
#For classification tasks

export DATA_PATH=$path/to/data/folder
python classification.py \
    --model_name_or_path=YYLY66/mRNABERT \
    --data_path ${DATA_PATH} \
    --run_name mRNABERT_${DATA_PATH} \
    --model_max_length 250 \  #set as the number of tokens
    --per_device_train_batch_size 16 \
    --per_device_eval_batch_size 8 \
    --gradient_accumulation_steps 1 \
    --learning_rate 5e-5 \
    --num_train_epochs 50 \
    --save_steps 10 \
    --output_dir output/${DATA_PATH} \
    --evaluation_strategy steps \
    --eval_steps 10 \
    --warmup_steps 10 \
    --logging_steps 10 \
    --overwrite_output_dir True \
    --log_level info \
    --find_unused_parameters False       
```
You need to choose different `batch sizes` and `epochs` based on the dataset to achieve optimal results. Incidentally, you can also use this code to test other benchmark models through HuggingFace.


## Reports

- [Protein-to-mRNA design pipeline report](docs/reports/protein-mrna-design-pipeline.md)


## Citation

If you find the models useful in your research, please cite our paper:

```
@article{xiong2025mrnabert,
  title={mRNABERT: advancing mRNA sequence design with a universal language model and comprehensive dataset},
  author={Xiong, Ying and Wang, Aowen and Kang, Yu and Shen, Chao and Hsieh, Chang-Yu and Hou, Tingjun},
  journal={Nature Communications},
  volume={16},
  number={1},
  pages={10371},
  year={2025},
  publisher={Nature Publishing Group UK London},
}
```

The model of this code builds on the [DNABERT-2](https://arxiv.org/abs/2306.15006) modeling framework. We use [transformers](https://github.com/huggingface/transformers/tree/main/examples/pytorch/language-modeling) and [OpenAI-CLIP](https://github.com/moein-shariatnia/OpenAI-CLIP) framework to train our mRNA language models and [MultiMolecule](https://github.com/DLS5-Omics/multimolecule) for testing and comparing various benchmark models. We really appreciate these excellent works!

## Contact
If you have any question, please feel free to email us (xiongying@zju.edu.cn).
