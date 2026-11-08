import json
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import Mock, patch

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
