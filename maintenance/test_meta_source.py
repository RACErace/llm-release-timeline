import unittest
from unittest.mock import Mock, patch

import collector
import sources


def card(slug, title, stamp="2026-11-09"):
    return (
        f'<article><a href="{slug}"><h2>{title}</h2>'
        f'<time datetime="{stamp}">Date</time></a></article>'
    )


class MetaResearchTests(unittest.TestCase):
    def setUp(self):
        self.source = next(s for s in sources.registry() if s["id"] == "meta-research")

    def snapshot(self, markup):
        response = Mock(content=markup.encode("utf-8"))
        with patch.object(sources, "get", return_value=response):
            return sources.read_source(self.source, None)

    def test_dated_first_party_launches_and_stable_identity(self):
        slug = "/blog/muse-ember"
        title = "Introducing Our Smallest, Most Efficient Model: Muse&nbsp;Ember"
        wonder = card("/blog/muse-wonder", "Muse Wonder: A Frontier Model", "2026-10-12")
        snapshot = self.snapshot(
            wonder
            + card(slug, title)
            + card("/blog/muse-voice", "Introducing Muse Voice Transcribe")
            + card("/blog/muse-code", "Introducing Muse Code")
            + card("/blog/safety", "Safety evaluations of Muse Ember")
            + card("https://example.com/blog/other", "Introducing Muse Other")
        )
        self.assertEqual(len(snapshot), 2)
        ember = next(r for r in snapshot.values() if "Ember" in r["name"])
        self.assertEqual(ember["kind"], "发布")
        self.assertEqual(ember["date"], "2026-11-09")
        self.assertEqual(ember["url"], "https://research.meta.ai/blog/muse-ember")
        renamed = self.snapshot(card(slug + "/?ref=home", "Introducing Muse Ember"))
        self.assertIn(ember["id"], renamed)
        self.assertTrue(
            collector.already_catalogued(ember, [dict(ember, name="Muse Ember")])
        )

    def test_new_model_name_and_combined_client_launch(self):
        snapshot = self.snapshot(
            card("/blog/new", "Introducing Muse Example")
            + card("/blog/combined", "Introducing Muse Code and Muse Spark 1.2")
        )
        self.assertEqual(len(snapshot), 2)

    def test_first_scan_queues_only_uncatalogued_launches_in_bootstrap_window(self):
        def launch(model, date):
            return card(f"/blog/muse-{model.lower()}", f"Introducing Muse {model}", date)

        snapshot = self.snapshot(
            launch("Ember", "2026-11-09")
            + launch("Today", "2026-11-10")
            + launch("Older", "2026-11-08")
            + launch("Future", "2026-11-11")
            + launch("Known", "2026-11-09")
        )
        records = [
            {
                "org": "Meta",
                "name": "Muse Known",
                "url": "https://research.meta.ai/blog/muse-known",
                "kind": "发布",
                "date": "2026-11-09",
            }
        ]
        state = {"sources": {}, "pending": {}, "delivered": {}}
        first = collector.accept_snapshot(
            state, self.source, snapshot, "2026-11-10T00:01:00+00:00", records
        )
        self.assertEqual(first, 2)
        pending = [r for group in state["pending"].values() for r in group.values()]
        self.assertEqual(
            {r["name"] for r in pending},
            {"Introducing Muse Ember", "Introducing Muse Today"},
        )
        repeat = collector.accept_snapshot(
            state, self.source, snapshot, "2026-11-10T12:01:00+00:00", records
        )
        self.assertEqual(repeat, 0)
        reached_date = collector.accept_snapshot(
            state, self.source, snapshot, "2026-11-11T00:01:00+00:00", records
        )
        self.assertEqual(reached_date, 1)
        future = next(r for r in snapshot.values() if r["date"] == "2026-11-11")
        self.assertIn(future["id"], state["pending"][collector.event_id(future)])

    def test_missing_index_or_publication_date_fails_scan(self):
        for html in (
            "<p>Loading</p>",
            card("/blog/muse-example", "Introducing Muse Example", "today"),
            '<article><a href="/blog/muse-example"><h2>Introducing Muse Example</h2></a></article>',
        ):
            with self.subTest(html=html), self.assertRaises(ValueError):
                self.snapshot(html)
