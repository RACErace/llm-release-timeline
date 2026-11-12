import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import collector
import sources
from model_scope import announcement_in_scope, base_training_evidence, check_new_record, hub_metadata_in_scope


HUB = {"id": "huggingface:org", "org": "Org", "owner": "org", "channel": "huggingface"}
MODEL = {
    "id": "org/Example", "pipeline_tag": "text-generation",
    "siblings": [{"rfilename": "model.safetensors"}],
}


class FoundationScopeTests(unittest.TestCase):
    def test_lineage_and_task_take_precedence_over_the_name_and_weight_files(self):
        for properties in [
            {"tags": ["base_model:finetune:Qwen/Qwen3.5"]},
            {"cardData": {"base_model": ["original/a", "original/b"]}},
            {"base_model_relation": "finetune"},
            {"pipeline_tag": "sentence-similarity"},
            {"pipeline_tag": "text-to-image"},
            {"siblings": MODEL["siblings"] + [{"rfilename": "adapter_config.json"}]},
            {"id": "org/Qwen-7B-Chat"}, {"id": "org/Qwen-7B-Instruct-AWQ"},
        ]:
            with self.subTest(properties=properties):
                self.assertIsNone(sources.hub_signal(HUB, dict(MODEL, **properties)))
        self.assertFalse(announcement_in_scope("Introducing the Multimodal Knowledge Agent benchmark"))
        self.assertTrue(announcement_in_scope("Introducing Claude Fable 5.2"))

    def test_card_must_identify_a_language_base_and_not_just_mention_one(self):
        for card in [
            "This multimodal decision model built on Qwen3.5 uses the base model.",
            "Our model is fine-tuned from a pretrained language model.",
            "Training Stage: Pretraining & Post-training",
            "See our benchmark of other models.", "",
        ]:
            self.assertFalse(base_training_evidence(MODEL, card))
        self.assertFalse(base_training_evidence(
            {"id": "org/Example"}, "A video diffusion model trained from scratch."
        ))
        quantized = dict(MODEL, config={"quantization_config": {"quant_method": "fp8"}})
        self.assertFalse(base_training_evidence(quantized, "We quantized the base language model."))
        self.assertTrue(base_training_evidence(
            quantized, "A language model trained from scratch. We use FP8 mixed precision training."
        ))
        self.assertTrue(base_training_evidence(MODEL, "A pretrained generative text model."))
        self.assertTrue(base_training_evidence(
            MODEL, "A causal decoder-only model trained on a causal language modeling task."
        ))
        self.assertTrue(hub_metadata_in_scope(MODEL))

    def test_paper_source_and_old_pending_cannot_create_an_issue(self):
        self.assertNotIn("arxiv-cl", {s["id"] for s in sources.registry()})
        old = sources.hub_signal(HUB, MODEL)
        paper = dict(old, id="v1", source="arxiv-cl", url="https://arxiv.org/abs/1234.50000")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            state = collector.state_at(path)
            state["pending"] = {
                "one": {"v1": paper, "v2": dict(paper, id="v2", arxiv={"version": 2})},
                "two": {old["id"]: old},
            }
            state["delivered"] = {"old-receipt": 12}
            collector.save(path, state)
            with (
                patch.object(collector, "lookup_hub_model", return_value=None) as lookup,
                patch.object(collector, "api") as github,
            ):
                self.assertFalse(collector.deliver(path, "org/repo", 10))
                self.assertFalse(collector.deliver(path, "org/repo", 10))
            lookup.assert_called_once_with("org/Example", "huggingface")
            github.assert_not_called()
            self.assertEqual(collector.state_at(path)["pending"], {})
            self.assertEqual(collector.state_at(path)["delivered"], {"old-receipt": 12})

    def test_old_hub_evidence_waits_for_verification_without_losing_other_groups(self):
        candidate = sources.hub_signal(HUB, MODEL)
        news = dict(candidate, source="gemini", name="Introducing Gemini 4", model="")
        state = {"pending": {"news": {"news": news}, "hub": {candidate["id"]: candidate}}}
        with patch.object(collector, "lookup_hub_model", side_effect=OSError("offline")):
            ready, waiting = collector.eligible_pending(state)
        self.assertTrue(waiting)
        self.assertEqual([key for key, _ in ready], ["news"])
        with patch.object(collector, "lookup_hub_model", return_value=dict(candidate, base_evidence="Trained from scratch.")):
            ready, waiting = collector.eligible_pending(state)
        self.assertFalse(waiting)
        self.assertEqual([key for key, _ in ready], ["news", "hub"])

    def test_manual_record_uses_publisher_evidence_and_allows_historical_corrections(self):
        article = {"name": "New Model", "note": "Original pretraining.", "url": "https://arxiv.org/abs/1234.50000"}
        with self.assertRaises(ValueError):
            check_new_record(article)
        check_new_record(article, historical_source=True)
        check_new_record(dict(article, url2="https://org.example/research/new-model"))
        for note in (
            "基于 Qwen3-VL 的医疗模型", "Qwen3.5 底座、合并 LoRA", "采用 Example-3B 作为骨干",
            "通过合并多个 LoRA 适配器得到。", "Built by merging two trained LoRA adapters.",
        ):
            with self.assertRaises(ValueError):
                check_new_record(dict(article, note=note, url="https://org.example/candidate"))
        for note in ("基于 Transformer 架构，从零预训练。", "基于作者自训底座。", "基于 Llama3 的架构，从零训练。"):
            check_new_record(dict(article, note=note, url="https://org.example/model"))
