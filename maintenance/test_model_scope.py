import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import collector
import sources
from model_scope import announcement_in_scope, hub_release_evidence, check_new_record, hub_metadata_in_scope


HUB = {"id": "huggingface:org", "org": "Org", "owner": "org", "channel": "huggingface"}
MODEL = {
    "id": "org/Example", "pipeline_tag": "text-generation",
    "siblings": [{"rfilename": "model.safetensors"}],
}


class FoundationScopeTests(unittest.TestCase):
    def test_reviewed_application_update_cannot_reenter_as_a_model_release(self):
        url = "https://openai.com/index/improving-gpt-5-6-sol-in-chatgpt"
        title = "GPT-5.6 Sol / GPT-5.6 Luna（ChatGPT）"
        for address in (url, url + "/", url + "?utm_source=rss#availability", url.replace("//openai", "//www.openai")):
            self.assertFalse(announcement_in_scope(title, address))
            with self.assertRaisesRegex(ValueError, "应用体验更新"):
                check_new_record({"name": title, "note": "ChatGPT 聊天体验调整。", "url": address})
        self.assertTrue(announcement_in_scope("Introducing GPT-5.6", "https://openai.com/index/introducing-gpt-5-6/"))
        check_new_record({"name": "GPT-5.6 Sol", "note": "首次发布，可在 ChatGPT 和 API 使用。",
                          "url": "https://openai.com/index/introducing-gpt-5-6/"})
        item = sources.signal({"id": "openai", "org": "OpenAI"}, "app-update", title, url,
                              "版本更新", "2026-08-06", model="GPT-5.6 Sol")
        state = {"pending": {"app": {item["id"]: item}}, "delivered": {"original": 1}}
        self.assertEqual(collector.eligible_pending(state), ([], False))
        self.assertEqual(state, {"pending": {}, "delivered": {"original": 1}})

    def test_application_source_filter_keeps_a_real_model_launch_in_the_same_feed(self):
        source = {"id": "openai", "org": "OpenAI", "channel": "feeds", "url": "https://openai.com/news/rss.xml"}
        rss = '''<rss><channel><item><title>Improving GPT-5.6 Sol in ChatGPT</title>
          <link>https://openai.com/index/improving-gpt-5-6-sol-in-chatgpt/</link>
          <pubDate>2026-08-06</pubDate></item><item><title>Introducing GPT-5.6</title>
          <link>https://openai.com/index/introducing-gpt-5-6/</link>
          <pubDate>2026-07-09</pubDate></item></channel></rss>'''
        with patch.object(sources, "get") as get:
            get.return_value.content = rss.encode()
            rows = list(sources.read_source(source, None).values())
        self.assertEqual([row["url"] for row in rows], ["https://openai.com/index/introducing-gpt-5-6/"])

    def test_publisher_versions_keep_the_original_hub_lineage(self):
        version = dict(MODEL, id="org/Example-7B-Instruct", tags=[
            "base_model:org/Example-7B", "base_model:finetune:org/Example-7B",
        ])
        self.assertTrue(hub_metadata_in_scope(version))
        self.assertIn("org/Example-7B", hub_release_evidence(version, "Official model card."))
        for tags in (
            ["base_model:finetune:someone/Example-7B"],
            ["base_model:quantized:org/Example-7B"],
            ["base_model:org/Example-7B", "base_model:org/Example-13B"],
        ):
            self.assertFalse(hub_metadata_in_scope(dict(version, tags=tags)))

        closed = {"date": "2026-11-11", "name": "Example Assistant", "org": "Org",
                  "note": "基于 Atlas-7B 的官方通用预览。", "url": "https://publisher.example/releases/assistant"}
        base = [{"name": "Atlas-7B", "org": "Org"}]
        check_new_record(closed, catalogue=base)
        with self.assertRaises(ValueError):
            check_new_record(dict(closed, org="Outsider"), catalogue=base)
        with self.assertRaises(ValueError):
            check_new_record(dict(closed, note="基于 Atlas-7B，通过合并多个 LoRA 适配器得到。"), catalogue=base)

    def test_lineage_and_task_take_precedence_over_the_name_and_weight_files(self):
        for properties in [
            {"tags": ["base_model:finetune:Qwen/Qwen3.5"]},
            {"cardData": {"base_model": ["original/a", "original/b"]}},
            {"base_model_relation": "finetune"},
            {"pipeline_tag": "sentence-similarity"},
            {"pipeline_tag": "text-to-image"},
            {"siblings": MODEL["siblings"] + [{"rfilename": "adapter_config.json"}]},
            {"id": "org/Qwen-7B-Chat", "cardData": {"base_model": "Qwen/Qwen-7B"}},
            {"id": "org/Qwen-7B-Instruct-AWQ"},
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
            self.assertFalse(hub_release_evidence(MODEL, card))
        self.assertFalse(hub_release_evidence(
            {"id": "org/Example"}, "A video diffusion model trained from scratch."
        ))
        quantized = dict(MODEL, config={"quantization_config": {"quant_method": "fp8"}})
        self.assertFalse(hub_release_evidence(quantized, "We quantized the base language model."))
        self.assertTrue(hub_release_evidence(
            quantized, "A language model trained from scratch. We use FP8 mixed precision training."
        ))
        self.assertTrue(hub_release_evidence(MODEL, "A pretrained generative text model."))
        self.assertTrue(hub_release_evidence(
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
