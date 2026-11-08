"""Publish independently verified release records through ordinary GitHub pull requests."""

import argparse
import base64
import json
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

import catalog
from collector import LABEL, api

REPO = "RACErace/llm-release-timeline"
MAIN = "main"
HOLD = "needs-human"
DATA = "data/releases.json"
INDEX = "index.html"
BRANCH = "atlas-agent/issue-"


def github(path, *args, **kwargs):
    return api(f"repos/{REPO}/{path}", *args, **kwargs)


def labels(item):
    return {label["name"] for label in item["labels"]}


def queue():
    items = github(
        "issues?state=open&sort=created&direction=asc&per_page=100", paginate=True
    )
    runs = github("actions/workflows/pages.yml/runs?branch=main&per_page=100")[
        "workflow_runs"
    ]
    latest = next(
        (r for r in runs if r["event"] in {"push", "workflow_dispatch"}), None
    )
    return {
        "open": [
            {
                "type": "PR" if "pull_request" in item else "Issue",
                "number": item["number"],
                "title": item["title"],
                "labels": sorted(labels(item)),
                "updated": item["updated_at"],
            }
            for item in items
        ],
        "pages": {
            k: latest[k] for k in ("status", "conclusion", "head_sha", "html_url")
        }
        if latest
        else None,
    }


def candidate(number):
    issue = github(f"issues/{number}")
    if (
        "pull_request" in issue
        or issue["state"] != "open"
        or LABEL not in labels(issue)
        or HOLD in labels(issue)
    ):
        raise ValueError(f"#{number} 必须为开放且未暂停的 {LABEL} Issue")
    return issue


def contents(path, sha):
    file = github(f"contents/{path}?ref={sha}")
    if file["encoding"] != "base64":
        file = github("git/blobs/" + file["sha"])
    return base64.b64decode(file["content"]).decode("utf-8")


def stamp_of(html):
    (match,) = catalog.STAMP.findall(html)
    date.fromisoformat(match[1])
    if match[3] != match[1].replace("-", "."):
        raise ValueError("首页整理日期显示不一致")
    return match[1]


def release_files(data, html, row, stamp):
    rows = catalog.incorporate(json.loads(data), row)
    catalog.validate(rows)
    if not max(stamp_of(html), rows[-1]["date"]) <= stamp <= catalog.today():
        raise ValueError("首页整理日期超出范围")
    return {
        DATA: catalog.release_document(rows),
        INDEX: catalog.render_date(html, stamp),
    }


def release_tree(base_sha, row, stamp):
    """Rebuild the entire allowed Git tree from the trusted base; never check out a PR."""
    base = github("git/commits/" + base_sha)
    files = release_files(
        contents(DATA, base_sha), contents(INDEX, base_sha), row, stamp
    )
    return github(
        "git/trees",
        {
            "base_tree": base["tree"]["sha"],
            "tree": [
                {"path": path, "mode": "100644", "type": "blob", "content": content}
                for path, content in files.items()
            ],
        },
    )["sha"]


def verify_tree(base_sha, head, row):
    stamp = stamp_of(contents(INDEX, head["sha"]))
    if head["tree"]["sha"] != release_tree(base_sha, row, stamp):
        raise ValueError(
            "PR 与当前 main 加入这一条核实记录后的完整文件树不同，请检查变更或更新分支"
        )


def open_pr_state(pr):
    if pr["state"] != "open" or pr["draft"] or HOLD in labels(pr):
        raise ValueError("PR 已关闭、为草稿或已暂停")
    return json.loads(
        catalog.cmd(
            "gh",
            "pr",
            "view",
            str(pr["number"]),
            "--repo",
            REPO,
            "--json",
            "closingIssuesReferences,latestReviews,mergeStateStatus,reviewDecision,statusCheckRollup",
        )
    )


def check_reviews(state):
    if state["reviewDecision"] in {"CHANGES_REQUESTED", "REVIEW_REQUIRED"} or any(
        review["state"] == "CHANGES_REQUESTED" for review in state["latestReviews"]
    ):
        raise ValueError("有待处理的审查意见或必需的审查")


def propose(number, row):
    candidate(number)
    branch = BRANCH + str(number)
    ref = "refs/heads/" + branch
    query = urlencode(
        {"head": REPO.split("/")[0] + ":" + branch, "base": MAIN, "state": "all"}
    )
    pulls = github("pulls?" + query)
    pr = next((p for p in pulls if p["state"] == "open"), None)
    if pulls and not pr:
        raise ValueError("该 Issue 之前的 PR 已结束，请检查关闭原因")
    if pr:
        if pr["user"]["login"] != api("user")["login"]:
            raise ValueError("同名分支的 PR 由另一账户创建")
        check_reviews(open_pr_state(pr))
    base = github("git/ref/heads/" + MAIN)["object"]["sha"]
    refs = github("git/matching-refs/heads/" + branch)
    old = next((r["object"]["sha"] for r in refs if r["ref"] == ref), None)
    message = f"Add verified release from issue #{number}"
    if old:
        head = github("git/commits/" + old)
        if head["message"] != message or not head["parents"]:
            raise ValueError("维护分支已有其他提交，请人工检查")
        previous_base = head["parents"][0]["sha"]
        verify_tree(previous_base, head, row)
    if not old or previous_base != base:
        tree = release_tree(base, row, catalog.today())
        # Keeping the old head as a parent permits a normal fast-forward update.
        # Two concurrent proposals from the same ref cannot overwrite each other.
        commit = github(
            "git/commits",
            {
                "message": message,
                "tree": tree,
                "parents": [base, old] if old else [base],
            },
        )["sha"]
        if old:
            github(
                "git/refs/heads/" + branch,
                {"sha": commit, "force": False},
                method="PATCH",
            )
        else:
            github("git/refs", {"ref": ref, "sha": commit})
    if pr:
        return pr["html_url"]
    body = (
        f"Closes #{number}\n\n维护 Agent 已核对发布方原文，拟收录下列记录。\n\n"
        + "\n".join(f"- <{row[key]}>" for key in ("url", "url2") if key in row)
        + "\n\n```json\n"
        + json.dumps(row, ensure_ascii=False, indent=2)
        + "\n```"
    )
    return github(
        "pulls",
        {
            "head": branch,
            "base": MAIN,
            "title": f"收录 {row['name']}（{row['kind']}）",
            "body": body,
        },
    )["html_url"]


def validated(pr, checks):
    if any(
        (check.get("conclusion") or check.get("state"))
        not in {"SUCCESS", "SKIPPED", "NEUTRAL"}
        for check in checks
    ):
        return False
    sha = pr["head"]["sha"]
    query = urlencode({"event": "pull_request", "head_sha": sha, "per_page": 100})
    runs = github("actions/workflows/pages.yml/runs?" + query)["workflow_runs"]
    # Fork runs sometimes omit pull_requests. In that case bind the run to the
    # validate job URL GitHub itself reports on this PR, including the repository.
    check_urls = {
        check.get("detailsUrl", "")
        for check in checks
        if check.get("name") == "validate"
    }
    matches = [
        run
        for run in runs
        if run["head_sha"] == sha
        and (
            any(p["number"] == pr["number"] for p in run["pull_requests"])
            or any(url.startswith(run["html_url"] + "/job/") for url in check_urls)
        )
    ]
    if not matches:
        return False
    run = max(matches, key=lambda item: (item["run_number"], item["run_attempt"]))
    if run["conclusion"] != "success":
        return False
    jobs = github(f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs")[
        "jobs"
    ]
    return any(j["name"] == "validate" and j["conclusion"] == "success" for j in jobs)


def merge(number, sha, row, issue_number=None):
    pr = github(f"pulls/{number}")
    if pr["head"]["sha"] != sha or pr["base"]["ref"] != MAIN:
        raise ValueError("PR 当前提交或目标分支与已审查版本不符")
    if pr["merged"]:
        return pr["merge_commit_sha"]
    state = open_pr_state(pr)
    check_reviews(state)
    match = re.fullmatch(BRANCH + r"([1-9]\d*)", pr["head"]["ref"])
    linked = {i["url"] for i in state["closingIssuesReferences"]}
    if match and pr["head"]["repo"]["full_name"] == REPO:
        if issue_number and issue_number != int(match[1]):
            raise ValueError("所核实的 Issue 与维护分支不匹配")
        issue_number = int(match[1])
    if issue_number:
        issue = candidate(issue_number)
        if linked != {issue["html_url"]}:
            raise ValueError("自动收录 PR 只能关闭对应的候选 Issue")
    elif linked:
        raise ValueError("该投稿会关闭其他 Issue，请人工确认关联")
    if state["mergeStateStatus"] != "CLEAN" or not validated(
        pr, state["statusCheckRollup"]
    ):
        raise ValueError("请等待此 PR 当前提交的 validate 成功并解决合并阻塞")
    base = github("git/ref/heads/" + MAIN)["object"]["sha"]
    verify_tree(base, github("git/commits/" + sha), row)
    if github("git/ref/heads/" + MAIN)["object"]["sha"] != base:
        raise ValueError("main 已有新提交，请重新核对")
    if HOLD in labels(github(f"issues/{number}")):
        raise ValueError("PR 已暂停")
    if issue_number:
        candidate(issue_number)
    result = github(
        f"pulls/{number}/merge",
        {
            "sha": sha,
            "merge_method": "squash",
            "commit_title": f"Add verified timeline entry (#{number})",
            "commit_message": "",
        },
        method="PUT",
    )
    if not result.get("merged"):
        raise ValueError(result.get("message", "GitHub 未确认合并"))
    return result["sha"]


def hold(number, reason):
    item = github(f"issues/{number}")
    if item["state"] != "open" or not reason.strip():
        raise ValueError("请为开放的 Issue 或 PR 填写具体的待确认问题")
    marker = "<!-- atlas-agent:needs-human -->"
    body = f"{marker}\n维护 Agent 需要人工确认。\n\n{reason.strip()}"
    login = api("user")["login"]
    path = f"issues/{number}/comments"
    comments = github(path + "?per_page=100", paginate=True)
    previous = next(
        (
            c
            for c in comments
            if c["user"]["login"] == login and (c["body"] or "").startswith(marker)
        ),
        None,
    )
    if not previous:
        github(path, {"body": body})
    elif previous["body"] != body:
        github(f"issues/comments/{previous['id']}", {"body": body}, method="PATCH")
    if HOLD not in labels(item):
        github(f"issues/{number}/labels", {"labels": [HOLD]})
    return item["html_url"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("queue")
    for name in ("propose", "merge", "hold"):
        sub = commands.add_parser(name)
        sub.add_argument("number", type=int)
        sub.add_argument(
            "file", type=Path, help="核实后的记录 JSON，或 hold 的说明文本"
        )
        if name == "merge":
            sub.add_argument("--head", required=True, help="已审查的完整 PR head SHA")
            sub.add_argument(
                "--issue", type=int, help="投稿要关闭的、已核实的候选 Issue"
            )
    args = parser.parse_args()
    if args.command == "queue":
        print(json.dumps(queue(), ensure_ascii=False, indent=2))
        return
    content = args.file.read_text("utf-8-sig")
    if args.command == "hold":
        print(hold(args.number, content))
        return
    row = json.loads(content)
    catalog.check_record(row)
    print(
        propose(args.number, row)
        if args.command == "propose"
        else merge(args.number, args.head, row, args.issue)
    )


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as error:
        sys.exit(str(error))
