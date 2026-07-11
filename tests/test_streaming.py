import tempfile
import unittest
from pathlib import Path

from mrnabert import streaming


def _write_files(dirpath, files):
    """files: {name: [line, ...]} -> list of file paths (written with trailing \\n)."""
    paths = []
    for name, lines in files.items():
        path = Path(dirpath) / name
        path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        paths.append(str(path))
    return paths


def _collect_all_partitions(reader, files, world_size, num_workers):
    """Concatenate every (rank, worker) partition's output for the given reader."""
    collected = []
    for rank in range(world_size):
        for worker in range(num_workers):
            collected.extend(
                streaming.iter_reader_lines(reader, files, rank, world_size, worker, num_workers)
            )
    return collected


class ShardCoverageTest(unittest.TestCase):
    """Across all partitions a reader must cover every non-empty line exactly once."""

    GRID = [(1, 1), (1, 4), (4, 1), (3, 2), (4, 3), (2, 3)]

    def _corpus(self):
        # Unique lines so duplication changes the sorted multiset; blank/whitespace
        # lines interspersed to confirm they are skipped consistently.
        files = {
            "a.txt": [f"a_seq_{i:03d}" for i in range(17)],
            "b.txt": [f"b_seq_{i:03d}" for i in range(23)],
            "c.txt": [f"c_seq_{i:03d}" for i in range(11)],
        }
        files["a.txt"].insert(5, "")
        files["a.txt"].insert(9, "   ")
        files["b.txt"].insert(0, "")
        expected = sorted(
            line for lines in files.values() for line in lines if line.strip()
        )
        return files, expected

    def test_all_readers_cover_corpus_once(self):
        files, expected = self._corpus()
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_files(tmp, files)
            for reader in streaming.LOCAL_STREAMING_READERS:
                for world_size, num_workers in self.GRID:
                    got = _collect_all_partitions(reader, paths, world_size, num_workers)
                    self.assertEqual(
                        sorted(got),
                        expected,
                        msg=f"reader={reader} world_size={world_size} num_workers={num_workers}",
                    )

    def test_byte_range_covers_lines_aligned_to_boundaries(self):
        # Equal-length lines + a partition count dividing the line count places every
        # partition boundary exactly on a line start; the old unconditional
        # "discard partial line" dropped one line per boundary. This is the regression.
        n = 24
        lines = [f"row{i:04d}" for i in range(n)]  # each identical width -> uniform byte stride
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_files(tmp, {"eq.txt": lines})
            for divisor in (1, 2, 3, 4, 6, 8, 12, 24):
                got = _collect_all_partitions("byte-range", paths, divisor, 1)
                self.assertEqual(sorted(got), sorted(lines), msg=f"num_partitions={divisor}")

    def test_partitions_do_not_overlap(self):
        # Stronger than coverage: no line is emitted by two partitions.
        files, expected = self._corpus()
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_files(tmp, files)
            for reader in streaming.LOCAL_STREAMING_READERS:
                got = _collect_all_partitions(reader, paths, 4, 3)
                self.assertEqual(len(got), len(expected), msg=f"reader={reader} produced duplicates")

    def test_file_shard_fast_seek_starts_near_cursor_without_prefix_replay(self):
        # Fixed-width rows make the byte fraction an exact line boundary. Across
        # all workers, fast seek must cover the tail once without reading the
        # prefix that the checkpoint cursor has already consumed.
        lines = [f"row{i:04d}" for i in range(40)]
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_files(tmp, {"shard.txt": lines})
            got = []
            for worker in range(4):
                got.extend(
                    streaming.iter_reader_lines(
                        "file-shard",
                        paths,
                        rank=0,
                        world_size=1,
                        worker_id=worker,
                        num_workers=4,
                        start_fraction=0.5,
                    )
                )

        self.assertEqual(sorted(got), sorted(lines[20:]))
        self.assertFalse(set(got) & set(lines[:20]))


class PartitionMathTest(unittest.TestCase):
    def test_partition_id_and_count(self):
        # rank 2, worker 1, world 4, workers 3 -> id = 2*3+1 = 7, count = 12
        self.assertEqual(streaming.partition_id_and_count(2, 4, 1, 3), (7, 12))
        self.assertEqual(streaming.partition_id_and_count(0, 1, 0, 1), (0, 1))

    def test_per_partition_cap_is_global_ceil_divide(self):
        self.assertIsNone(streaming.per_partition_cap(None, 4))
        # 10 samples over 4 partitions -> 3 each (ceil), global total in [10, 12]
        self.assertEqual(streaming.per_partition_cap(10, 4), 3)
        cap = streaming.per_partition_cap(10, 4)
        self.assertGreaterEqual(cap * 4, 10)          # never silently short
        self.assertLess((cap - 1) * 4, 10)            # and it is the ceiling, not more
        self.assertEqual(streaming.per_partition_cap(8, 4), 2)
        self.assertEqual(streaming.per_partition_cap(1, 8), 1)

    def test_partition_skip_distributes_global_budget_exactly(self):
        self.assertEqual([streaming.partition_skip(10, i, 4) for i in range(4)], [3, 3, 2, 2])
        self.assertEqual(sum(streaming.partition_skip(10, i, 4) for i in range(4)), 10)
        self.assertEqual([streaming.partition_skip(8, i, 4) for i in range(4)], [2, 2, 2, 2])
        self.assertEqual([streaming.partition_skip(1, i, 4) for i in range(4)], [1, 0, 0, 0])
        self.assertEqual([streaming.partition_skip(0, i, 4) for i in range(4)], [0, 0, 0, 0])


class BoundedShuffleTest(unittest.TestCase):
    def test_passthrough_when_buffer_disabled(self):
        data = [str(i) for i in range(20)]
        self.assertEqual(list(streaming.iter_bounded_shuffle(iter(data), 0, 42)), data)
        self.assertEqual(list(streaming.iter_bounded_shuffle(iter(data), 1, 42)), data)

    def test_is_a_permutation_and_deterministic(self):
        data = [str(i) for i in range(200)]
        out1 = list(streaming.iter_bounded_shuffle(iter(data), 16, 7))
        out2 = list(streaming.iter_bounded_shuffle(iter(data), 16, 7))
        self.assertEqual(sorted(out1, key=int), sorted(data, key=int))  # no drop/dup
        self.assertEqual(out1, out2)                                     # deterministic per seed
        self.assertNotEqual(out1, data)                                  # actually reordered

    def test_resume_skip_happens_after_shuffle(self):
        data = [str(i) for i in range(200)]
        full = list(streaming.iter_bounded_shuffle(iter(data), 16, 7))
        skipped = []
        skip = 37
        for line in streaming.iter_bounded_shuffle(iter(data), 16, 7):
            if skip > 0:
                skip -= 1
                continue
            skipped.append(line)
        self.assertEqual(skipped, full[37:])


class ValidateReaderPartitionsTest(unittest.TestCase):
    def test_file_shard_fewer_files_than_ranks_raises(self):
        with self.assertRaises(ValueError):
            streaming.validate_reader_partitions("file-shard", num_files=2, world_size=4)

    def test_file_shard_enough_files_ok(self):
        streaming.validate_reader_partitions("file-shard", num_files=4, world_size=4)
        streaming.validate_reader_partitions("file-shard", num_files=8, world_size=4)

    def test_single_process_never_raises(self):
        streaming.validate_reader_partitions("file-shard", num_files=1, world_size=1)

    def test_other_readers_never_raise(self):
        streaming.validate_reader_partitions("line-stride", num_files=1, world_size=8)
        streaming.validate_reader_partitions("byte-range", num_files=1, world_size=8)


if __name__ == "__main__":
    unittest.main()
