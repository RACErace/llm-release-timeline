import json
import unittest
from unittest.mock import patch

import agent


def record(name, day):
    return {
        "date": day,
        "org": "Lab",
        "name": name,
        "kind": "发布",
        "note": "公开发布。",
        "source": "实验室公告",
        "url": "https://example.org/release",
    }


class AgentTests(unittest.TestCase):
    def test_expected_files_preserve_old_records_and_only_change_stamp(self):
        old = record("A", "2024-02-01")
        new = record("B", "2024-01-01")
        html = '<main><time id="updated" datetime="2024-02-02">2024.02.02</time></main>'
        files = agent.release_files(json.dumps([old]), html, new, "2024-03-01")
        self.assertEqual(json.loads(files[agent.DATA]), [new, old])
        self.assertEqual(
            files[agent.INDEX], html.replace("02-02", "03-01").replace("02.02", "03.01")
        )
        with self.assertRaisesRegex(ValueError, "相同记录"):
            agent.release_files(
                files[agent.DATA], files[agent.INDEX], new, "2024-03-01"
            )
        with self.assertRaisesRegex(ValueError, "日期"):
            agent.release_files(json.dumps([old]), html, new, "2024-01-01")

    def test_whole_tree_mismatch_rejects_extra_code_or_metadata(self):
        html = '<time id="updated" datetime="2024-03-01">2024.03.01</time>'
        row = record("B", "2024-02-01")
        head = {"sha": "reviewed", "tree": {"sha": "changed-mode-or-extra-file"}}
        with (
            patch.object(agent, "contents", side_effect=[json.dumps([row]), html]),
            patch.object(agent, "release_tree", return_value="one-approved-entry"),
            self.assertRaisesRegex(ValueError, "完整文件树"),
        ):
            agent.verify_tree("main", head, row)

    def test_verify_tree_accepts_reordered_receipt_but_checks_exact_fields(self):
        row = record("B", "2024-02-01")
        restored = dict(sorted(row.items()))
        self.assertNotEqual(list(row), list(restored))
        html = '<time id="updated" datetime="2024-03-01">2024.03.01</time>'
        head = {"sha": "reviewed", "tree": {"sha": "original"}}

        def build(base, approved, stamp):
            self.assertEqual(
                (base, list(approved.items()), stamp),
                ("main", list(row.items()), "2024-03-01"),
            )
            return "original"

        with (
            patch.object(agent, "contents", side_effect=[json.dumps([row]), html]),
            patch.object(agent, "release_tree", side_effect=build),
        ):
            authenticated = agent.verify_tree("main", head, restored)
            self.assertEqual(list(authenticated.items()), list(row.items()))
        for changed in ([dict(row, note="not the reviewed text")], [row, row]):
            with (
                self.subTest(changed=changed),
                patch.object(agent, "contents", return_value=json.dumps(changed)),
                self.assertRaisesRegex(ValueError, "已核实字段"),
            ):
                agent.verify_tree("main", head, restored)

    def test_ci_must_belong_to_pr_and_latest_attempt_must_pass(self):
        pr = {"number": 5, "head": {"sha": "reviewed"}}
        run = {
            "id": 24,
            "run_number": 12,
            "run_attempt": 1,
            "head_sha": "reviewed",
            "html_url": "https://github.com/o/r/actions/runs/24",
            "pull_requests": [],
            "conclusion": "success",
        }
        with patch.object(agent, "github", return_value={"workflow_runs": [run]}):
            self.assertFalse(agent.validated(pr, []))
        check = {
            "name": "validate",
            "detailsUrl": run["html_url"] + "/job/3",
            "conclusion": "SUCCESS",
        }
        with patch.object(
            agent,
            "github",
            side_effect=[
                {"workflow_runs": [run]},
                {"jobs": [{"name": "validate", "conclusion": "success"}]},
            ],
        ):
            self.assertTrue(agent.validated(pr, [check]))
        retry = dict(run, run_attempt=2, conclusion="failure")
        with patch.object(
            agent, "github", return_value={"workflow_runs": [run, retry]}
        ):
            self.assertFalse(agent.validated(pr, [check]))

    def test_stale_head_and_open_review_cannot_merge(self):
        with patch.object(
            agent, "github", return_value={"head": {"sha": "new"}}
        ) as remote:
            with self.assertRaisesRegex(ValueError, "已审查"):
                agent.merge(5, "old", {})
            self.assertEqual(remote.call_count, 1)
        with self.assertRaisesRegex(ValueError, "审查"):
            agent.check_reviews(
                {
                    "reviewDecision": "",
                    "latestReviews": [{"state": "CHANGES_REQUESTED"}],
                }
            )

    def test_held_candidates_are_ignored(self):
        item = {
            "state": "open",
            "labels": [{"name": agent.LABEL}, {"name": agent.HOLD}],
        }
        with (
            patch.object(agent, "github", return_value=item),
            self.assertRaisesRegex(ValueError, "未暂停"),
        ):
            agent.candidate(3)

    def test_repeated_hold_reuses_own_comment(self):
        note = "请补充有日期的发布方原文。"
        item = {
            "state": "open",
            "html_url": "issue-url",
            "labels": [{"name": agent.HOLD}],
        }
        comment = {
            "user": {"login": "maintainer"},
            "body": "<!-- atlas-agent:needs-human -->\n维护 Agent 需要人工确认。\n\n"
            + note,
        }
        with (
            patch.object(agent, "api", return_value={"login": "maintainer"}),
            patch.object(agent, "github", side_effect=[item, [comment]]) as remote,
        ):
            self.assertEqual(agent.hold(3, note), "issue-url")
            self.assertEqual(remote.call_count, 2)
