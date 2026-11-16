import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import collector
import sources
from model_scope import check_new_record, hub_release_evidence


def table(headers, rows):
    return "<table>" + "".join("<tr>" + "".join(f"<td>{x}</td>" for x in r) + "</tr>"
                              for r in [headers, *rows]) + "</table>"


def source(name):
    return next(s for s in sources.registry() if s["id"] == name)


def parse(name, html):
    return list(sources.page_items(source(name), html.encode()))


class PublisherReleaseTests(unittest.TestCase):
    def test_huawei_shared_cells_do_not_corrupt_the_following_models(self):
        html = ('<table><tr><th>模型名称</th><th>模型类型</th><th>模型版本</th>'
                '<th>预置服务</th><th>版本说明</th></tr>'
                '<tr><td>Vidu-T2V</td><td rowspan="2">视频生成</td><td>20261115</td>'
                '<td rowspan="2">√</td><td>视频</td></tr>'
                '<tr><td>Vidu-IT2V</td><td>20261115</td><td>视频</td></tr>'
                '<tr><td>openPangu-2.1-Pro</td><td>文本生成</td><td>20261115</td>'
                '<td>√</td><td>512K</td></tr><tr><td colspan="5">说明</td></tr></table>')
        row, = parse("huawei-maas", html)
        self.assertEqual((row["name"], row["date"]), ("openPangu-2.1-Pro", "2026-11-15"))
        with self.assertRaisesRegex(ValueError, "incomplete row"):
            parse("huawei-maas", html.replace('<td>512K</td>', ''))

    def test_cloud_changelog_uses_original_publishers_and_row_dates(self):
        tc = table(["动态名称", "动态描述", "发布时间", "相关文档"], [
            ["新增支持模型", "新增支持 Hy4 flash 模型。", "2026-11-16", "模型列表"],
            ["新增支持模型", "新增支持 Hy4 preview 模型。", "2026-08-28", "模型列表"],
            ["新增支持模型", "新增支持 Qwen4-Max、DeepSeek-V4.1 模型。", "2026-11-16", "模型列表"],
            ["价格调整", "Hy4 flash 降价。", "2026-11-17", "模型列表"],
        ])
        rows = parse("tencent-tokenhub", tc)
        self.assertEqual([(r["model"], r["date"], r["kind"]) for r in rows], [
            ("Hy4 flash", "2026-11-16", "开放 API"), ("Hy4 preview", "2026-08-28", "预览")])
        hw = table(["模型名称", "模型类型", "模型版本", "预置服务", "版本说明"], [
            ["openPangu-2.1-Pro", "文本生成", "20261115", "√", "支持512K上下文"],
            ["Qwen4-Max", "文本生成", "20261116", "√", "第三方上架"],
            ["openPangu-1.0", "文本对话", "V1", "√", "旧版"],
            ["openPangu-Image", "图像生成", "20261115", "√", "图像模型"],
        ])
        row, = parse("huawei-maas", hw)
        self.assertEqual((row["name"], row["date"], row["kind"]),
                         ("openPangu-2.1-Pro", "2026-11-15", "开放 API"))

    def test_bailian_regions_collapse_but_preview_and_formal_remain_distinct(self):
        def card(model, date, region):
            return (f'<div class="rkb-update" id="region-{region}-{date}">'
                    f'<div class="rkb-update-meta"><h3>{date}</h3></div>'
                    f'<div class="rkb-update-body"><h4>{model}</h4><p>模型上线</p>'
                    f'<a href="/zh/model-studio/{model}">{model}</a></div></div>')
        html = card("qwen4-max", "2026-11-16", 0) + card("qwen4-max", "2026-11-16", 1)
        html += card("qwen4-max-preview", "2026-11-11", 0)
        html += card("qwen-image-3", "2026-11-16", 0) + card("other/model", "2026-11-16", 0)
        rows = {r["id"]: r for r in parse("bailian-releases", html)}
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["kind"] for r in rows.values()}, {"预览", "开放 API"})
        row = next(r for r in rows.values() if r["kind"] == "开放 API")
        self.assertTrue(collector.already_catalogued(row, [dict(row, name="Qwen4-Max")]))
        state = {"sources": {}, "pending": {}, "delivered": {"previous": 99}}
        self.assertEqual(collector.accept_snapshot(state, source("bailian-releases"), rows,
                                                  "2026-11-16", [dict(row, name="Qwen4-Max")]), 0)
        self.assertEqual(state["delivered"], {"previous": 99})

    def test_same_thinking_article_gains_a_dated_weight_event(self):
        src = source("thinking-news")
        index = ('<a class="post-item-link" href="/news/inkling-2-small/">'
                 '<h2>Introducing Inkling 2 Small</h2><time datetime="2026-11-05"></time></a>')
        intro = ('<main><h1>Introducing Inkling 2 Small</h1><article class="content">'
                 '<p>Today we introduce Inkling 2 Small. We intend to release weights next week.</p>{}</article></main>')
        update = ('<blockquote><p><strong>Nov 12, 2026 Update:</strong> '
                  'Inkling 2 Small’s weights are now available for download.</p></blockquote>')
        def scan(body, seen=None):
            with patch.object(sources, "get", return_value=Mock(
                url="https://thinkingmachines.ai/news/inkling-2-small/", content=body.encode())):
                return {r["id"]: r for r in sources.page_items(src, index.encode(), seen)}
        first = scan(intro.format(""))
        self.assertEqual(len(first), 1)
        second = scan(intro.format(update), set(first))
        self.assertEqual(len(second), 2)
        weights = next(r for r in second.values() if r["kind"] == "开放权重")
        self.assertEqual(weights["date"], "2026-11-12")
        state = {"sources": {}, "pending": {}, "delivered": {}}
        collector.accept_snapshot(state, src, first, "2026-11-05", [])
        state["pending"] = {}
        self.assertEqual(collector.accept_snapshot(state, src, second, "2026-11-12", []), 1)
        self.assertEqual(collector.accept_snapshot(state, src, second, "2026-11-12", []), 0)
        self.assertTrue(collector.already_catalogued(weights, [dict(weights,
            url="https://huggingface.co/thinkingmachines/Inkling-2-Small")]))

    def test_malformed_sources_preserve_checkpoint_and_outbox(self):
        for ident in ("huawei-maas", "tencent-tokenhub", "bailian-releases", "thinking-news"):
            with self.subTest(ident=ident), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "state.json"
                state = {"version": 1, "sources": {ident: {"checked": "2026-11-12", "seen": ["old"]}},
                         "pending": {}, "delivered": {"receipt": 7}}
                collector.save(path, state)
                with patch.object(collector, "registry", return_value=[source(ident)]), \
                     patch.object(sources, "get", return_value=Mock(content=b"<title>Security Verification</title>")), \
                     patch.object(collector, "report"):
                    self.assertTrue(collector.scan(path))
                self.assertEqual(json.loads(path.read_text()), state)

    def test_native_training_precision_and_disclosed_publisher_package(self):
        row = {"id": "thinkingmachines/Inkling-2-Small", "pipeline_tag": "image-text-to-text",
               "config": {"quantization_config": {"quant_method": "modelopt"}}}
        card = "The model was pretrained, midtrained and post-trained in NVFP4, so this is the native precision."
        self.assertTrue(hub_release_evidence(row, card))
        self.assertFalse(hub_release_evidence(row, "We converted the pretrained language model to NVFP4."))
        agnes = {"id": "Agnes-AI/Agnes-2.5-Pro-Theta", "pipeline_tag": "image-text-to-text",
                 "config": {"architectures": ["AgnesForConditionalGeneration"]}}
        card = ("**Agnes 2.5 Pro Theta** is a multimodal mixture-of-experts checkpoint. "
                "This release provides BF16 weights for local inference.")
        self.assertTrue(hub_release_evidence(agnes, card))
        self.assertFalse(hub_release_evidence(dict(agnes, id="someone/Agnes-2.5-Pro-Theta"), card))
        self.assertFalse(hub_release_evidence(agnes, card + " The model is built on Qwen3.5-35B."))
        self.assertFalse(hub_release_evidence({"id": "nex-agi/Nex-N2.6", "pipeline_tag": "image-text-to-text"},
                         "Nex-N2.6 is built on GLM-5.3-Flash. These are FP8-quantized weights."))
        check_new_record({"name": "Agnes 2.5 Pro Theta", "note": "采用 Apache 2.0 许可。",
                          "url": "https://huggingface.co/Agnes-AI/Agnes-2.5-Pro-Theta"})


if __name__ == "__main__":
    unittest.main()
