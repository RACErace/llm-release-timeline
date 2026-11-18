import unittest
from unittest.mock import Mock, patch

import collector
import sources


class InceptionSourceTests(unittest.TestCase):
    source = next(s for s in sources.registry() if s["id"] == "mercury")
    site = "https://www.inceptionlabs.ai/"
    index = "https://framerusercontent.com/sites/example/searchIndex-build.json"
    path = "/blog/introducing-mercury-3"
    url = site.rstrip("/") + path
    title = "Diffusion at Scale: Introducing Mercury 3"

    def read(self, entries, article="", *, seen=(), final_url=None):
        pages = {
            self.site: Mock(content=f'<meta name="framer-search-index" content="{self.index}">'.encode()),
            self.index: Mock(**{"json.return_value": entries}),
            self.url: Mock(content=article.encode(), url=final_url or self.url),
        }
        with patch.object(sources, "get", side_effect=pages.__getitem__) as get:
            rows = sources.read_source(self.source, None, set(seen))
            return rows, get.call_count

    def test_full_index_and_dates_belonging_to_the_announcement(self):
        entries = {
            "/research": {"h1": ["Research"]},
            "/blog/award": {"h1": ["Mercury 3 wins award"], "h6": [self.title]},
            "/blog/voice": {"h1": ["Introducing Mercury Voice"]},
            self.path: {"h1": [self.title]},
        }
        related = '<a href="./award"><time datetime="2026-10-20">Oct 20</time></a>'
        own = '<a href="./introducing-mercury-3"><time datetime="2026-10-19T00:00:00Z">Oct 19</time></a>'
        rows, calls = self.read(entries, f"<h1>{self.title}</h1>" + related + own)
        item, = rows.values()
        self.assertEqual((item["model"], item["date"], item["url"]), ("Mercury 3", "2026-10-19", self.url))
        self.assertEqual(calls, 3)
        self.assertEqual(self.read(entries, seen=rows), ({}, 2))

    def test_undated_new_article_after_migration_is_queued_once(self):
        entries = {self.path: {"h1": [self.title]}}
        article = f'<h1>{self.title}</h1><a href="./other"><time datetime="2026-11-18"></time></a>'
        rows, _ = self.read(entries, article)
        item, = rows.values()
        self.assertEqual(item["date"], "")
        state = {"sources": {"mercury": {"checked": "2026-11-17", "seen": ["old-id"], "revision": 2}}, "pending": {}}
        collector.accept_snapshot(state, self.source, {}, "2026-11-18", [])
        self.assertEqual(collector.accept_snapshot(state, self.source, rows, "2026-11-19", []), 1)
        dated, _ = self.read(entries, article + '<meta property="article:published_time" content="2026-11-19">')
        self.assertEqual(next(iter(dated.values()))["date"], "2026-11-19")
        self.assertEqual(collector.accept_snapshot(state, self.source, dated, "2026-11-20", []), 0)
        self.assertIn("old-id", state["sources"]["mercury"]["seen"])
        self.assertEqual(len(state["pending"]), 1)

    def test_migration_recovers_recent_announcement_without_replaying_archive(self):
        entries = {self.path: {"h1": [self.title]}}
        article = f"<h1>{self.title}</h1>"
        for stamp, expected in [("2026-11-18", 1), ("2026-10-19", 0), ("", 0)]:
            with self.subTest(stamp=stamp):
                meta = f'<time itemprop="datePublished" datetime="{stamp}"></time>' if stamp else ""
                rows, _ = self.read(entries, article + meta)
                old = {"checked": "2026-11-17", "seen": ["old-id"], "revision": 2}
                state = {"sources": {"mercury": old}, "pending": {}}
                self.assertEqual(collector.accept_snapshot(state, self.source, rows, "2026-11-18", []), expected)
                self.assertIn("old-id", state["sources"]["mercury"]["seen"])
                self.assertEqual(collector.accept_snapshot(state, self.source, rows, "2026-11-18", []), 0)

    def test_broken_index_or_redirected_article_does_not_advance_source(self):
        with self.assertRaisesRegex(ValueError, "missing Inception blog articles"):
            self.read({"/": {"h1": ["Inception"]}})
        with self.assertRaisesRegex(ValueError, "missing Inception blog headline"):
            self.read({self.path: {"h6": [self.title]}})
        with self.assertRaisesRegex(ValueError, "differs from its index"):
            self.read({self.path: {"h1": [self.title]}}, f"<h1>{self.title}</h1>", final_url=self.site)
