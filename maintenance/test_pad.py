import json
import unittest
from io import BytesIO
from unittest.mock import patch

import pad


class PadMaintainerTests(unittest.TestCase):
    def test_wrong_and_owner_identity_never_reach_repository(self):
        for actual in ("different", "RACErace"):
            with (
                patch.dict(
                    "os.environ",
                    {"GH_TOKEN": "dummy", "ATLAS_EXPECTED_LOGIN": "alternate"},
                ),
                patch.object(pad, "api", return_value={"login": actual}) as remote,
                self.assertRaisesRegex(ValueError, "备用维护账户"),
            ):
                pad.dispatch({"op": "hold", "number": 3, "reason": "Needs review"})
            self.assertEqual(remote.call_count, 1)

    def test_review_threads_read_second_page(self):
        def page(resolved, more):
            return {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": [{"isResolved": resolved}],
                                "pageInfo": {"hasNextPage": more, "endCursor": "next"},
                            }
                        }
                    }
                }
            }

        with patch.object(
            pad, "api", side_effect=[page(True, True), page(False, False)]
        ) as remote:
            self.assertEqual(
                pad.threads(4), [{"isResolved": True}, {"isResolved": False}]
            )
            self.assertEqual(remote.call_args.args[1]["variables"]["cursor"], "next")

    def test_closed_duplicate_requires_own_receipt_and_existing_catalogue_row(self):
        row = {"name": "A", "url": "https://example.org/a"}
        marker = "<!-- atlas-agent:duplicate:" + pad.digest(row) + " -->"
        closed = {"state": "closed", "html_url": "issue-url", "labels": []}

        def github(path):
            if path.startswith("git/ref/"):
                return {"object": {"sha": "main"}}
            return closed

        with (
            patch.object(pad.agent, "contents", return_value=json.dumps([row])),
            patch.object(pad.agent, "github", side_effect=github),
        ):
            with patch.object(
                pad,
                "pages",
                return_value=[{"user": {"login": "backup"}, "body": marker}],
            ):
                self.assertEqual(
                    pad.resolve_duplicate(9, row, "backup")["state"], "closed"
                )
            with (
                patch.object(
                    pad,
                    "pages",
                    return_value=[{"user": {"login": "outsider"}, "body": marker}],
                ),
                self.assertRaises(ValueError),
            ):
                pad.resolve_duplicate(9, row, "backup")

    def test_green_pages_requires_public_record(self):
        row = {"name": "A"}
        run = {
            "head_sha": "sha",
            "event": "push",
            "run_number": 1,
            "run_attempt": 1,
            "id": 1,
            "html_url": "run-url",
            "conclusion": "success",
        }

        def github(path, **_):
            return (
                [{"name": "deploy", "conclusion": "success"}]
                if "/jobs?" in path
                else {"workflow_runs": [run]}
            )

        with (
            patch.object(pad.agent, "github", side_effect=github),
            patch.object(pad.agent, "contents", return_value=json.dumps([row])),
        ):
            for live, ready in [([], False), ([row], True)]:
                with patch.object(
                    pad, "urlopen", return_value=BytesIO(json.dumps(live).encode())
                ):
                    self.assertIs(pad.verify_pages("sha", row)["ready"], ready)

    def test_long_comments_reassemble_verbatim(self):
        text = "a" * (pad.TEXT_SIZE + 1) + "末尾"
        parts = list(pad.chunks([{"id": 4, "body": text}]))
        self.assertEqual([p["offset"] for p in parts], [0, pad.TEXT_SIZE])
        self.assertEqual("".join(p["body"] for p in parts), text)
