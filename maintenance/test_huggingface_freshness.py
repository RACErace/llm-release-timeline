import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import collector
import sources


HUB = {
    "id": "huggingface:mistralai", "org": "Mistral", "owner": "mistralai",
    "channel": "huggingface",
}
SINCE = "2026-11-24T08:06:00+00:00"
CHECKED = "2026-11-24T09:03:00+00:00"
RECENT = "2026-11-24T08:50:43Z"
OLD = "2025-12-02T09:00:00Z"
HEAD = "1" * 40
PRIOR = "2" * 40
CARD = "A causal decoder-only model trained on a causal language modeling task."
TEMPLATE = {"rfilename": "chat_template.jinja"}
WEIGHT = {"rfilename": "model-00001-of-00002.safetensors"}
REPO = "mistralai/Mistral-7B-Instruct-v0.1"


def model(repo=REPO, **changes):
    return dict({
        "id": repo, "pipeline_tag": "text-generation", "lastModified": RECENT,
        "createdAt": OLD, "sha": HEAD, "siblings": [TEMPLATE, WEIGHT],
    }, **changes)


def response(payload):
    rv = Mock(links={})
    rv.json.return_value = payload
    return rv


def history():
    # The title is not trusted: a publisher may call a metadata edit a release.
    return response([
        {"id": HEAD, "date": RECENT, "title": "Release: new chat template"},
        {"id": PRIOR, "date": OLD, "title": "Old commit"},
    ])


class HuggingFaceFreshnessTests(unittest.TestCase):
    def snapshot(self, row, *evidence, seen=None):
        with (
            patch.object(sources, "request", side_effect=[response([row]), *evidence]) as request,
            patch.object(sources, "model_card", return_value=CARD) as card,
        ):
            snapshot = sources.read_source(HUB, SINCE, seen or set())
        return snapshot, request, card

    def test_template_only_update_of_old_unseen_model_never_creates_issue(self):
        snapshot, request, card = self.snapshot(
            model(), history(), response({"sha": PRIOR, "siblings": [WEIGHT]})
        )
        self.assertFalse(snapshot)
        self.assertEqual(request.call_count, 3)
        card.assert_not_called()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = collector.state_at(path)
            state["sources"][HUB["id"]] = {"checked": SINCE, "seen": []}
            self.assertEqual(collector.accept_snapshot(state, HUB, snapshot, CHECKED, []), 0)
            collector.save(path, state)
            with patch.object(collector, "api") as github:
                self.assertFalse(collector.deliver(path, "owner/repo", 10))
                github.assert_not_called()

    def test_old_empty_repo_first_upload_establishes_new_weights(self):
        snapshot, request, card = self.snapshot(
            model("mistralai/New-Model"), history(),
            response({"sha": PRIOR, "siblings": [TEMPLATE, {"rfilename": "adapter_model.safetensors"}]})
        )
        self.assertEqual([x["kind"] for x in snapshot.values()], ["开放权重"])
        self.assertEqual(request.call_count, 3)
        card.assert_called_once()
        state = {"sources": {HUB["id"]: {"checked": SINCE, "seen": []}}, "pending": {}}
        self.assertEqual(collector.accept_snapshot(state, HUB, snapshot, CHECKED, []), 1)
        self.assertEqual(collector.accept_snapshot(state, HUB, snapshot, CHECKED, []), 0)
        self.assertEqual(len(state["pending"]), 1)

    def test_new_repo_uses_creation_time_and_needs_no_git_history(self):
        snapshot, request, _ = self.snapshot(model("mistralai/New-Model", createdAt=RECENT))
        self.assertEqual(len(snapshot), 1)
        self.assertEqual(request.call_count, 1)

    def test_known_restricted_model_became_open(self):
        gated = sources.hub_signal(HUB, model(gated="manual"))
        snapshot, request, _ = self.snapshot(model(), seen={gated["id"]})
        self.assertEqual([x["kind"] for x in snapshot.values()], ["开放权重"])
        self.assertEqual(request.call_count, 1)

    def test_absent_history_is_not_positive_evidence(self):
        missing = requests.HTTPError(response=Mock(status_code=404))
        snapshot, _, _ = self.snapshot(model(), missing)
        self.assertFalse(snapshot)
        snapshot, _, _ = self.snapshot(model(sha=None))
        self.assertFalse(snapshot)
        snapshot, _, _ = self.snapshot(model(), response([{"id": HEAD, "date": RECENT}]))
        self.assertFalse(snapshot)

    def test_failed_history_read_does_not_advance_source_checkpoint(self):
        failed = requests.HTTPError(response=Mock(status_code=503))
        with self.assertRaises(requests.HTTPError):
            self.snapshot(model(), failed)


if __name__ == "__main__":
    unittest.main()
