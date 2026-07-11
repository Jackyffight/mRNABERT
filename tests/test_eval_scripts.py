from pathlib import Path
import unittest


class EvalScriptTest(unittest.TestCase):
    def test_eval_one_checkpoint_does_not_create_train_dataset(self):
        script = Path("scripts/eval_one_checkpoint_nas.sh").read_text(encoding="utf-8")

        self.assertIn("--do_eval", script)
        self.assertIn("--validation_file", script)
        self.assertNotIn("--train_file", script)
        self.assertNotIn("torch.distributed.run", script)
        self.assertNotIn("--ddp_backend", script)

    def test_public_baseline_is_pinned_and_loss_only(self):
        script = Path("scripts/eval_hf_baseline_nas.sh").read_text(encoding="utf-8")

        self.assertIn("a1eb7df25804d23f08646e1cb996b234d7208a40", script)
        self.assertIn("--attention_backend remote-safe", script)
        self.assertIn("--prediction_loss_only true", script)
        self.assertNotIn("--do_train", script)

    def test_download_script_verifies_public_assets(self):
        script = Path("scripts/download_baseline_assets_nas.sh").read_text(encoding="utf-8")

        self.assertIn("455973118", script)
        self.assertIn("cb2eb64831a494d4cac14acb5df908f734e088c4d62256ac3e42cada60c3bf75", script)
        self.assertIn("3652178c257341010800e2d241a9c258", script)
        self.assertIn("939b495793687db362d4b9464a5df570", script)
        self.assertIn("mktemp", script)
        self.assertIn("snapshot_download", script)
        self.assertNotIn("--continue-at", script)

    def test_mrfp_suite_includes_random_init_control(self):
        script = Path("scripts/run_mrfp_baseline_nas.sh").read_text(encoding="utf-8")

        self.assertIn('run_model "internal-checkpoint-$STEP"', script)
        self.assertIn('run_model "public-YYLY66-$MODEL_REVISION"', script)
        self.assertIn('run_model "random-init-internal-architecture" "$INTERNAL_MODEL" scratch', script)
        self.assertIn('--freeze_encoder "$FREEZE_ENCODER"', script)
        self.assertIn('--learning_rate "$LEARNING_RATE"', script)

    def test_mrfp_sweeps_use_equal_learned_model_budget(self):
        lr_sweep = Path("scripts/run_mrfp_lr_sweep_nas.sh").read_text(encoding="utf-8")
        frozen = Path("scripts/run_mrfp_frozen_probe_nas.sh").read_text(encoding="utf-8")

        self.assertIn("2e-5 5e-5", lr_sweep)
        self.assertIn("full", lr_sweep)
        self.assertIn("learned", lr_sweep)
        self.assertIn("1e-4 3e-4 1e-3", frozen)
        self.assertIn("frozen", frozen)
        self.assertIn("learned", frozen)

    def test_frozen_probe_keeps_pooler_trainable(self):
        source = Path("regression.py").read_text(encoding="utf-8")

        self.assertIn("for parameter in encoder.parameters()", source)
        self.assertIn('pooler = getattr(encoder, "pooler", None)', source)
        self.assertIn("parameter.requires_grad = True", source)


if __name__ == "__main__":
    unittest.main()
