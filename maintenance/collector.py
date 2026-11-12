"""Scan first-party sources into a durable outbox, then reconcile candidate Issues."""

import argparse
import json
import os
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path

from anthropic_releases import catalog_name as anthropic_catalog_name
from github_api import LABEL, api
from model_scope import announcement_in_scope, paper_url
from sources import (
    HERE,
    checked_model,
    key,
    lookup_hub_model,
    read_source,
    registry,
)

FAMILY = re.compile(
    r"\b(?:GPT[- ]?\d+(?:\.\d+)*|Claude[- ](?:Opus|Sonnet|Haiku|Fable)[- ]\d+(?:\.\d+)*|Gemini[- ]\d+(?:\.\d+)*|Kimi[- ]K\d+(?:\.\d+)*|DeepSeek[- ]V\d+(?:\.\d+)*|Qwen[- ]?\d+(?:\.\d+)*)(?:[- ](?:Pro|Flash|Cyber|Thinking|Highspeed|Lite|Base|Instant|Mini|[\d.]+[BMT]|\d{4}))*\b",
    re.IGNORECASE,
)


def normalized(text, *, hub=False):
    text = unicodedata.normalize("NFKC", text).casefold()
    if hub:
        text = re.sub(r"(?<=\d)_(?=\d)", ".", text)
    return re.sub(r"[^\w.]+|_", "", text)


def model_name(row):
    if row.get("model"):
        return normalized(row["model"], hub=True)
    names = {normalized(n) for n in FAMILY.findall(row["name"])}
    return names.pop() if len(names) == 1 else ""


def claude_launch(row):
    # Newsroom and Platform Docs can post the same version on different days.
    return (
        row["source"] in {"claude", "claude-news"}
        and row.get("model")
        and row["kind"] in {"发布", "预览"}
    )


def event_id(row):
    model = model_name(row)
    if not model:
        return key(row["org"], row["url"], row["name"], row["kind"])
    when = "" if row["kind"] in {"开放权重", "受限开放"} or claude_launch(row) else row["date"]
    return key(row["org"], model, row["kind"], when)


def already_catalogued(row, records):
    model = model_name(row)
    for record in records:
        if record["org"] != row["org"]:
            continue
        name, kind = record["name"], record["kind"]
        if row["source"] == "meta-research" and row["kind"] == "发布":
            # The index headline often omits the access stage verified from the article.
            # Require the same dated announcement and the complete model name.
            if (
                kind in {"发布", "开放 API", "开放权重", "受限开放"}
                and row["date"]
                and row["date"] == record["date"]
                and row["url"] in (record["url"], record.get("url2"))
                and re.search(
                    r"(?<![\w-])" + re.escape(unicodedata.normalize("NFKC", name))
                    + r"(?=\s*(?:$|[:,;!?()–—]|\.(?!\d))|\s+(?:and|is|are|for|from|in|on|with|now)\b)",
                    unicodedata.normalize("NFKC", row["name"]),
                    re.I,
                )
            ):
                return True
            continue
        if row["source"] in {"claude", "claude-news"}:
            # Earlier announcements used "Claude 3.5 Sonnet" and grouped
            # "Claude 4：Opus 4 / Sonnet 4" on the same catalog row.
            name = anthropic_catalog_name(name)
            if kind == "发布" and name.endswith("（更新版）"):
                name, kind = name.removesuffix("（更新版）"), "版本更新"
        if kind != row["kind"]:
            continue
        if row["date"] and row["date"] != record["date"] and not claude_launch(row):
            continue
        if model and model == normalized(name, hub=bool(row.get("model"))):
            return True
        if (
            row["date"]
            and row["date"] == record["date"]
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
    if "bootstrap_days" in source:
        # An announcement dated in the future may be visible on the index early.
        # Leave it out of seen until its publication day.
        snapshot = {k: row for k, row in snapshot.items() if row["date"] <= checked[:10]}
    old = state["sources"].get(source["id"])
    seen = set(old["seen"]) if old else set()
    baseline = old.get("baseline", old["checked"]) if old else checked
    # New publisher indexes may already contain yesterday's launch. Opt in only
    # for sources with publisher-supplied dates; baseline older entries silently.
    bootstrap_from = None
    if not old and "bootstrap_days" in source:
        today = datetime.fromisoformat(checked).date()
        bootstrap_from = (today - timedelta(days=source["bootstrap_days"])).isoformat()
    added = 0
    for ident, item in snapshot.items():
        if excluded_evidence(item):
            continue
        if ident in seen:
            continue
        if not old and not (
            bootstrap_from and bootstrap_from <= item["date"] <= checked[:10]
        ):
            continue
        # A busy Hub may return older items on another page of the overlap window.
        updated = item.get("upstream_updated")
        if updated and datetime.fromisoformat(updated) <= datetime.fromisoformat(
            baseline
        ):
            continue
        if already_catalogued(item, records):
            continue
        group = event_id(item)
        state["pending"].setdefault(group, {})[ident] = item
        added += 1
    state["sources"][source["id"]] = {
        "checked": checked,
        "baseline": baseline,
        "seen": sorted(seen | snapshot.keys()),
    }
    return added


def scan(path):
    state = state_at(path)
    state["sources"].pop("arxiv-cl", None)
    records = json.loads((HERE.parent / "data/releases.json").read_text("utf-8"))
    started = datetime.now(UTC).isoformat(timespec="seconds")
    sources = registry()
    errors = []
    good = baseline = added = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = {}
        for source in sources:
            checkpoint = state["sources"].get(source["id"], {})
            since = checkpoint.get("checked")
            job = pool.submit(read_source, source, since, set(checkpoint.get("seen", ())))
            jobs[job] = source
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
        "source": (
            "官方模型仓库"
            if first["source"].startswith(("huggingface:", "modelscope:"))
            else "发布方原文"
        ),
    }
    excerpt = safe_text(first["excerpt"])
    return (
        f"<!-- atlas-candidate:{group} -->\n待核实的发布线索：\n\n"
        + "\n".join(evidence_text(x) for x in items)
        + (f"\n\n{excerpt}" if excerpt else "")
        + "\n\n请补充原开发方关于所属基座或官方版本、发布日期和开放范围的一手依据。\n\n"
        + "```json\n"
        + json.dumps(proposal, ensure_ascii=False, indent=2).replace("`", "\\u0060")
        + "\n```"
    )


def excluded_evidence(item):
    return (
        item["source"] == "arxiv-cl" or "arxiv" in item or paper_url(item["url"])
        or not announcement_in_scope(item.get("model") or item["name"])
    )


def eligible_pending(state):
    ready = []
    incomplete = False
    for group, items in list(state["pending"].items()):
        waiting = False
        for ident, item in list(items.items()):
            if excluded_evidence(item):
                del items[ident]
                continue
            channel, _, owner = item["source"].partition(":")
            if channel not in {"modelscope", "huggingface"} or item.get("base_evidence"):
                continue
            # Queued by an older collector. Re-read its publisher evidence
            # before it can create an Issue or append to an existing one.
            try:
                checked = lookup_hub_model(owner + "/" + item["model"], channel)
            except Exception as exc:  # isolate the upstream; retain this group for retry
                report(f"旧候选核验暂未完成，保留待重试：{item['source']} — {exc!s}")
                waiting = incomplete = True
                continue
            if checked and checked["id"] == ident:
                items[ident] = checked
            else:
                del items[ident]
        if not items:
            del state["pending"][group]
        elif not waiting:
            ready.append((group, items))
    return ready, incomplete


def deliver(path, repo, limit):
    state = state_at(path)
    ready, incomplete = eligible_pending(state)
    save(path, state)
    if not ready:
        report("没有可投递的候选。")
        return incomplete
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
    for group, candidates in ready[:limit]:
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
                + items[0]["org"]
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
    return incomplete


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
