"""Read public first-party sources. Every returned item is evidence for a human to check."""

import hashlib
import json
import re
import threading
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

HERE = Path(__file__).resolve().parent
LOCAL = threading.local()
LANGUAGE_TASKS = {
    "text-generation",
    "text2text-generation",
    "image-text-to-text",
    "any-to-any",
    "visual-question-answering",
    "audio-text-to-text",
}
MODEL = re.compile(
    r"\b(?:GPT[- .]?\d|o[134]\b|Claude|Gemini|Gemma|Grok|Qwen|Kimi[- ]?K\d|DeepSeek|Nova|Titan|DBRX|Dolly|Jamba|Mercury|tsuzumi|Latam[- ]?GPT|LLMs?\b|(?:language|foundation|reasoning) models?\b)|大模型|语言模型|千问|星火|modelo de lenguaje",
    re.IGNORECASE,
)
LAUNCH = re.compile(
    r"introduc|launch|releas|unveil|announc|debut|availab|publish|open[- ](?:weight|sourc)|new model|发布|上线|推出|开放|升级|lanzamiento|lanzamos|presenta",
    re.IGNORECASE,
)
RETIRED = re.compile(
    r"deprecat|retir(?:e|ed|ement)|end of life|shut(?:ting)? down|price only|下线|下架",
    re.IGNORECASE,
)
DERIVATIVE = re.compile(
    r"(?:^|[-_/])(?:gguf|gptq|awq|mlx|lora|adapter)(?:[-_/]|$)", re.IGNORECASE
)


def key(*parts):
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:32]


def clean(value):
    return " ".join(value.replace("\u200b", "").split())


def plain(markup):
    if "<" not in markup:
        return clean(markup)
    return clean(BeautifulSoup(markup, "html.parser").get_text(" ", strip=True))


def utc_date(value):
    if not value:
        return ""
    try:
        d = datetime.fromisoformat(value)
    except ValueError:
        try:
            d = parsedate_to_datetime(value)
        except (ValueError, TypeError):
            return ""
    return d.date().isoformat()


def get(url, **kwargs):
    if not hasattr(LOCAL, "session"):
        session = requests.Session()
        session.headers["User-Agent"] = (
            "ModelReleaseAtlas/1.0 (+https://github.com/RACErace/llm-release-timeline)"
        )
        retries = Retry(
            total=2,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            respect_retry_after_header=False,
        )
        session.mount("https://", HTTPAdapter(max_retries=retries))
        LOCAL.session = session
    response = LOCAL.session.get(url, timeout=(8, 22), **kwargs)
    response.raise_for_status()
    return response


def registry():
    cfg = json.loads((HERE / "sources.json").read_text("utf-8"))
    result = []
    for channel in ("huggingface", "modelscope"):
        for org, owner in cfg[channel].items():
            result.append(
                {
                    "id": channel + ":" + owner,
                    "channel": channel,
                    "org": org,
                    "owner": owner,
                }
            )
    for channel in ("feeds", "pages"):
        result.extend(dict(s, channel=channel) for s in cfg[channel])
    return result


def signal(source, identity, name, url, kind, date="", excerpt="", *, model=""):
    if urlsplit(url).scheme not in {"http", "https"} or not name:
        raise ValueError("source returned an invalid title or URL")
    return {
        "id": key(source["id"], identity),
        "org": source["org"],
        "name": clean(name)[:180],
        "kind": kind,
        "date": date,
        "url": url,
        "source": source["id"],
        "excerpt": clean(excerpt)[:360],
        "model": model,
    }


def guess_kind(text):
    if re.search(r"open[- ]weights?|开放权重", text, re.IGNORECASE):
        return "开放权重"
    if re.search(r"preview|beta|预览|公测", text, re.IGNORECASE):
        return "预览"
    if re.search(r"API|接口上线", text, re.IGNORECASE):
        return "开放 API"
    if re.search(r"\bupdate[d]?\b|升级", text, re.IGNORECASE):
        return "版本更新"
    return "发布"


def feed_items(source, raw):
    root = ET.fromstring(raw)
    if root.tag.rsplit("}", 1)[-1] not in {"rss", "RDF", "feed"}:
        raise ValueError("expected an RSS or Atom feed")
    items = root.findall(".//{*}item") + root.findall("{*}entry")
    for node in items:
        link = node.find("{*}link")
        url = (link.get("href") or link.text or "") if link is not None else ""
        title = clean(node.findtext("{*}title", ""))
        date = utc_date(
            next(
                (
                    node.findtext("{*}" + k)
                    for k in ("published", "pubDate", "date", "updated")
                    if node.findtext("{*}" + k)
                ),
                "",
            )
        )
        body = (
            next(
                (
                    node.findtext("{*}" + k)
                    for k in ("content", "description", "summary")
                    if node.findtext("{*}" + k)
                ),
                "",
            )
            or ""
        )
        segments = [title]
        if source["id"] in {"claude", "vertex"}:
            doc = BeautifulSoup(body, "html.parser")
            segments = [
                plain(n.get_text(" ", strip=True))
                for n in doc.select("li, p")
                if not n.find_parent("li")
            ]
        brief = plain(body)[:650] if len(segments) == 1 and segments[0] == title else ""
        for part in segments:
            text = clean(part + " " + brief)
            if not MODEL.search(text) or (
                source.get("include")
                and not re.search(source["include"], text, re.IGNORECASE)
            ):
                continue
            if RETIRED.search(part) or not LAUNCH.search(text):
                continue
            if source["id"] in {"claude", "vertex"} and not re.search(
                r"(?:launch|releas|introduc|announc)\w*\s+(?:the\s+)?(?:Claude|Gemini|Gemma)\b|"
                r"\b(?:Claude (?:Opus|Haiku|Sonnet|Fable)|Gemini|Gemma)\b[^.!?]{0,60}\b(?:available|launched|released|rollout)",
                part,
                re.IGNORECASE,
            ):
                continue
            if source.get("discovery") and not (
                re.search(
                    r"language model|\bLLM\b|foundation model", title, re.IGNORECASE
                )
                and re.search(
                    r"we (?:introduce|release|present)|open[- ]weight",
                    text,
                    re.IGNORECASE,
                )
            ):
                continue
            kind = "研究公开" if source.get("discovery") else guess_kind(part)
            # Daily notes can add another release to an already published item.
            ref = url + ("|" + part[:180] if part != title else "")
            yield signal(source, ref, part[:145], url, kind, date, text)


def page_items(source, raw):
    soup = BeautifulSoup(raw, "html.parser")
    nodes = soup.select(source["select"])
    if not nodes:
        raise ValueError("missing expected page section: " + source["select"])
    for node in nodes:
        title = clean(node.get_text(" ", strip=True))
        if not re.search(source["include"], title, re.IGNORECASE) or (
            source["id"] != "spark" and RETIRED.search(title)
        ):
            continue
        section = (
            node if node.has_attr("id") else node.find_previous(["h2", "h3"], id=True)
        )
        url = source["url"]
        if node.name == "a":
            url = urljoin(url, node.get("href", ""))
        elif section:
            url += "#" + quote(section["id"])
        date = ""
        if source["id"] == "deepseek":
            heading = node.find_previous("h2")
            stamp = re.search(r"\d{4}-\d\d-\d\d", heading.get_text() if heading else "")
            date = stamp[0] if stamp else ""
        identity = url if node.name == "a" else url + "|" + title
        yield signal(
            source,
            identity,
            title,
            url,
            source.get("kind") or guess_kind(title),
            date,
            title,
        )


def hub_signal(source, row):
    hf = source["channel"] == "huggingface"
    repo = row["id"]
    if repo.split("/", 1)[0].casefold() != source["owner"].casefold():
        raise ValueError("upstream returned a different organization: " + repo)
    name = repo.split("/", 1)[1]
    tags = set(row.get("tags") or [])
    tasks = ({row.get("pipeline_tag")} | tags) if hf else set(row.get("tasks") or [])
    if not (
        tasks & LANGUAGE_TASKS
        or MODEL.search(name)
        or re.search(r"openPangu|SenseNova", name, re.IGNORECASE)
    ) or DERIVATIVE.search(name):
        return None
    if hf:
        files = [f["rfilename"] for f in row.get("siblings", [])]
        if not any(
            re.search(
                r"(?:\.safetensors|pytorch_model[^/]*\.bin|consolidated[^/]*\.pth)$",
                f,
                re.IGNORECASE,
            )
            and "adapter" not in f.lower()
            for f in files
        ):
            return None
    elif "library:safetensors" not in tags and not row.get("params"):
        return None
    kind = "受限开放" if row.get("gated") else "开放权重"
    link = (
        "https://huggingface.co/" if hf else "https://modelscope.cn/models/"
    ) + quote(repo, safe="/-_.")
    return signal(
        source,
        repo + "|" + kind,
        name,
        link,
        kind,
        excerpt="官方模型库中的权重或权重元数据。请核实首次对外开放的日期与范围。",
        model=name,
    )


def checked_model(repo):
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise ValueError("请填写官方 Hugging Face 模型标识，例如 组织/模型")
    source = next(
        (
            s
            for s in registry()
            if s["channel"] == "huggingface"
            and s["owner"].casefold() == repo.split("/")[0].casefold()
        ),
        None,
    )
    if not source:
        raise ValueError("该发布方不在官方模型库清单内")
    item = hub_signal(
        source, get("https://huggingface.co/api/models/" + quote(repo)).json()
    )
    if not item:
        raise ValueError("该仓库没有匹配的语言模型权重")
    return item


def hub_items(source, since):
    hf = source["channel"] == "huggingface"
    owner = source["owner"]
    url = (
        "https://huggingface.co/api/models"
        if hf
        else "https://modelscope.cn/openapi/v1/models"
    )
    params = (
        {
            "author": owner,
            "sort": "lastModified",
            "direction": -1,
            "limit": 50,
            "full": "true",
        }
        if hf
        else {
            "owner": owner,
            "sort": "last_modified",
            "page_number": 1,
            "page_size": 50,
        }
    )
    # Revisit an overlap after outages, and paginate until the previous successful window.
    cutoff = datetime.fromisoformat(since) - timedelta(hours=24) if since else None
    while True:
        response = get(url, params=params)
        data = response.json()
        rows = data if hf else data["data"]["models"]
        if not isinstance(rows, list) or (not rows and not since):
            raise ValueError("expected a non-empty initial public organization listing")
        for row in rows:
            updated = row["lastModified" if hf else "last_modified"]
            if cutoff and datetime.fromisoformat(updated) < cutoff:
                return
            item = hub_signal(source, row)
            if item:
                yield item
        if not since:
            return
        if hf:
            url = response.links.get("next", {}).get("url")
            if not url:
                return
            if urlsplit(url).netloc != "huggingface.co":
                raise ValueError("invalid pagination host")
            params = None
        else:
            if (
                params["page_number"] * params["page_size"]
                >= data["data"]["total_count"]
            ):
                return
            params["page_number"] += 1


def read_source(source, since):
    if source["channel"] in {"huggingface", "modelscope"}:
        rows = hub_items(source, since)
    else:
        response = get(source["url"])
        rows = (
            feed_items(source, response.content)
            if source["channel"] == "feeds"
            else page_items(source, response.content)
        )
    return {r["id"]: r for r in rows}
