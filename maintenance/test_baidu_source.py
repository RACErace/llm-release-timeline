import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import collector
import sources
from requests import HTTPError


SOURCE = next(source for source in sources.registry() if source["id"] == "baidu-news")
ORIGIN = "https://ernie.baidu.com"
PREVIEW = "/zh/release/ernie-5-5-flash-preview/"
BASE = "/zh/release/ernie-5-5-flash-base/"


def card(path, title, date, category="产品动态", featured=False):
    tag = "h2" if featured else "h3"
    return (
        f'<a class="article-link" href="{path}"><{tag}>{title}</{tag}>'
        f'<p class="meta"><span>{category}</span><time datetime="{date}"></time></p></a>'
    )


def article(title, intro):
    return f'<main><h1>{title}</h1><article><div class="article-body"><p>{intro}</p></div></article></main>'


class BaiduReleaseTests(unittest.TestCase):
    def snapshot(self, index, articles, seen=None):
        pages = {SOURCE["url"]: "<main>" + index + "</main>"}
        pages.update({ORIGIN + path: content for path, content in articles.items()})
        with patch.object(sources, "get", side_effect=lambda url: Mock(url=url, content=pages[url].encode("utf-8"))):
            return sources.read_source(SOURCE, None, seen)

    def test_preview_and_base_are_distinct_events_and_replay_quietly(self):
        model = "ERNIE 5.5 Flash Preview"
        base = "ERNIE 5.5 Flash Base"
        index = card(PREVIEW, model, "2026-11-11", "精选 / 产品动态", True)
        index += card(BASE, base, "2026-11-05")
        entries = {
            PREVIEW: article(model, f"今天，我们发布 {model}，这是此前 Base 的后训练版本。"
                                    "模型 API 已在千帆平台上线，权重将于11月25日前开源。"),
            BASE: article(base, f"今天，我们发布 {base}。本次发布的权重是预训练终点的 checkpoint。"),
        }
        rows = self.snapshot(index, entries)
        preview, weights = rows.values()
        self.assertEqual((preview["name"], preview["date"], preview["kind"]),
                         (model, "2026-11-11", "预览"))
        self.assertEqual((weights["name"], weights["kind"]), (base, "开放权重"))
        self.assertNotEqual(collector.event_id(preview), collector.event_id(weights))
        state = {"sources": {}, "pending": {}, "delivered": {}}
        self.assertEqual(collector.accept_snapshot(state, SOURCE, rows, "2026-11-12", [weights]), 1)
        self.assertEqual(self.snapshot(index, {}, seen=set(rows)), {})
        self.assertTrue(collector.already_catalogued(preview, [dict(preview, url2=ORIGIN + BASE)]))

    def test_new_product_stages_and_exclusions(self):
        label = "ERNIE 6.0"
        path = "/zh/research/posts/ernie6/"
        index = card(path, label + " 正式发布", "2026-12-01")
        for slug, title, category in [
            ("benchmark", label + " 荣登评测榜首", "模型评测"),
            ("paper", label + " 技术报告", "技术研究"),
            ("tune", label + " 医疗专项微调", "产品动态"),
            ("quant", label + "-AWQ", "产品动态"),
            ("image", "ERNIE-Image 文生图模型发布", "产品动态"),
            ("tool", "FastDeploy 原生支持 " + label, "产品动态"),
        ]:
            index += card("/zh/research/posts/" + slug, title, "2026-12-01", category)
        for intro, kind in [
            ("今天正式发布 ERNIE 6.0。", "发布"),
            ("ERNIE 6.0 API 已在千帆平台上线。", "开放 API"),
            ("ERNIE 6.0 API 已上线，同步开源评测代码，权重将开放。", "开放 API"),
            ("ERNIE 6.0 现已开放权重。", "开放权重"),
        ]:
            with self.subTest(kind=kind):
                row, = self.snapshot(index, {path: article(label, intro)}).values()
                self.assertEqual((row["name"], row["kind"]), (label, kind))
        preview = "ERNIE 6.0 Flash Preview"
        row, = self.snapshot(
            card(path, preview, "2026-12-02"),
            {path: article(preview, f"今天，我们正式开放 {preview} 的模型权重，权重现已开放下载。")},
        ).values()
        self.assertEqual(row["kind"], "开放权重")

    def test_broken_upstream_is_not_an_empty_snapshot(self):
        with self.assertRaisesRegex(ValueError, "index"):
            self.snapshot("", {})
        with self.assertRaisesRegex(ValueError, "announcement"):
            self.snapshot(card(PREVIEW, "ERNIE 5.5 Flash Preview", "2026-11-11"), {PREVIEW: "<main/>"})

    def test_article_identity_date_and_origin(self):
        label = "ERNIE 6.0"
        path = "/zh/news/a-new-announcement/"
        index = card(path, label, "2026-01-02") + card("https://example.org/fake", label, "2026-01-02")
        row, = self.snapshot(index, {path: article(label, label + " 正式上线。")}).values()
        self.assertEqual(row["url"], ORIGIN + path)
        with self.assertRaisesRegex(ValueError, "model differs"):
            self.snapshot(index, {path: article("ERNIE 6.1", label + " 正式上线。")})
        wrong_date = article(label, label + " 正式上线。").replace(
            "<article>", '<article><p class="article-meta">产品动态 · 2026年1月1日</p>'
        )
        with self.assertRaisesRegex(ValueError, "date differs"):
            self.snapshot(index, {path: wrong_date})
        with patch.object(sources, "get", side_effect=[
            Mock(content=("<main>" + index + "</main>").encode()),
            Mock(url="https://example.org/fake", content=article(label, label + " 正式上线。").encode()),
        ]), self.assertRaisesRegex(ValueError, "outside"):
            sources.read_source(SOURCE, None)

    def test_503_on_later_article_preserves_checkpoint_for_retry(self):
        models = {"a": "ERNIE 6.1", "b": "ERNIE 6.2"}
        index = "<main>" + "".join(
            card("/" + slug, model, "2025-01-02") for slug, model in models.items()
        ) + "</main>"
        def fetch(url):
            if url == SOURCE["url"]:
                return Mock(url=url, content=index.encode())
            name = models[url.rsplit("/", 1)[1]]
            return Mock(url=url, content=article(name, "今天发布 " + name).encode())
        with tempfile.TemporaryDirectory() as directory, patch.object(
            collector, "registry", return_value=[SOURCE]
        ), patch.object(collector, "report"):
            path = Path(directory) / "state.json"
            previous = {"checked": "2025-01-01", "seen": []}
            state = collector.state_at(path)
            state["sources"][SOURCE["id"]] = previous
            collector.save(path, state)
            with patch.object(sources, "get", side_effect=[
                fetch(SOURCE["url"]), fetch(ORIGIN + "/a"), HTTPError("503")
            ]):
                self.assertTrue(collector.scan(path))
            self.assertEqual(collector.state_at(path), state)
            with patch.object(sources, "get", side_effect=fetch):
                self.assertFalse(collector.scan(path))
            self.assertEqual(len(collector.state_at(path)["pending"]), 2)
