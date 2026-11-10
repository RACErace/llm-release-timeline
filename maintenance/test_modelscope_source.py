import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import collector
import sources


SOURCE = {"id": "modelscope:Qwen", "channel": "modelscope", "org": "阿里巴巴", "owner": "Qwen"}
SINCE = "2026-11-10T06:30:00+00:00"
CUTOFF = "2026-11-09T06:30:00+00:00"
OLDER = "2026-11-09T06:29:59+00:00"


def openapi_model(name, stamp=SINCE, gated=False):
    return {
        "id": "Qwen/" + name, "last_modified": stamp, "gated": gated,
        "tasks": ["text-generation"], "tags": ["library:safetensors"],
    }


def sdk_model(name, stamp=SINCE, gated=False):
    return {
        "Path": "Qwen", "Name": name,
        "LastUpdatedTime": int(datetime.fromisoformat(stamp).timestamp()),
        "Visibility": 1 if gated else 5, "ProtectedMode": 1 if gated else 2,
        "IsAccessible": 1, "ApprovalMode": 1,
        "Tasks": [{"Name": "text-generation"}], "Libraries": ["safetensors"],
    }


def openapi_page(rows, total):
    return Mock(**{"json.return_value": {"data": {"models": rows, "total_count": total}}})


def sdk_page(rows, total):
    payload = {"Code": 200, "Success": True, "Data": {"Models": rows, "TotalCount": total}}
    return Mock(**{"json.return_value": payload})


def http_error(status):
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(f"HTTP {status}", response=response)


class ModelScopeTests(unittest.TestCase):
    def test_healthy_openapi_uses_only_primary_listing(self):
        page = openapi_page([openapi_model("Example")], 1)
        with patch.object(sources, "request", return_value=page) as request:
            rows = sources.read_source(SOURCE, SINCE)
        request.assert_called_once()
        self.assertEqual(request.call_args.args, ("GET", "https://modelscope.cn/openapi/v1/models"))
        self.assertEqual(list(rows), [sources.key(SOURCE["id"], "Qwen/Example|开放权重")])

    def test_second_page_failure_restarts_from_sdk_with_the_same_evidence_identity(self):
        first = [openapi_model(f"Discarded-{i}") for i in range(50)]
        older = [sdk_model(f"Older-{i}", OLDER) for i in range(499)]
        responses = [
            openapi_page(first, 51), http_error(503),
            sdk_page([sdk_model("Example"), *older], 501),
            sdk_page([sdk_model("Restricted", CUTOFF, gated=True)], 501),
        ]
        with patch.object(sources, "request", side_effect=responses) as request:
            rows = sources.read_source(SOURCE, SINCE)

        calls = request.call_args_list
        self.assertEqual([c.args[0] for c in calls], ["GET", "GET", "PUT", "PUT"])
        self.assertEqual([c.kwargs["params"]["page_number"] for c in calls[:2]], [1, 2])
        for number, call in enumerate(calls[2:], start=1):
            self.assertEqual(call.args[1], "https://modelscope.cn/api/v1/models/")
            self.assertEqual(call.kwargs["json"]["PageNumber"], number)
            self.assertEqual(call.kwargs["json"]["Path"], "Qwen")

        primary = [openapi_model("Example"), openapi_model("Restricted", CUTOFF, gated=True)]
        with patch.object(sources, "request", return_value=openapi_page(primary, 2)):
            self.assertEqual(rows, sources.read_source(SOURCE, SINCE))
        self.assertEqual(
            {(r["name"], r["kind"], r["upstream_updated"]) for r in rows.values()},
            {("Example", "开放权重", SINCE), ("Restricted", "受限开放", CUTOFF)},
        )

    def test_unavailable_or_inconsistent_sdk_inventory_keeps_checkpoint(self):
        row = sdk_model("Example")
        cases = {
            "both endpoints unavailable": http_error(502),
            "duplicate repository": sdk_page([row, row], 2),
            "incorrect total count": sdk_page([row, sdk_model("Another")], 1),
        }
        for scenario, fallback in cases.items():
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "state.json"
                state = collector.state_at(path)
                state["sources"][SOURCE["id"]] = {"checked": SINCE, "baseline": CUTOFF, "seen": ["saved"]}
                collector.save(path, state)
                with (
                    patch.object(collector, "registry", return_value=[SOURCE]),
                    patch.object(sources, "request", side_effect=[http_error(503), fallback]) as request,
                    patch.object(collector, "report"),
                ):
                    self.assertTrue(collector.scan(path))
                self.assertEqual([c.args[0] for c in request.call_args_list], ["GET", "PUT"])
                self.assertEqual(collector.state_at(path), state)

    def test_forbidden_and_rate_limited_requests_do_not_switch_endpoints(self):
        for status in (403, 429):
            with self.subTest(status=status):
                error = http_error(status)
                with patch.object(sources, "request", side_effect=error) as request:
                    with self.assertRaises(requests.HTTPError) as raised:
                        sources.read_source(SOURCE, SINCE)
                self.assertIs(raised.exception, error)
                request.assert_called_once()
