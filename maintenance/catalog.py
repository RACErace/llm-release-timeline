"""Validate catalogue data and prepare reviewed Issue records for normal pull requests."""

import argparse
import bisect
import hashlib
import json
import os
import re
import subprocess
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/releases.json"
HOME_PAGE = ROOT / "index.html"
KINDS = {
    "发布",
    "预览",
    "开放权重",
    "开放 API",
    "受限开放",
    "版本更新",
    "研究公开",
    "公告",
}
REQUIRED = {"date", "org", "name", "kind", "note", "url", "source"}
STAMP = re.compile(r'(<time id="updated" datetime=")([\d-]+)("[^>]*>)([\d.]+)(</time>)')


def today():
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def identity(row):
    def fold(text):
        return unicodedata.normalize("NFKC", text).strip().casefold()

    return tuple(fold(row[k]) for k in ("date", "org", "name", "kind"))


def check_record(row):
    if (
        not isinstance(row, dict)
        or not REQUIRED <= row.keys()
        or row.keys() - (REQUIRED | {"url2", "stage"})
    ):
        raise ValueError(
            "字段须含 date, org, name, kind, note, url, source；可选 url2, stage"
        )
    for k, v in row.items():
        if (
            not isinstance(v, str)
            or not v.strip()
            or len(v) > (2000 if k in {"url", "url2"} else 700)
            or any(ord(c) < 32 for c in v)
        ):
            raise ValueError(f"请填写有效的单行字段 {k}")
    stamp = row["date"]
    if not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", stamp):
        raise ValueError("请核实事件日期，格式为 YYYY-MM-DD、YYYY-MM 或 YYYY")
    date.fromisoformat(stamp + {4: "-01-01", 7: "-01", 10: ""}[len(stamp)])
    if stamp > today() or row["kind"] not in KINDS:
        raise ValueError("请核实日期与事件类型：" + row["name"])
    for field in ("url", "url2"):
        if field in row:
            parsed = urlsplit(row[field])
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or "." not in parsed.hostname
                or parsed.username
                or parsed.password
                or re.search(r"[\s<>]", row[field])
            ):
                raise ValueError("请提供一手来源的完整 HTTPS 网址：" + field)


def validate(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError("年表必须是非空数组")
    seen = set()
    for row in rows:
        check_record(row)
        ident = identity(row)
        if ident in seen:
            raise ValueError("日期、机构、模型与事件类型重复：" + row["name"])
        seen.add(ident)
    if [r["date"] for r in rows] != sorted(r["date"] for r in rows):
        raise ValueError("记录应按日期升序排列")


def reviewed_row(body):
    blocks = re.findall(
        r"^```json[ \t]*\r?\n(.*?)^```[ \t]*\r?$", body, re.MULTILINE | re.DOTALL
    )
    if len(blocks) != 1:
        raise ValueError("Issue 正文应只包含一个 json 代码块")
    row = json.loads(blocks[0])
    check_record(row)
    return row


def incorporate(rows, row):
    check_record(row)
    if any(identity(r) == identity(row) for r in rows):
        raise ValueError("年表中已有相同记录：" + row["name"])
    result = rows.copy()
    result.insert(bisect.bisect_right([r["date"] for r in rows], row["date"]), row)
    return result


def release_document(rows):
    return json.dumps(rows, ensure_ascii=False, indent=2) + "\n"


def render_date(html, stamp):
    if len(STAMP.findall(html)) != 1:
        raise ValueError("首页应有一个资料整理日期")
    return STAMP.sub(
        lambda m: m[1] + stamp + m[3] + stamp.replace("-", ".") + m[5], html
    )


def check_site(rows):
    validate(rows)
    html = HOME_PAGE.read_text("utf-8")
    stamps = STAMP.findall(html)
    if len(stamps) != 1:
        raise ValueError("首页整理日期缺失")
    stamp, shown = stamps[0][1], stamps[0][3]
    date.fromisoformat(stamp)
    if not rows[-1]["date"] <= stamp <= today() or shown != stamp.replace("-", "."):
        raise ValueError("资料整理日期与年表不一致")
    cfg = json.loads((ROOT / "maintenance/sources.json").read_text("utf-8"))
    orgs = (
        set(cfg["huggingface"])
        | set(cfg["modelscope"])
        | {s["org"] for k in ("feeds", "pages") for s in cfg[k]}
    )
    uncovered = sorted({r["org"] for r in rows} - orgs)
    if uncovered:
        print("尚可增加官方订阅来源的团队：" + "、".join(uncovered))
    print(f"已校验 {len(rows)} 条发布记录，资料整理于 {stamp}")


def cmd(*args):
    result = subprocess.run(
        args, cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8"
    )
    return result.stdout.strip()


def prepare(number, repo):
    from github_api import api

    issue = api(f"repos/{repo}/issues/{number}")
    if "pull_request" in issue or issue["state"] != "open":
        raise ValueError("请选择尚未关闭的候选 Issue")
    row = reviewed_row(issue["body"] or "")
    rows = incorporate(json.loads(DATA.read_text("utf-8")), row)
    validate(rows)
    digest = hashlib.sha256(
        json.dumps(row, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()[:10]
    base = cmd("git", "rev-parse", "HEAD")
    branch = f"review/issue-{number}-{base[:8]}-{digest}"
    ref = "refs/heads/" + branch
    if not cmd("git", "ls-remote", "--heads", "origin", ref):
        DATA.write_text(
            release_document(rows),
            encoding="utf-8",
            newline="\n",
        )
        HOME_PAGE.write_text(
            render_date(HOME_PAGE.read_text("utf-8"), today()),
            encoding="utf-8",
            newline="\n",
        )
        check_site(rows)
        cmd("git", "add", "--", "data/releases.json", "index.html")
        cmd(
            "git",
            "-c",
            "user.name=github-actions[bot]",
            "-c",
            "user.email=41898282+github-actions[bot]@users.noreply.github.com",
            "commit",
            "-m",
            f"Add reviewed release from issue #{number}",
        )
        cmd("git", "push", "origin", "HEAD:" + ref)
    params = urlencode(
        {
            "expand": "1",
            "title": f"收录 {row['name']}（{row['kind']}）",
            "body": f"Closes #{number}\n\n依据：{row['url']}",
        }
    )
    url = f"https://github.com/{repo}/compare/main...{branch}?{params}"
    msg = (
        f"<!-- atlas-review:{branch} -->\n已生成收录分支。请[查看改动并创建 PR]({url})，"
        "核对检查结果后合并，网页会自动部署。修改候选内容后，可从 Actions 重新运行「准备收录」。"
    )
    comments = api(f"repos/{repo}/issues/{number}/comments?per_page=100", paginate=True)
    if not any(
        c["user"]["login"] == "github-actions[bot]"
        and f"<!-- atlas-review:{branch} -->" in c["body"]
        for c in comments
    ):
        api(f"repos/{repo}/issues/{number}/comments", {"body": msg})
    print("收录分支：" + branch)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as f:
            f.write(msg + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["check", "prepare"])
    parser.add_argument("--issue", type=int)
    args = parser.parse_args()
    if args.mode == "check":
        check_site(json.loads(DATA.read_text("utf-8")))
    elif not args.issue or args.issue < 1:
        parser.error("--issue must be a positive issue number")
    else:
        prepare(args.issue, os.environ["GITHUB_REPOSITORY"])
