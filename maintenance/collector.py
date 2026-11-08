"""Scan first-party sources into a durable outbox, then reconcile candidate Issues."""

import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from sources import HERE, checked_model, key, read_source, registry

LABEL = "release-candidate"
FAMILY = re.compile(
    r"\b(?:GPT[- ]?\d+(?:\.\d+)*|Claude[- ](?:Opus|Sonnet|Haiku|Fable)[- ]\d+(?:\.\d+)*|Gemini[- ]\d+(?:\.\d+)*|Kimi[- ]K\d+(?:\.\d+)*|DeepSeek[- ]V\d+(?:\.\d+)*|Qwen[- ]?\d+(?:\.\d+)*)(?:[- ](?:Pro|Flash|Cyber|Thinking|Highspeed|Lite|Base|Instant|Mini|[\d.]+[BMT]|\d{4}))*\b",
    re.IGNORECASE,
)


def normalized(text):
    return re.sub(r"[^\w.]+|_", "", unicodedata.normalize("NFKC", text).casefold())


def model_name(row):
    if row.get("model"):
        return normalized(row["model"])
    names = {normalized(n) for n in FAMILY.findall(row["name"])}
    return names.pop() if len(names) == 1 else ""


def event_id(row):
    model = model_name(row)
    if not model:
        return key(row["org"], row["url"], row["name"], row["kind"])
    when = "" if row["kind"] in {"开放权重", "受限开放"} else row["date"]
    return key(row["org"], model, row["kind"], when)


def already_catalogued(row, records):
    model = model_name(row)
    for record in records:
        if record["org"] != row["org"] or record["kind"] != row["kind"]:
            continue
        if row["date"] and row["date"] != record["date"]:
            continue
        if model and model == normalized(record["name"]):
            return True
        if (
            row["date"]
            and row["url"] in (record["url"], record.get("url2"))
            and normalized(record["name"]) in normalized(row["name"])
        ):
            return True
    return False


def state_at(path):
    if not path.exists():
        return {"version": 1, "sources": {}, "pending": {}, "delivered": {}}
    state = json.loads(path.read_text("utf-8"))
    if state.get("version") != 1 or not all(
        isinstance(state.get(k), dict) for k in ("sources", "pending", "delivered")
    ):
        raise ValueError("unrecognized collector state")
    return state


def save(path, state):
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def report(message):
    print(message, flush=True)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as f:
            f.write(message + "\n\n")


def accept_snapshot(state, source, snapshot, checked, records):
    old = state["sources"].get(source["id"])
    seen = set(old["seen"]) if old else set()
    added = 0
    if old:
        for ident, item in snapshot.items():
            if ident in seen or already_catalogued(item, records):
                continue
            group = event_id(item)
            state["pending"].setdefault(group, {})[ident] = item
            added += 1
    state["sources"][source["id"]] = {
        "checked": checked,
        "seen": sorted(seen | snapshot.keys()),
    }
    return added


def scan(path):
    state = state_at(path)
    records = json.loads((HERE.parent / "data/releases.json").read_text("utf-8"))
    started = datetime.now(UTC).isoformat(timespec="seconds")
    sources = registry()
    errors = []
    good = baseline = added = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = {}
        for source in sources:
            since = state["sources"].get(source["id"], {}).get("checked")
            if (
                since
                and source.get("daily")
                and (since[:10] == started[:10] or started[11:13] < "04")
            ):
                continue
            jobs[pool.submit(read_source, source, since)] = source
        for job in as_completed(jobs):
            source = jobs[job]
            try:
                snapshot = job.result()
                baseline += source["id"] not in state["sources"]
                added += accept_snapshot(state, source, snapshot, started, records)
                good += 1
            except Exception as exc:  # noqa: BLE001 - isolate each upstream; failed sources retain their checkpoint.
                errors.append(source["id"] + " — " + str(exc)[:220])
    save(path, state)
    report(
        f"采集成功 {good}/{len(jobs)} 个来源，首次建立基线 {baseline} 个，新增线索 {added} 条，等待投递 {len(state['pending'])} 组。"
    )
    for error in sorted(errors):
        report("采集失败，保留上次成功进度：" + error)
    return bool(errors)


def recheck(path, model):
    item = checked_model(model)
    state = state_at(path)
    records = json.loads((HERE.parent / "data/releases.json").read_text("utf-8"))
    group = event_id(item)
    if group in state["delivered"] or already_catalogued(item, records):
        report("该事件已投递或已收录。")
        return
    state["pending"].setdefault(group, {})[item["id"]] = item
    save(path, state)
    report("已从官方模型库读取指定候选：" + item["name"])


def api(endpoint, data=None, paginate=False):
    cmd = ["gh", "api", endpoint]
    if paginate:
        cmd.extend(["--paginate", "--slurp"])
    if data is not None:
        cmd.extend(["--input", "-"])
    result = subprocess.run(
        cmd,
        check=False,
        input=json.dumps(data, ensure_ascii=False) if data is not None else None,
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[:500])
    value = json.loads(result.stdout)
    return [item for page in value for item in page] if paginate else value


def safe_text(value):
    # Prevent feed content from mentioning users or injecting GitHub metadata and fences.
    return (
        value.replace("@", "＠")
        .replace("<", "＜")
        .replace(">", "＞")
        .replace("`", "｀")
        .replace("\n", " ")
    )


def evidence_text(item):
    return f"- {safe_text(item['source'])} · {safe_text(item['name'])}\n  <{item['url'].replace('>', '%3E').replace('<', '%3C')}>\n  <!-- atlas-evidence:{item['id']} -->"


def issue_body(group, items):
    first = items[0]
    proposal = {
        "date": first["date"],
        "org": first["org"],
        "name": safe_text(first.get("model") or first["name"]),
        "kind": first["kind"],
        "note": "",
        "url": first["url"],
        "source": "官方模型仓库" if first.get("model") else "发布方原文",
    }
    excerpt = safe_text(first["excerpt"])
    return (
        f"<!-- atlas-candidate:{group} -->\n这是一条待核实的公开消息。请打开原文核对模型名、发布阶段及事件日期，补写一句中文说明。\n\n"
        + "\n".join(evidence_text(x) for x in items)
        + (f"\n\n原文片段：{excerpt}" if excerpt else "")
        + "\n\n编辑下方 JSON 后，添加 `prepare-release` 标签即可生成收录分支。对应的 PR 合并后网页会更新。已有记录或无关消息可直接关闭。\n\n"
        + "```json\n"
        + json.dumps(proposal, ensure_ascii=False, indent=2).replace("`", "\\u0060")
        + "\n```"
    )


def deliver(path, repo, limit):
    state = state_at(path)
    if not state["pending"]:
        report("没有待投递的候选。")
        return False
    endpoint = "repos/" + repo + "/issues"
    # Include closed and unlabelled Issues so a retry after a remote write is idempotent.
    issues = api(endpoint + "?state=all&per_page=100", paginate=True)
    existing = {}
    for issue in issues:
        match = re.search(
            r"<!-- atlas-candidate:([a-f0-9]{32}) -->", issue.get("body") or ""
        )
        if (
            match
            and "pull_request" not in issue
            and issue["user"]["login"] == "github-actions[bot]"
        ):
            existing[match[1]] = issue
    count = 0
    for group, candidates in list(state["pending"].items())[:limit]:
        items = list(candidates.values())
        receipt = state["delivered"].get(group)
        issue = existing.get(group)
        if not issue and receipt:
            issue = api(endpoint + "/" + str(receipt))
        if issue:
            if issue["state"] == "open":
                body = issue.get("body") or ""
                if issue["comments"]:
                    body += "\n".join(
                        c["body"]
                        for c in api(
                            endpoint + f"/{issue['number']}/comments?per_page=100",
                            paginate=True,
                        )
                        if c["user"]["login"] == "github-actions[bot]"
                    )
                new = [
                    item for item in items if "atlas-evidence:" + item["id"] not in body
                ]
                if new:
                    api(
                        endpoint + f"/{issue['number']}/comments",
                        {
                            "body": "补充来源\n\n"
                            + "\n".join(evidence_text(x) for x in new)
                        },
                    )
        else:
            title = (
                "待核实 · "
                + (items[0]["org"] or "研究论文")
                + " · "
                + safe_text(items[0]["name"])
            )
            issue = api(
                endpoint,
                {
                    "title": title[:220],
                    "body": issue_body(group, items),
                    "labels": [LABEL],
                },
            )
        state["delivered"][group] = issue["number"]
        del state["pending"][group]
        save(path, state)
        count += 1
    report(
        f"已核对并投递 {count} 组候选，剩余 {len(state['pending'])} 组随下次运行处理。"
    )
    return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["scan", "deliver", "recheck"])
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--model")
    args = parser.parse_args()
    if args.mode == "deliver" and not args.repo:
        parser.error("--repo is required for delivery")
    if args.mode == "scan":
        sys.exit(scan(args.state))
    if args.mode == "recheck":
        if not args.model:
            parser.error("--model is required for recheck")
        recheck(args.state, args.model)
    else:
        sys.exit(deliver(args.state, args.repo, args.limit))
