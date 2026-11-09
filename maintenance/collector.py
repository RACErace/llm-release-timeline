"""Scan first-party sources into a durable outbox, then reconcile candidate Issues."""

import argparse
import json
import os
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from github_api import LABEL, api
from sources import (
    HERE,
    arxiv_abstracts,
    arxiv_announcement,
    checked_model,
    key,
    paper_signal,
    read_source,
    registry,
)

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
    if row["source"] == "arxiv-cl":
        # A later revision can disclose another model or new weights after
        # the Issue for the original paper has already been closed.
        return row["id"]
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


def previous_paper_artifacts(checkpoint, snapshot, seen):
    artifacts = dict(checkpoint.get("paper_artifacts", {}))
    requests = {}
    for item in snapshot.values():
        paper, url = item["arxiv"], item["url"]
        if (
            url not in artifacts
            and key(item["source"], url) in seen
            and paper["type"].startswith("replace")
            and paper["version"] > 1
            and paper["date"] > checkpoint["checked"][:10]
        ):
            # A paper last observed under the URL-only collector has returned
            # on a later day. Compare the preceding version, even if its old
            # Issue is already closed. A missing abstract must retry the scan.
            requests[url + "v" + str(paper["version"] - 1)] = item
    if requests:
        previous = arxiv_abstracts(requests)
        if requests.keys() - previous.keys():
            raise ValueError("missing previous arXiv revision; retaining checkpoint")
        for link, (title, abstract) in previous.items():
            item = requests[link]
            prior = paper_signal(
                {"id": item["source"], "org": item["org"]},
                title,
                item["url"],
                abstract,
                "",
                "",
            )
            artifacts[item["url"]] = prior["arxiv"]["artifacts"] if prior else []
    return artifacts


def accept_snapshot(state, source, snapshot, checked, records):
    old = state["sources"].get(source["id"])
    seen = set(old["seen"]) if old else set()
    baseline = old.get("baseline", old["checked"]) if old else checked
    artifacts = (
        previous_paper_artifacts(old, snapshot, seen)
        if old and source.get("discovery")
        else {}
    )
    added = 0
    for ident, item in snapshot.items():
        if paper := item.get("arxiv"):
            url = item["url"]
            previous = artifacts.get(url)
            facts = set(paper["artifacts"])
            artifacts[url] = sorted(facts.union(previous or ()))
            # Older checkpoints only know the URL. Establish their evidence
            # baseline once; future replacements must add a disclosed fact.
            if previous is None and key(source["id"], url) in seen:
                continue
            if previous is not None and facts.issubset(previous):
                continue
            if previous is not None:
                paper["added_artifacts"] = sorted(facts.difference(previous))
        if not old or ident in seen:
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
    if source.get("discovery"):
        state["sources"][source["id"]]["paper_artifacts"] = artifacts
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
            checkpoint = state["sources"].get(source["id"], {})
            since = checkpoint.get("checked")
            if (
                since
                and source.get("daily")
                and (not source.get("discovery") or "paper_artifacts" in checkpoint)
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
    announcement = ""
    if paper := item.get("arxiv"):
        announcement = f"\n  arXiv 公告类型 {safe_text(paper['type']) or '未标明'}，公告日期 {safe_text(paper['date']) or '待核对'}。模型事件日期请另行核实。"
        if paper.get("version"):
            announcement += f" [当次论文版本]({item['url']}v{paper['version']})"
        if changes := paper.get("added_artifacts"):
            labels = {
                "model": "模型披露",
                "open-weights": "开放权重",
                "name": "名称",
                "size": "参数量",
                "variant": "变体",
            }
            facts = []
            for fact in changes:
                kind, _, value = fact.partition(":")
                facts.append((labels[kind] + " " + safe_text(value)).strip())
            announcement += " 本次摘要新增的核验线索：" + "、".join(facts) + "。"
    return f"- {safe_text(item['source'])} · {safe_text(item['name'])}\n  <{item['url'].replace('>', '%3E').replace('<', '%3C')}>{announcement}\n  <!-- atlas-evidence:{item['id']} -->"


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
        f"<!-- atlas-candidate:{group} -->\n待核实的发布线索：\n\n"
        + "\n".join(evidence_text(x) for x in items)
        + (f"\n\n{excerpt}" if excerpt else "")
        + "\n\n欢迎补充能确认模型名称、发布日期和开放范围的一手来源。\n\n"
        + "```json\n"
        + json.dumps(proposal, ensure_ascii=False, indent=2).replace("`", "\\u0060")
        + "\n```"
    )


def legacy_paper(item):
    return item["source"] == "arxiv-cl" and "arxiv" not in item


def refresh_pending_papers(state):
    legacy = [
        item
        for group in state["pending"].values()
        for item in group.values()
        if legacy_paper(item)
    ]
    if not legacy:
        return False
    revisions = {}
    for item in legacy:
        paper_id = item["url"].partition("/abs/")[2]
        version = re.search(
            r"arXiv:" + re.escape(paper_id) + r"v(\d+)\b", item["excerpt"]
        )
        number = int(version[1]) if version else 0
        revisions[item["id"]] = (item["url"] + (f"v{number}" if number else ""), number)
    try:
        urls = {link for link, _ in revisions.values()}
        papers = arxiv_abstracts(sorted(urls))
    except Exception as exc:  # noqa: BLE001 - keep old evidence for the next run when arXiv is unavailable.
        report(f"arXiv 摘要暂不可用，旧候选留待重试：{exc!s}")
        return True
    refreshed = removed = 0
    for group, items in list(state["pending"].items()):
        for ident, item in list(items.items()):
            if not legacy_paper(item):
                continue
            link, version = revisions[ident]
            if link not in papers:
                continue
            title, abstract = papers[link]
            updated = paper_signal(
                {"id": item["source"], "org": item["org"]},
                title,
                item["url"],
                abstract,
                item["date"],
                arxiv_announcement(item["excerpt"]),
                version,
            )
            if updated:
                # Retain existing Issue/evidence markers across interrupted
                # legacy deliveries, including a remote write before save().
                updated["id"] = ident
                items[ident] = updated
                checkpoint = state["sources"].get(item["source"])
                if checkpoint and "paper_artifacts" in checkpoint:
                    previous = checkpoint["paper_artifacts"].get(updated["url"], [])
                    checkpoint["paper_artifacts"][updated["url"]] = sorted(
                        set(previous).union(updated["arxiv"]["artifacts"])
                    )
                refreshed += 1
            else:
                del items[ident]
                removed += 1
        if not items:
            del state["pending"][group]
    missing = len(urls - papers.keys())
    report(
        f"旧 arXiv 线索重新核对：保留 {refreshed} 条模型研究，过滤 {removed} 条，缺少摘要 {missing} 条。"
    )
    return missing > 0


def deliver(path, repo, limit):
    state = state_at(path)
    incomplete = False
    if any(
        legacy_paper(item)
        for group in state["pending"].values()
        for item in group.values()
    ):
        incomplete = refresh_pending_papers(state)
        save(path, state)
    ready = [
        (group, items)
        for group, items in state["pending"].items()
        if not any(legacy_paper(item) for item in items.values())
    ]
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
