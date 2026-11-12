import unittest
from xml.etree import ElementTree as ET

import collector
import sources
from anthropic_releases import release


RSS = {"id": "claude", "org": "Anthropic"}
NEWS = {
    "id": "claude-news",
    "org": "Anthropic",
    "url": "https://www.anthropic.com/news",
    "bootstrap_days": 3,
}


class AnthropicTests(unittest.TestCase):
    def test_capture_model_launches_and_exclude_feature_announcements(self):
        for title, expected in [
            ("Claude Mythos 6 is now available.", ("Claude Mythos 6", "发布")),
            ("Mythos 6 is now available on the Claude API.", ("Claude Mythos 6", "开放 API")),
            ("Claude Mythos 6 is now in public preview.", ("Claude Mythos 6", "预览")),
            ("We announced Claude Mythos Preview is available as a gated research preview.",
             ("Claude Mythos Preview", "预览")),
            ("Introducing Claude 6", ("Claude 6", "发布")),
            ("Introducing Claude Cipher 6", ("Claude Cipher 6", "发布")),
            ("Introducing Claude 3.5 Sonnet", ("Claude Sonnet 3.5", "发布")),
            ("Introducing the next generation of Claude", ("", "发布")),
            ("Claude Sonnet 3.5, our most intelligent model yet, just got an upgrade "
             "and is now available on the Claude API.", ("Claude Sonnet 3.5", "版本更新")),
            ("We’ve launched Claude Fable 5.1, the successor to Claude Fable 5, "
             "alongside Claude Mythos 5.1 for verified organizations.",
             ("Claude Fable 5.1 / Mythos 5.1", "发布")),
        ]:
            with self.subTest(title=title):
                self.assertEqual(release(title), expected)
        for title in [
            "We’ve launched fast mode for Claude Fable 5.2 in Messages API requests.",
            "Fast mode for Claude Opus 5.5 is now available on Microsoft Foundry.",
            "We’ve launched Claude Models on Google Cloud.",
            "We’ve released the Claude API skill.",
            "Claude Fable 5 requires 30-day retention and is not available under ZDR.",
            "We’ve launched Claude Opus 5.5 fast mode.",
            "Expanding our partnership to bring Claude to all levels of government",
        ]:
            with self.subTest(title=title):
                self.assertIsNone(release(title))

    @staticmethod
    def feed():
        root = ET.Element("rss")
        item = ET.SubElement(root, "item")
        ET.SubElement(item, "title").text = "Claude Platform release notes — November 9, 2026"
        ET.SubElement(item, "pubDate").text = "Mon, 09 Nov 2026 00:00:00 GMT"
        ET.SubElement(item, "link").text = (
            "https://platform.claude.com/docs/en/release-notes/overview#claude-fable-5-2"
        )
        # Keep the first 180 characters of the RSS evidence from Issue #34.
        ET.SubElement(item, "description").text = (
            "<ul><li>We’ve launched <strong>Claude Fable 5.2</strong> "
            "(<code>claude-fable-5-2</code>), built for demanding reasoning and "
            "long-horizon agentic work. <strong>Claude Mythos 5.2</strong> "
            "(<code>claude-mythos-5-2</code>) offers the same capabilities only to "
            "verified organizations. The previous Claude Fable 5.1 and Claude "
            "Mythos 5.1 cost more on the API.</li>"
            "<li>We’ve launched fast mode for Claude Fable 5.2 in the API.</li></ul>"
        )
        return next(iter(sources.feed_items(RSS, ET.tostring(root))))

    @staticmethod
    def newsroom():
        page = """<ul><li data-listing-row="a"><a href="/claude-fable-and-mythos-5-2">
        <h3>Introducing Claude Mythos 5.2 and Claude Fable 5.2</h3></a>
        <time>Nov 9, 2026</time></li><li data-listing-row="b"><a href="/partnerships">
        <h3>Expanding our partnership to bring Claude to governments</h3></a></li></ul>"""
        row, = sources.page_items(NEWS, page)
        return row

    def test_rss_newsroom_reprints_and_api_stage(self):
        rss, news = self.feed(), self.newsroom()
        self.assertEqual(rss["id"], "f2cc2160b1ce89656ed00d74014bcbfe")
        self.assertEqual((rss["name"], rss["kind"]), ("Claude Fable 5.2 / Mythos 5.2", "发布"))
        self.assertEqual(news["date"], "2026-11-09")
        state = {"pending": {}, "sources": {"claude": {"checked": "2026-11-08", "seen": []}}}
        for source, item in [(RSS, rss), (NEWS, news)]:
            self.assertEqual(collector.accept_snapshot(
                state, source, {item["id"]: item}, "2026-11-10", []
            ), 1)
        group, = state["pending"]
        self.assertEqual(len(state["pending"][group]), 2)
        self.assertIn("发布方原文", collector.issue_body(group, [rss, news]))
        self.assertEqual(group, collector.event_id(dict(news, date="2026-11-11")))
        self.assertNotEqual(group, collector.event_id(dict(rss, kind="开放 API")))

    def test_restricted_companion_in_the_september_platform_note(self):
        # The platform listed the two access tiers in separate sentences (#69).
        title = (
            "We’ve launched Claude Fable 5.1 (claude-fable-5-1), the successor to "
            "Claude Fable 5. For Project Glasswing participants, we’ve also launched "
            "Claude Mythos 5.1 (claude-mythos-5-1)."
        )
        name, kind = release(title)
        record = {
            "org": "Anthropic", "name": "Claude Fable 5.1 / Mythos 5.1",
            "kind": "发布", "date": "2026-09-01",
            "url": "https://www.anthropic.com/claude-fable-and-mythos-5-1",
        }
        item = dict(record, name=name, kind=kind, source="claude", model=name,
                    url="https://platform.claude.com/docs/en/release-notes/overview#september-1-2026")
        self.assertTrue(collector.already_catalogued(item, [record]))

    def test_deployed_state_and_catalog_do_not_reopen_issue_34(self):
        rss, news = self.feed(), self.newsroom()
        state = {
            "pending": {}, "delivered": {"6b278419dc1e536ce5008c682f47c88c": 34},
            "sources": {"claude": {"checked": "2026-11-10", "seen": [rss["id"]]}},
        }
        records = [dict(rss, url=news["url"])]
        for source, item in [(RSS, rss), (NEWS, news)]:
            self.assertEqual(collector.accept_snapshot(
                state, source, {item["id"]: item}, "2026-11-10", records
            ), 0)
        self.assertFalse(state["pending"])
        historical = dict(rss, name="Claude Haiku 3.5", model="Claude Haiku 3.5", kind="开放 API")
        self.assertTrue(collector.already_catalogued(historical, [dict(historical, name="Claude 3.5 Haiku")]))
        update = dict(rss, model="Claude Sonnet 3.5", kind="版本更新", date="2024-10-22")
        record = dict(update, name="Claude 3.5 Sonnet（更新版）", kind="发布")
        self.assertTrue(collector.already_catalogued(update, [record]))
        self.assertFalse(collector.already_catalogued(dict(update, date="2024-11-01"), [record]))
        with self.assertRaises(ValueError):
            list(sources.page_items(NEWS, "<html>Temporary outage</html>"))
