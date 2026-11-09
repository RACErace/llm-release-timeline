import json
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET

import catalog
import collector
import sources


def weight(channel, name="Test-7B"):
    return sources.signal(
        {"id": channel, "org": "Test Org"},
        name,
        name,
        "https://example.org/" + channel + "/" + name,
        "开放权重",
        model=name,
    )


class CollectorTests(unittest.TestCase):
    def test_hub_versions_and_quantized_repackaging(self):
        source = {
            "id": "hf",
            "channel": "huggingface",
            "org": "OpenBMB",
            "owner": "openbmb",
        }
        record = {
            "org": "OpenBMB",
            "kind": "开放权重",
            "name": "MiniCPM-V 4.5",
            "date": "2025-08-26",
        }

        def hub(name):
            return sources.hub_signal(
                source,
                {
                    "id": "openbmb/" + name,
                    "pipeline_tag": "text-generation",
                    "siblings": [{"rfilename": "model.safetensors"}],
                },
            )

        for suffix in ("-BNB", "-Thinking-BNB", "-bitsandbytes-4bit"):
            self.assertIsNone(hub("MiniCPM-V-4.6" + suffix))
        alias = hub("MiniCPM-V-4_5")
        self.assertTrue(collector.already_catalogued(alias, [record]))
        self.assertFalse(
            collector.already_catalogued(alias, [dict(record, org="Other")])
        )
        self.assertFalse(
            collector.already_catalogued(alias, [dict(record, kind="预览")])
        )
        for name in (
            "MiniCPM-V-45",
            "MiniCPM-V-4.5-Base",
            "MiniCPM-V-4_5-Thinking",
            "MiniCPM-V-4.5-SFT",
        ):
            other = hub(name)
            self.assertIsNotNone(other)
            self.assertFalse(collector.already_catalogued(other, [record]))
            self.assertNotEqual(collector.event_id(other), collector.event_id(alias))

    def test_old_weight_on_second_page_of_first_incremental_scan(self):
        initial = "2025-02-02T12:00:00+00:00"
        state = {"sources": {"hf": {"checked": initial, "seen": []}}, "pending": {}}
        old = dict(weight("hf", "Older"), upstream_updated="2025-02-02T11:59:00Z")
        new = dict(weight("hf", "New"), upstream_updated="2025-02-02T12:02:00Z")
        count = collector.accept_snapshot(
            state,
            {"id": "hf"},
            {old["id"]: old, new["id"]: new},
            "2025-02-02T12:30:00+00:00",
            [],
        )
        self.assertEqual(count, 1)
        self.assertEqual(set(state["pending"]), {collector.event_id(new)})

    def test_baseline_cross_source_evidence_and_later_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = collector.state_at(Path(tmp) / "state.json")
        old = weight("hf", "Older")
        for s in ("hf", "ms"):
            collector.accept_snapshot(
                state, {"id": s}, {old["id"]: old}, "2025-08-01", []
            )
        self.assertFalse(state["pending"])
        for s in ("hf", "ms"):
            new = weight(s)
            snapshot = {new["id"]: new}
            collector.accept_snapshot(state, {"id": s}, snapshot, "2025-08-02", [])
            self.assertEqual(
                collector.accept_snapshot(state, {"id": s}, snapshot, "2025-08-03", []),
                0,
            )
        self.assertEqual(len(state["pending"]), 1)
        self.assertEqual(len(next(iter(state["pending"].values()))), 2)
        preview = dict(new, kind="预览")
        self.assertNotEqual(collector.event_id(new), collector.event_id(preview))

    def test_failed_source_preserves_its_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = collector.state_at(path)
            state["sources"]["down"] = {
                "checked": "2025-01-01T00:00:00+00:00",
                "seen": ["saved"],
            }
            collector.save(path, state)
            with (
                patch.object(
                    collector, "registry", return_value=[{"id": "down"}, {"id": "ok"}]
                ),
                patch.object(
                    collector,
                    "read_source",
                    side_effect=lambda s, _: {} if s["id"] == "ok" else self._offline(),
                ),
                patch.object(collector, "report"),
            ):
                self.assertTrue(collector.scan(path))
            saved = collector.state_at(path)
            self.assertEqual(saved["sources"]["down"], state["sources"]["down"])
            self.assertIn("ok", saved["sources"])

    @staticmethod
    def _offline():
        raise ConnectionError("offline")

    def test_retry_after_successful_issue_creation_before_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = collector.state_at(path)
            item = weight("hf")
            group = collector.event_id(item)
            state["pending"][group] = {item["id"]: item}
            collector.save(path, state)
            remote = {
                "number": 12,
                "state": "open",
                "comments": 0,
                "body": collector.issue_body(group, [item]),
                "user": {"login": "github-actions[bot]"},
            }
            with (
                patch.object(collector, "api", return_value=[remote]) as api,
                patch.object(collector, "report"),
            ):
                collector.deliver(path, "owner/repo", 10)
                self.assertEqual(api.call_count, 1)
            received = collector.state_at(path)
            self.assertEqual(received["delivered"][group], 12)
            self.assertFalse(received["pending"])

    def test_hub_follows_pages_through_previous_success(self):
        def model(name, stamp):
            return {
                "id": "official/" + name,
                "lastModified": stamp,
                "pipeline_tag": "text-generation",
                "siblings": [{"rfilename": "model.safetensors"}],
            }

        first = Mock(
            links={"next": {"url": "https://huggingface.co/api/models?cursor=more"}}
        )
        first.json.return_value = [model("First", "2025-01-04T10:00:00Z")]
        last = Mock(links={})
        last.json.return_value = [
            model("Second", "2025-01-03T09:00:00Z"),
            model("Old", "2025-01-01T00:00:00Z"),
        ]
        source = {
            "id": "hf",
            "org": "Test Org",
            "owner": "official",
            "channel": "huggingface",
        }
        with patch.object(sources, "get", side_effect=[first, last]):
            rows = sources.read_source(source, "2025-01-04T00:00:00+00:00")
        self.assertEqual({r["name"] for r in rows.values()}, {"First", "Second"})

    def test_empty_feed_is_valid_but_login_page_is_not(self):
        source = {"id": "arxiv", "org": "", "discovery": True}
        self.assertEqual(list(sources.feed_items(source, b"<rss><channel/></rss>")), [])
        with self.assertRaises(ValueError):
            list(sources.feed_items(source, b"<html><body>login</body></html>"))

    def test_paper_discovery_requires_a_model_claim_and_separates_all_feed_dates(self):
        source = {"id": "arxiv-cl", "org": "", "discovery": True}
        feed = ET.Element("rss")
        for i, (title, announcement, abstract) in enumerate(
            [
                (
                    "KLEAR: LLM Evaluation",
                    "new",
                    "We introduce KLEAR, a framework for comparing language models.",
                ),
                (
                    "DUET: Inference for Language Models",
                    "new",
                    "We present DUET, a training-free framework for language models.",
                ),
                (
                    "MemoryDojo: LLM Agent Memory",
                    "cross",
                    "We release a benchmark of open-weight language models.",
                ),
                (
                    "Example-7B Technical Report",
                    "new",
                    "We train and release Example-7B, an open language model.",
                ),
                (
                    "Example-2: A New Family",
                    "cross",
                    "Earlier language models have limitations. " * 30
                    + "We present Example-2, a family of multilingual language models.",
                ),
                (
                    "CoBit: Language Modeling with Bitstream Diffusion",
                    "replace",
                    "We introduce CoBit, a continuous diffusion language model that generates text.",
                ),
                (
                    "LittleLearner: Language Models Under Controlled Exposure",
                    "replace-cross",
                    (
                        "We introduce a curriculum. Training yields LittleLearner, a model with measured language competence. "
                        "We release the curriculum and LittleLearner as a sandbox."
                    ),
                ),
                (
                    "ChainFlow",
                    "cross",
                    "We introduce a paradigm for faster LLMs. We open-source code and drafter models.",
                ),
            ]
        ):
            entry = ET.SubElement(feed, "item")
            ET.SubElement(entry, "title").text = title
            ET.SubElement(entry, "link").text = f"https://arxiv.org/abs/2611.0000{i}"
            ET.SubElement(entry, "pubDate").text = "Mon, 09 Nov 2026 00:00:00 -0500"
            ET.SubElement(
                entry, "{http://arxiv.org/schemas/atom}announce_type"
            ).text = announcement
            ET.SubElement(
                entry, "description"
            ).text = f"Announce Type: {announcement}\nAbstract: {abstract}"
        items = list(sources.feed_items(source, ET.tostring(feed)))
        self.assertEqual(
            [row["name"].split(":")[0] for row in items],
            [
                "Example-7B Technical Report",
                "Example-2",
                "CoBit",
                "LittleLearner",
            ],
        )
        self.assertEqual(
            [row["arxiv"]["type"] for row in items],
            ["new", "cross", "replace", "replace-cross"],
        )
        for row in items:
            self.assertEqual(row["arxiv"]["date"], "2026-11-09")
            self.assertEqual(row["date"], "")
            body = collector.issue_body(collector.event_id(row), [row])
            self.assertIn("公告日期 2026-11-09", body)
            self.assertIn('"date": ""', body)

    def test_old_pending_papers_are_rechecked_before_delivery(self):
        source = {"id": "arxiv-cl", "org": ""}
        papers = [
            sources.signal(
                source,
                url,
                title,
                url,
                "研究公开",
                "2026-11-09",
                "Announce Type: replace Abstract: " + "Introduction. " * 40,
            )
            for title, url in [
                ("Example paper", "https://arxiv.org/abs/2601.12345"),
                ("A survey", "https://arxiv.org/abs/2601.12346"),
            ]
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = collector.state_at(path)
            for i, row in enumerate(papers):
                state["pending"][str(i)] = {row["id"]: row}
            collector.save(path, state)
            with (
                patch.object(
                    collector, "arxiv_abstracts", side_effect=OSError("offline")
                ),
                patch.object(collector, "api") as api,
            ):
                self.assertTrue(collector.deliver(path, "owner/repo", 10))
                self.assertEqual(api.call_count, 0)
                self.assertEqual(collector.state_at(path)["pending"], state["pending"])
            abstracts = {
                papers[0]["url"]: (
                    "Example paper",
                    "We introduce Example, a new language model.",
                ),
                papers[1]["url"]: (
                    "A survey",
                    "We present a benchmark of language models.",
                ),
            }
            with (
                patch.object(collector, "arxiv_abstracts", return_value=abstracts),
                patch.object(collector, "api", side_effect=[[], {"number": 43}]) as api,
            ):
                self.assertFalse(collector.deliver(path, "owner/repo", 10))
                body = api.call_args.args[1]["body"]
                self.assertIn("We introduce Example, a new language model.", body)
                self.assertIn("公告类型 replace", body)
                self.assertIn('"date": ""', body)
                self.assertIn("<!-- atlas-candidate:0 -->", body)
            saved = collector.state_at(path)
            self.assertEqual(saved["pending"], {})
            self.assertEqual(saved["delivered"], {"0": 43})

    def test_material_revisions_can_open_new_issues_after_url_only_baseline(self):
        source = {"id": "arxiv-cl", "org": "", "discovery": True}
        url = "https://arxiv.org/abs/2601.12345"
        state = {
            "sources": {
                "arxiv-cl": {
                    "seen": [sources.key("arxiv-cl", url)],
                    "checked": "2026-11-09",
                }
            },
            "delivered": {"earlier-issue": 1},
            "pending": {},
        }

        def accept(abstract, date, version):
            row = sources.paper_signal(
                source,
                "Example Technical Report",
                url + "v" + str(version),
                abstract,
                date,
                "replace",
                version,
            )
            self.assertEqual(row["url"], url)
            return collector.accept_snapshot(state, source, {row["id"]: row}, date, [])

        baseline = "We introduce Example, a 7B language model."
        self.assertEqual(accept(baseline, "2026-11-09", 1), 0)
        self.assertEqual(
            accept(
                "We present Example, a 7 billion parameter language model. It scores 99%.",
                "2026-11-10",
                2,
            ),
            0,
        )
        revised = "We introduce Example, a 7B language model. We release unified models at 7B and 13B."
        self.assertEqual(accept(revised, "2026-11-11", 3), 1)
        group, items = state["pending"].popitem()
        self.assertEqual(
            set(next(iter(items.values()))["arxiv"]["artifacts"]),
            {"model", "name:example", "size:7b", "size:13b", "variant:unified"},
        )
        state["delivered"][group] = 2
        self.assertEqual(accept(baseline, "2026-11-12", 4), 0)
        self.assertEqual(accept(revised, "2026-11-13", 5), 0)
        self.assertEqual(
            accept(revised + " We release the model weights.", "2026-11-14", 6), 1
        )
        self.assertNotIn(group, state["pending"])

    def test_first_post_upgrade_revision_compares_previous_full_abstract(self):
        source = {"id": "arxiv-cl", "org": "", "discovery": True}
        url = "https://arxiv.org/abs/2610.23170"
        state = {
            "sources": {
                "arxiv-cl": {
                    "checked": "2026-11-08",
                    "seen": [sources.key("arxiv-cl", url)],
                }
            },
            "pending": {},
        }
        row = sources.paper_signal(
            source,
            "Chinese Decision",
            url,
            "We present Chinese Decision, a family of language models. We release decision-only and unified models at 2B.",
            "2026-11-09",
            "replace",
            3,
        )
        with (
            patch.object(collector, "arxiv_abstracts", return_value={}),
            self.assertRaisesRegex(ValueError, "previous arXiv revision"),
        ):
            collector.accept_snapshot(state, source, {row["id"]: row}, "2026-11-09", [])
        self.assertEqual(state["sources"]["arxiv-cl"]["checked"], "2026-11-08")
        with patch.object(
            collector,
            "arxiv_abstracts",
            return_value={
                url + "v2": (
                    "Chinese Decision",
                    "We present Chinese Decision, a family of language models. We release decision-only models at 2B.",
                )
            },
        ) as fetch:
            self.assertEqual(
                collector.accept_snapshot(
                    state, source, {row["id"]: row}, "2026-11-09", []
                ),
                1,
            )
            self.assertEqual(list(fetch.call_args.args[0]), [url + "v2"])


class ReviewTests(unittest.TestCase):
    row: ClassVar[dict] = {
        "date": "2024-05",
        "org": "Team",
        "name": "Example",
        "kind": "预览",
        "note": "首次公开预览。",
        "url": "https://example.org/announce",
        "source": "发布公告",
    }

    def test_reviewed_issue_and_duplicate(self):
        body = "### 发布记录\r\n\r\n```json\r\n" + json.dumps(self.row) + "\r\n```\r\n"
        row = catalog.reviewed_row(body)
        existing = [dict(row, date="2024-06", kind="开放权重")]
        new = catalog.incorporate(existing, row)
        self.assertEqual(new, [row, *existing])
        with self.assertRaises(ValueError):
            catalog.incorporate(new, row)

    def test_unverified_or_invalid_input_does_not_publish(self):
        for changes in (
            {"note": ""},
            {"date": "2024-02-31"},
            {"url": "javascript:alert(1)"},
            {"kind": "以后发布"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                catalog.check_record(dict(self.row, **changes))
