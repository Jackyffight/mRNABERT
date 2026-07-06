import argparse
import os
import sys
import time
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mrnabert.sequence_codec import (  # noqa: E402
    discover_fasta_files,
    encode_record,
    iter_all_fasta_records,
)


def format_duration(seconds):
    if seconds is None:
        return "unknown"
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{seconds:02d}s"
    return f"{minutes}m{seconds:02d}s"


def format_bytes(num_bytes):
    value = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} TiB"


def log_progress(
    *,
    path,
    file_index,
    total_files,
    file_sequences,
    total_sequences,
    file_bytes_read,
    file_size,
    processed_bytes,
    total_bytes,
    started_at,
):
    elapsed = time.time() - started_at
    rate = processed_bytes / elapsed if elapsed > 0 else 0
    eta = (total_bytes - processed_bytes) / rate if rate > 0 and total_bytes else None
    total_pct = processed_bytes / total_bytes * 100 if total_bytes else 0
    file_pct = file_bytes_read / file_size * 100 if file_size else 0

    print(
        "progress "
        f"file={file_index}/{total_files} "
        f"path={path} "
        f"file_bytes={format_bytes(file_bytes_read)}/{format_bytes(file_size)} "
        f"file_pct={file_pct:.2f}% "
        f"total_bytes={format_bytes(processed_bytes)}/{format_bytes(total_bytes)} "
        f"total_pct={total_pct:.2f}% "
        f"seq_file={file_sequences} "
        f"seq_total={total_sequences} "
        f"speed={format_bytes(rate)}/s "
        f"rate={format_bytes(rate)}/s "
        f"elapsed={format_duration(elapsed)} "
        f"eta={format_duration(eta)}",
        flush=True,
    )


def default_workers():
    cpu_count = os.cpu_count() or 1
    return max(1, min(cpu_count, 32))


def process_records(input_files, workers, chunksize, unordered):
    records = iter_all_fasta_records(input_files)
    if workers == 1:
        for record in records:
            yield encode_record(record)
        return

    with Pool(processes=workers) as pool:
        mapper = pool.imap_unordered if unordered else pool.imap
        yield from mapper(encode_record, records, chunksize=chunksize)


def process_files(raw_dir, input_list, output_dir, output_name, progress_interval, workers, chunksize, unordered):
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / output_name
    input_files = discover_fasta_files(raw_dir, input_list)
    if not input_files:
        raise FileNotFoundError(f"No FASTA files found under {raw_dir}")

    input_sizes = [path.stat().st_size for path in input_files]
    total_bytes = sum(input_sizes)

    print(f"Found {len(input_files)} FASTA files, total input {format_bytes(total_bytes)}", flush=True)
    order_mode = "unordered" if unordered else "ordered"
    print(f"Using workers={workers}, chunksize={chunksize}, mode={order_mode}", flush=True)

    total_sequences = 0
    file_sequences = [0 for _ in input_files]
    file_progress_bytes = [0 for _ in input_files]
    started_at = time.time()
    last_progress_at = 0.0
    last_file_index = None

    with output_path.open("w") as output:
        for line, file_index, file_bytes_read in process_records(input_files, workers, chunksize, unordered):
            output.write(line + "\n")
            file_offset = file_index - 1
            path = input_files[file_offset]
            file_size = input_sizes[file_offset]

            if last_file_index != file_index:
                print(f"Processing {file_index}/{len(input_files)} {path} ({format_bytes(file_size)})", flush=True)
                last_file_index = file_index

            file_sequences[file_offset] += 1
            total_sequences += 1
            file_progress_bytes[file_offset] = max(file_progress_bytes[file_offset], file_bytes_read)

            now = time.time()
            if now - last_progress_at >= progress_interval:
                log_progress(
                    path=path,
                    file_index=file_index,
                    total_files=len(input_files),
                    file_sequences=file_sequences[file_offset],
                    total_sequences=total_sequences,
                    file_bytes_read=file_progress_bytes[file_offset],
                    file_size=file_size,
                    processed_bytes=sum(file_progress_bytes),
                    total_bytes=total_bytes,
                    started_at=started_at,
                )
                last_progress_at = now

    if last_file_index is not None:
        file_offset = last_file_index - 1
        log_progress(
            path=input_files[file_offset],
            file_index=last_file_index,
            total_files=len(input_files),
            file_sequences=file_sequences[file_offset],
            total_sequences=total_sequences,
            file_bytes_read=input_sizes[file_offset],
            file_size=input_sizes[file_offset],
            processed_bytes=total_bytes,
            total_bytes=total_bytes,
            started_at=started_at,
        )

    print(f"Processed {total_sequences} sequences -> {output_path}")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Stream FASTA files into mRNABERT pretraining text format.")
    parser.add_argument("--raw-dir", "--raw_dir", type=Path, default=Path("raw"))
    parser.add_argument("--input-list", "--input_list", default=None)
    parser.add_argument("--output-dir", "--output_dir", type=Path, required=True)
    parser.add_argument("--output-name", "--output_name", default="pre.txt")
    parser.add_argument("--progress-interval", "--progress_interval", type=float, default=60.0)
    parser.add_argument("--workers", type=int, default=default_workers())
    parser.add_argument("--chunksize", type=int, default=32)
    parser.add_argument("--unordered", action="store_true")
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be >= 1")
    if args.chunksize < 1:
        parser.error("--chunksize must be >= 1")
    if args.progress_interval <= 0:
        parser.error("--progress-interval must be > 0")
    return args


def main(argv=None):
    args = parse_args(argv)
    process_files(
        args.raw_dir,
        args.input_list,
        args.output_dir,
        args.output_name,
        args.progress_interval,
        args.workers,
        args.chunksize,
        args.unordered,
    )


if __name__ == "__main__":
    main()
