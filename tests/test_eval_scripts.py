from pathlib import Path
import unittest


class EvalScriptTest(unittest.TestCase):
    def test_eval_one_checkpoint_does_not_create_train_dataset(self):
        script = Path("scripts/eval_one_checkpoint_nas.sh").read_text(encoding="utf-8")

        self.assertIn("--do_eval", script)
        self.assertIn("--validation_file", script)
        self.assertNotIn("--train_file", script)
        self.assertNotIn("CUDA_VISIBLE_DEVICES", script)


if __name__ == "__main__":
    unittest.main()
