"""JSON stdin/stdout entry point for the Personal-Agent release maintainer."""

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

# Executed with python -I. Import only siblings in the deployed, trusted package.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent
import catalog
from github_api import api

SITE = "https://racerace.github.io/llm-release-timeline/"
OWNER, NAME = agent.REPO.split("/")
PAGE_SIZE = 8
TEXT_SIZE = 3500


def identity():
    expected = os.environ.get("ATLAS_EXPECTED_LOGIN", "")
    if not os.environ.get("GH_TOKEN") or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", expected
    ):
        raise ValueError("备用账户凭据或预期登录名尚未配置")
    actual = api("user")["login"]
    if (
        actual.casefold() != expected.casefold()
        or actual.casefold() == OWNER.casefold()
    ):
        raise ValueError("GitHub 登录身份不符合备用维护账户配置")
    repo = api("repos/" + agent.REPO)
    if repo["full_name"] != agent.REPO or not repo.get("permissions", {}).get("push"):
        raise ValueError("备用账户尚无年表仓库写权限")
    return actual


def integer(obj, key):
    value = obj.get(key)
    if type(value) is not int or value < 1:
        raise ValueError(f"{key} 应为正整数")
    return value


def sha(obj, key):
    value = obj.get(key)
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{40}", value):
        raise ValueError(f"{key} 应为完整 Git SHA")
    return value


def pages(path):
    return agent.github(
        path + ("&" if "?" in path else "?") + "per_page=100", paginate=True
    )


def connection(number, target, field, select, extra=""):
    query = (
        'query($n:Int!, $cursor:String){repository(owner:"'
        + OWNER
        + '",name:"'
        + NAME
        + '"){'
        + target
        + "(number:$n){"
        + field
        + "(first:100,after:$cursor"
        + extra
        + "){nodes{"
        + select
        + "}pageInfo{endCursor hasNextPage}}}}}"
    )
    nodes, cursor = [], None
    while True:
        result = api(
            "graphql", {"query": query, "variables": {"n": number, "cursor": cursor}}
        )
        if result.get("errors"):
            raise RuntimeError("GitHub 未能完整读取 " + field)
        page = result["data"]["repository"][target][field]
        nodes.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            return nodes
        cursor = page["pageInfo"]["endCursor"]
        if not cursor:
            raise RuntimeError("GitHub 未返回分页游标")


def linked_prs(number):
    pulls = connection(
        number,
        "issue",
        "closedByPullRequestsReferences",
        "number url state headRefOid repository{nameWithOwner} "
        "labels(first:100){nodes{name} pageInfo{hasNextPage}}",
        ",includeClosedPrs:true",
    )
    result = []
    for p in pulls:
        if p["repository"]["nameWithOwner"] != agent.REPO:
            continue
        labels = p["labels"]["nodes"]
        if p["labels"]["pageInfo"]["hasNextPage"]:
            labels = pages(f"issues/{p['number']}/labels")
        result.append(
            {
                "number": p["number"],
                "url": p["url"],
                "state": p["state"].lower(),
                "head": p["headRefOid"],
                "labels": sorted(label["name"] for label in labels),
            }
        )
    return result


def maintenance_branch(pr, login, linked_issues):
    """Describe open, host-generated branches. All mutation checks still run afresh."""
    head, base = pr["head"], pr["base"]
    match = re.fullmatch(re.escape(agent.BRANCH) + r"([1-9]\d*)", head["ref"])
    if (
        not match
        or pr["state"] != "open"
        or pr["user"]["login"].casefold() != login.casefold()
        or (head.get("repo") or {}).get("full_name") != agent.REPO
        or base["repo"]["full_name"] != agent.REPO
        or base["ref"] != agent.MAIN
        or len(linked_issues) != 1
    ):
        return None
    issue = int(match[1])
    linked = linked_issues[0]
    if linked["number"] != issue or linked["repository"]["nameWithOwner"] != agent.REPO:
        return None
    commit = agent.github("git/commits/" + head["sha"])
    if commit["message"] != agent.proposal_message(issue) or not commit["parents"]:
        return None
    based_on = commit["parents"][0]["sha"]
    current = agent.github("git/ref/heads/" + agent.MAIN)["object"]["sha"]
    return {
        "issue": issue,
        "based_on": based_on,
        "current_main": current,
        "needs_refresh": based_on != current,
    }


def threads(number):
    return connection(
        number,
        "pullRequest",
        "reviewThreads",
        "id isResolved isOutdated path line comments(first:1){nodes{databaseId}}",
    )


def checks(head):
    runs = agent.github(
        f"commits/{head}/check-runs?filter=latest&per_page=100",
        paginate=True,
        page_key="check_runs",
    )
    result = [
        {
            "name": r["name"],
            "status": r["status"],
            "conclusion": (r["conclusion"] or "").upper(),
            "detailsUrl": r.get("details_url") or "",
            "id": r["id"],
        }
        for r in runs
    ]
    latest = {}
    for s in pages(f"commits/{head}/statuses"):
        latest.setdefault(
            s["context"],
            {"name": s["context"], "state": s["state"].upper(), "id": s["id"]},
        )
    return result + list(latest.values())


def notes(items):
    return [
        {
            "id": c["id"],
            "author": (c.get("user") or {}).get("login", "ghost"),
            "body": c.get("body") or "",
            **{
                k: c[k]
                for k in (
                    "state",
                    "updated_at",
                    "submitted_at",
                    "html_url",
                    "commit_id",
                    "path",
                    "line",
                )
                if k in c
            },
        }
        for c in items
    ]


def catalogue(query):
    if not isinstance(query, str) or not query.strip():
        return []
    base = agent.github("git/ref/heads/main")["object"]["sha"]
    term = query.strip().casefold()
    return [
        row
        for row in json.loads(agent.contents(agent.DATA, base))
        if any(term in value.casefold() for value in row.values())
    ]


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def chunks(items):
    for item in items:
        field = "patch" if "patch" in item else "body"
        value = item.get(field) or ""
        if len(value) <= TEXT_SIZE:
            yield item
            continue
        for start in range(0, len(value), TEXT_SIZE):
            yield {
                **item,
                field: value[start : start + TEXT_SIZE],
                "offset": start,
                "total_chars": len(value),
            }


def inspect(number, login, query="", page=1):
    raw = agent.github(f"issues/{number}")
    issue = {
        "number": number,
        "title": raw["title"],
        "body": raw.get("body") or "",
        "author": raw["user"]["login"],
        "labels": sorted(agent.labels(raw)),
        "url": raw["html_url"],
        "state": raw["state"],
    }
    comments = notes(pages(f"issues/{number}/comments"))
    groups = {
        "comments": comments,
        "reviews": [],
        "review_threads": [],
        "review_comments": [],
        "checks": [],
        "changed_files": [],
        "catalog_matches": catalogue(query),
    }
    result = {"issue": issue.copy(), "pr": None, "linked_issues": [], "linked_prs": []}
    if "pull_request" in raw:
        p = agent.github(f"pulls/{number}")
        head = p["head"]["sha"]
        result["pr"] = {
            "number": number,
            "state": p["state"],
            "url": p["html_url"],
            "head": head,
            "base": p["base"]["sha"],
            "base_ref": p["base"]["ref"],
            "draft": p["draft"],
            "merged": p["merged"],
            "merge_sha": p["merge_commit_sha"] if p["merged"] else None,
        }
        result["linked_issues"] = connection(
            number,
            "pullRequest",
            "closingIssuesReferences",
            "number url repository{nameWithOwner}",
        )
        result["pr"]["maintenance_branch"] = maintenance_branch(
            p, login, result["linked_issues"]
        )
        groups.update(
            reviews=notes(pages(f"pulls/{number}/reviews")),
            review_threads=threads(number),
            review_comments=notes(pages(f"pulls/{number}/comments")),
            checks=checks(head),
        )
        groups["changed_files"] = [
            {
                **{
                    k: f[k]
                    for k in (
                        "filename",
                        "previous_filename",
                        "status",
                        "sha",
                        "patch",
                        "blob_url",
                        "raw_url",
                        "changes",
                    )
                    if k in f
                },
                "patch_available": isinstance(f.get("patch"), str),
            }
            for f in pages(f"pulls/{number}/files")
        ]
        if len(groups["changed_files"]) != p["changed_files"]:
            raise ValueError("GitHub 未返回完整文件清单，请人工审查")
    else:
        result["linked_prs"] = linked_prs(number)
    # Hash facts across all pages; paging and fuzzy catalogue search do not change a task revision.
    factual = {k: v for k, v in groups.items() if k != "catalog_matches"}
    for k in ("comments", "reviews", "review_comments"):
        factual[k] = [
            c for c in factual[k] if c["author"].casefold() != login.casefold()
        ]
    result["revision"] = digest([result, factual])
    groups["issue_body"] = [{"body": issue["body"]}]
    result["issue"]["body"] = issue["body"][:TEXT_SIZE]
    result["issue"]["body_truncated"] = len(issue["body"]) > TEXT_SIZE
    totals = {}
    for key, items in groups.items():
        rows = list(chunks(items))
        totals[key] = len(rows)
        result[key] = rows[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
    result.update(
        page=page,
        totals=totals,
        has_more=any(n > page * PAGE_SIZE for n in totals.values()),
    )
    return result


def resolve_duplicate(number, row, login):
    commit = agent.github("git/ref/heads/main")["object"]["sha"]
    if row not in json.loads(agent.contents(agent.DATA, commit)):
        raise ValueError("主分支中未找到逐字段相同的已有记录")
    marker = "<!-- atlas-agent:duplicate:" + digest(row) + " -->"
    matches = [
        c
        for c in pages(f"issues/{number}/comments")
        if c["user"]["login"] == login and (c["body"] or "").startswith(marker)
    ]
    issue = agent.github(f"issues/{number}")
    if issue["state"] == "closed" and matches:
        return {"number": number, "url": issue["html_url"], "state": "closed"}
    agent.candidate(number)
    if not matches:
        link = SITE + "?" + urlencode({"q": row["name"]})
        agent.github(
            f"issues/{number}/comments",
            {
                "body": f"{marker}\n已收录同一事件。[查看年表记录]({link}) · <{row['url']}>"
            },
        )
    agent.candidate(number)
    closed = agent.github(
        f"issues/{number}",
        {"state": "closed", "state_reason": "completed"},
        method="PATCH",
    )
    if closed["state"] != "closed":
        raise RuntimeError("GitHub 未确认关闭")
    return {"number": number, "url": closed["html_url"], "state": "closed"}


def verify_pages(commit, row):
    query = urlencode({"branch": agent.MAIN, "head_sha": commit, "per_page": 100})
    runs = agent.github("actions/workflows/pages.yml/runs?" + query)["workflow_runs"]
    runs = [
        r
        for r in runs
        if r["head_sha"] == commit and r["event"] in ("push", "workflow_dispatch")
    ]
    proof = {"ready": False, "merge_sha": commit, "status": "pending"}
    if not runs:
        return proof
    run = max(runs, key=lambda r: (r["run_number"], r["run_attempt"]))
    proof.update(status=run["conclusion"] or run["status"], run_url=run["html_url"])
    if run["conclusion"] != "success":
        return proof
    jobs = agent.github(
        f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100",
        paginate=True,
        page_key="jobs",
    )
    if not any(j["name"] == "deploy" and j["conclusion"] == "success" for j in jobs):
        return {**proof, "status": "deploy_unconfirmed"}
    if row not in json.loads(agent.contents(agent.DATA, commit)):
        raise ValueError("指定合并提交未包含核实的记录")
    with urlopen(SITE + "data/releases.json?rev=" + commit, timeout=15) as res:
        public = json.load(res)
    if row not in public:
        return {**proof, "status": "not_visible"}
    return {
        **proof,
        "ready": True,
        "status": "published",
        "site_url": SITE + "?" + urlencode({"q": row["name"]}),
    }


def takeover_queue():
    queue = agent.queue()
    queue["recover_prs"] = []
    current = agent.github("git/ref/heads/main")["object"]["sha"]
    deploy = queue["pages"] or {}
    if deploy.get("head_sha") == current and deploy.get("conclusion") == "success":
        return queue
    for pr in pages(f"commits/{current}/pulls"):
        if not pr["merged_at"] or pr["merge_commit_sha"] != current:
            continue
        try:
            row = catalog.reviewed_row(pr["body"] or "")
        except ValueError:
            # Contributor PRs often have no JSON block. Recover its single new
            # record from the committed data without executing contributor code.
            commit = agent.github(f"git/commits/{current}")
            if not commit["parents"]:
                continue
            base = json.loads(agent.contents(agent.DATA, commit["parents"][0]["sha"]))
            new = json.loads(agent.contents(agent.DATA, current))
            old_keys = {catalog.identity(r) for r in base}
            added = [r for r in new if catalog.identity(r) not in old_keys]
            if len(added) != 1 or catalog.incorporate(base, added[0]) != new:
                continue
            row = added[0]
        queue["recover_prs"].append(
            {
                "number": pr["number"],
                "url": pr["html_url"],
                "head": pr["head"]["sha"],
                "merge_sha": current,
                "record": row,
            }
        )
    return queue


def dispatch(request):
    if not isinstance(request, dict):
        raise TypeError("输入须为 JSON 对象")
    op = request.get("op")
    login = identity()
    if op == "preflight":
        return {"repo": agent.REPO, "login": login, "write_access": True}
    if op == "queue":
        return takeover_queue()
    if op in {"propose", "merge", "resolve_duplicate", "verify_pages"}:
        row = request.get("record")
        catalog.check_record(row)
    if op == "verify_pages":
        return verify_pages(sha(request, "merge_sha"), row)
    number = integer(request, "number")
    if op == "inspect":
        return inspect(
            number,
            login,
            request.get("query", ""),
            integer(request, "page") if "page" in request else 1,
        )
    if op == "propose":
        url = agent.propose(number, row)
        pr = agent.github("pulls/" + url.rsplit("/", 1)[-1])
        return {"number": pr["number"], "url": url, "head": pr["head"]["sha"]}
    if op == "merge":
        head = sha(request, "head")
        pr = agent.github(f"pulls/{number}")
        if not pr["merged"]:
            if pr["head"]["sha"] != head or any(
                not t["isResolved"] for t in threads(number)
            ):
                raise ValueError("PR 提交已变更或仍有未解决的审查讨论")
            if not agent.validated(pr, checks(head)):
                raise ValueError("当前提交的检查尚未全部通过")
        merged = agent.merge(
            number,
            head,
            row,
            integer(request, "issue") if request.get("issue") is not None else None,
        )
        return {"number": number, "url": pr["html_url"], "merge_sha": merged}
    if op == "hold":
        reason = request.get("reason")
        if not isinstance(reason, str) or len(reason) > 3000:
            raise ValueError("请填写具体的待确认问题")
        return {
            "number": number,
            "url": agent.hold(number, reason),
            "state": "needs-human",
        }
    if op == "resolve_duplicate":
        return resolve_duplicate(number, row, login)
    raise ValueError("未知维护操作")


if __name__ == "__main__":
    try:
        request = json.loads(sys.stdin.buffer.read(128 * 1024 + 1).decode("utf-8-sig"))
        response = {"ok": True, "result": dispatch(request)}
    except (
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        subprocess.SubprocessError,
        KeyError,
    ) as exc:
        message = str(exc)
        if token := os.environ.get("GH_TOKEN"):
            message = message.replace(token, "[redacted]")
        response = {"ok": False, "error": message[:600]}
    print(json.dumps(response, ensure_ascii=False))
