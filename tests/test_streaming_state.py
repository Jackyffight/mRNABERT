import json
import tempfile
import unittest
from pathlib import Path

from mrnabert import streaming_state


class StreamingStateMathTest(unittest.TestCase):
    def test_corrected_legacy_cursor_advances_from_resume_step(self):
        cursor = streaming_state.next_sample_cursor(
            resume_sample_cursor=6_240_000,
            resume_global_step=300_000,
            current_global_step=400_000,
            batch_size=96,
        )
        self.assertEqual(cursor, 15_840_000)

    def test_effective_batch_size(self):
        self.assertEqual(streaming_state.effective_batch_size(32, 1, 3), 96)
        self.assertEqual(streaming_state.effective_batch_size(16, 2, 3), 96)


class StreamingStatePersistenceTest(unittest.TestCase):
    def _checkpoint(self, root: Path, step: int) -> Path:
        checkpoint = root / f"checkpoint-{step}"
        checkpoint.mkdir()
        (checkpoint / "trainer_state.json").write_text(
            json.dumps({"global_step": step}), encoding="utf-8"
        )
        return checkpoint

    def test_checkpoint_state_wins_over_global_step_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            checkpoint = self._checkpoint(root, 400_000)
            state = streaming_state.build_checkpoint_state(
                global_step=400_000,
                resume_global_step=300_000,
                resume_sample_cursor=6_240_000,
                effective_batch=96,
                streaming_reader="file-shard",
                shuffle_seed=42,
            )
            streaming_state.write_checkpoint_state(checkpoint, state)

            resolved = streaming_state.resolve_resume_state(
                checkpoint=checkpoint,
                fallback_effective_batch=96,
            )

            self.assertEqual(resolved.next_sample_cursor, 15_840_000)
            self.assertEqual(resolved.source, "checkpoint-streaming-state")

    def test_legacy_checkpoint_falls_back_to_global_step(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint = self._checkpoint(Path(tmpdir), 600_000)
            resolved = streaming_state.resolve_resume_state(
                checkpoint=checkpoint,
                fallback_effective_batch=96,
            )
            self.assertEqual(resolved.next_sample_cursor, 57_600_000)
            self.assertEqual(resolved.source, "legacy-global-step-fallback")

    def test_manifest_identity_is_checked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            checkpoint = self._checkpoint(root, 10)
            old_manifest = root / "old.json"
            new_manifest = root / "new.json"
            old_manifest.write_text('{"total_lines": 1000, "seed": 42}', encoding="utf-8")
            new_manifest.write_text('{"total_lines": 1000, "seed": 43}', encoding="utf-8")
            state = streaming_state.build_checkpoint_state(
                global_step=10,
                resume_global_step=0,
                resume_sample_cursor=0,
                effective_batch=4,
                streaming_reader="file-shard",
                shuffle_seed=42,
                shard_manifest_path=str(old_manifest),
            )
            streaming_state.write_checkpoint_state(checkpoint, state)

            with self.assertRaisesRegex(ValueError, "manifest changed"):
                streaming_state.resolve_resume_state(
                    checkpoint=checkpoint,
                    fallback_effective_batch=4,
                    current_shard_manifest_path=str(new_manifest),
                )

    def test_manifest_identity_ignores_sharding_timing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            first = root / "first.json"
            second = root / "second.json"
            first.write_text(
                '{"total_lines": 1000, "seed": 42, "elapsed_seconds": 10.0}',
                encoding="utf-8",
            )
            second.write_text(
                '{"total_lines": 1000, "seed": 42, "elapsed_seconds": 20.0}',
                encoding="utf-8",
            )
            self.assertEqual(
                streaming_state.shard_manifest_sha256(first),
                streaming_state.shard_manifest_sha256(second),
            )

    def test_streaming_topology_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            checkpoint = self._checkpoint(root, 10)
            state = streaming_state.build_checkpoint_state(
                global_step=10,
                resume_global_step=0,
                resume_sample_cursor=0,
                effective_batch=96,
                streaming_reader="file-shard",
                shuffle_buffer=20_000,
                shuffle_seed=42,
                world_size=3,
                dataloader_num_workers=4,
            )
            streaming_state.write_checkpoint_state(checkpoint, state)

            with self.assertRaisesRegex(ValueError, "topology changed"):
                streaming_state.resolve_resume_state(
                    checkpoint=checkpoint,
                    fallback_effective_batch=96,
                    current_streaming_reader="file-shard",
                    current_shuffle_buffer=20_000,
                    current_shuffle_seed=42,
                    current_world_size=3,
                    current_dataloader_num_workers=0,
                )

    def test_state_tracks_corpus_pass_and_offset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = Path(tmpdir) / "manifest.json"
            manifest.write_text('{"total_lines": 100}', encoding="utf-8")
            state = streaming_state.build_checkpoint_state(
                global_step=30,
                resume_global_step=0,
                resume_sample_cursor=0,
                effective_batch=4,
                streaming_reader="file-shard",
                shuffle_seed=42,
                shard_manifest_path=str(manifest),
            )
            self.assertEqual(state.next_sample_cursor, 120)
            self.assertEqual(state.corpus_pass, 1)
            self.assertEqual(state.corpus_offset, 20)

    def test_state_records_fast_seek_resume_mode(self):
        state = streaming_state.build_checkpoint_state(
            global_step=10,
            resume_global_step=5,
            resume_sample_cursor=20,
            effective_batch=4,
            streaming_reader="file-shard",
            shuffle_seed=42,
            resume_mode="fast-seek",
        )
        self.assertEqual(state.next_sample_cursor, 40)
        self.assertEqual(state.resume_mode, "fast-seek")


if __name__ == "__main__":
    unittest.main()
