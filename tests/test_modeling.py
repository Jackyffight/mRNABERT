import importlib.util
import unittest
from unittest.mock import Mock, patch


HAS_TRANSFORMERS = importlib.util.find_spec("transformers") is not None

if HAS_TRANSFORMERS:
    from mrnabert import modeling


class _Embeddings:
    class _Weight:
        shape = (74, 768)

    weight = _Weight()


class _Model:
    def get_input_embeddings(self):
        return _Embeddings()

    def resize_token_embeddings(self, size):
        raise AssertionError(f"resize_token_embeddings should not be called, got {size}")


class _Tokenizer:
    def __len__(self):
        return 74


@unittest.skipUnless(HAS_TRANSFORMERS, "transformers is not installed")
class ModelingTest(unittest.TestCase):
    def test_config_uses_builtin_bert_class_while_model_uses_remote_code(self):
        config = Mock()
        config.attention_probs_dropout_prob = 0

        with patch.object(modeling.AutoConfig, "from_pretrained", return_value=config) as config_loader:
            with patch.object(modeling.AutoTokenizer, "from_pretrained", return_value=_Tokenizer()) as tokenizer_loader:
                with patch.object(modeling.AutoModelForMaskedLM, "from_pretrained", return_value=_Model()) as model_loader:
                    runtime = modeling.ModelRuntimeConfig(
                        model_name_or_path="YYLY66/mRNABERT",
                        init_mode="pretrained",
                        attention_backend="remote-safe",
                    )
                    bundle = modeling.load_mlm_model_and_tokenizer(runtime)

        self.assertIs(bundle.config, config)
        self.assertEqual(config.attention_probs_dropout_prob, 1e-12)
        self.assertFalse(config_loader.call_args.kwargs["trust_remote_code"])
        self.assertTrue(tokenizer_loader.call_args.kwargs["trust_remote_code"])
        self.assertTrue(model_loader.call_args.kwargs["trust_remote_code"])
        self.assertIs(model_loader.call_args.kwargs["config"], config)

    def test_scratch_initializes_from_config_without_remote_code_or_weights(self):
        config = Mock()
        config.attention_probs_dropout_prob = 0

        with patch.object(modeling.AutoConfig, "from_pretrained", return_value=config) as config_loader:
            with patch.object(modeling.AutoTokenizer, "from_pretrained", return_value=_Tokenizer()) as tokenizer_loader:
                with patch.object(modeling.AutoModelForMaskedLM, "from_pretrained") as pretrained_loader:
                    with patch.object(modeling.AutoModelForMaskedLM, "from_config", return_value=_Model()) as config_model_loader:
                        runtime = modeling.ModelRuntimeConfig(model_name_or_path="assets/mrnabert-base")
                        bundle = modeling.load_mlm_model_and_tokenizer(runtime)

        self.assertIs(bundle.config, config)
        self.assertEqual(config.attention_probs_dropout_prob, 0)
        self.assertFalse(config_loader.call_args.kwargs["trust_remote_code"])
        self.assertFalse(tokenizer_loader.call_args.kwargs["trust_remote_code"])
        self.assertEqual(config_model_loader.call_args.args[0], config)
        pretrained_loader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
