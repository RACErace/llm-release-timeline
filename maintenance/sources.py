"""Read public first-party sources. Every returned item is evidence for a human to check."""

import hashlib
import json
import re
import threading
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from anthropic_releases import release as anthropic_release
from baidu_releases import read_announcement as baidu_announcement, release_cards as baidu_cards
from model_hubs import huggingface_models, legacy_model, modelscope_models
from model_scope import announcement_in_scope, hub_release_evidence, hub_metadata_in_scope

HERE = Path(__file__).resolve().parent
LOCAL = threading.local()
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
# Muse is also the name of an app, a coding client and several media models.
META_MODEL = re.compile(
    r"\b(?:Llama|Muse\s+(?!(?:Code|Image|Video|Voice|Models?)\b)[A-Z][\w-]*)\b"
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


def request(method, url, **kwargs):
    if not hasattr(LOCAL, "session"):
        session = requests.Session()
        session.headers["User-Agent"] = (
            "ModelReleaseAtlas/1.0 (+https://github.com/RACErace/llm-release-timeline)"
        )
        retries = Retry(
            total=2,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            raise_on_status=False,
            respect_retry_after_header=False,
        )
        session.mount("https://", HTTPAdapter(max_retries=retries))
        LOCAL.session = session
    response = LOCAL.session.request(method, url, timeout=(8, 22), **kwargs)
    response.raise_for_status()
    return response


def get(url, **kwargs):
    return request("GET", url, **kwargs)


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
        if source["id"] == "claude":
            for part in segments or [plain(body), title]:
                if launch := anthropic_release(part):
                    name, kind = launch
                    # Preserve evidence IDs stored by the original RSS reader.
                    ref = url + ("|" + part[:180] if part != title else "")
                    yield signal(source, ref, name or part[:145], url, kind, date, part, model=name)
            continue
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
            if source["id"] == "vertex" and not re.search(
                r"(?:launch|releas|introduc|announc)\w*\s+(?:the\s+)?(?:Claude|Gemini|Gemma)\b|"
                r"\b(?:Claude (?:Opus|Haiku|Sonnet|Fable)|Gemini|Gemma)\b[^.!?]{0,60}\b(?:available|launched|released|rollout)",
                part,
                re.IGNORECASE,
            ):
                continue
            kind = guess_kind(part)
            # Daily notes can add another release to an already published item.
            ref = url + ("|" + part[:180] if part != title else "")
            yield signal(source, ref, part[:145], url, kind, date, text)


def meta_research_items(source, soup):
    cards = soup.select("article a[href*='/blog/']")
    if not cards:
        raise ValueError("missing Meta AI Research blog cards")
    for card in cards:
        link = urlsplit(urljoin(source["url"], card["href"]))
        if link.scheme != "https" or link.netloc not in {
            "research.meta.ai",
            "ai.meta.com",
        }:
            continue
        heading = card.select_one("h2, h3")
        if not heading:
            raise ValueError("missing Meta AI Research article heading")
        title = clean(heading.get_text(" ", strip=True))
        if not META_MODEL.search(title) or RETIRED.search(title):
            continue
        if not (
            LAUNCH.search(title)
            or re.match(r"^(?:Muse|Llama)\b[^:]+:.*\bmodel\b", title, re.I)
        ):
            continue
        stamp = card.select_one("time[datetime]")
        date = utc_date(stamp["datetime"]) if stamp else ""
        if not date:
            raise ValueError("missing Meta AI Research publication date: " + link.path)
        url = link._replace(query="", fragment="").geturl()
        kind = guess_kind(title.replace("‑", "-"))
        yield signal(source, url.rstrip("/"), title, url, kind, date, title)


def page_items(source, raw, seen=None):
    soup = BeautifulSoup(raw, "html.parser")
    if source["id"] == "baidu-news":
        for model, title, url, date in baidu_cards(soup, source["url"]):
            identity = url.rstrip("/") + "|" + date
            if seen and key(source["id"], identity) in seen:
                continue
            announcement = baidu_announcement(get(url), model, title, date)
            if announcement:
                kind, intro = announcement
                yield signal(source, identity, model, url, kind, date, intro, model=model)
        return
    if source["id"] == "claude-news":
        cards = soup.select("li[data-listing-row]")
        if not cards:
            raise ValueError("missing Anthropic newsroom article list")
        for card in cards:
            heading = card.select_one("a[href] h3")
            if not heading:
                raise ValueError("missing Anthropic newsroom headline")
            title = clean(heading.get_text(" ", strip=True))
            if launch := anthropic_release(title):
                name, kind = launch
                url = urljoin(source["url"], heading.find_parent("a")["href"])
                if urlsplit(url).hostname not in {"anthropic.com", "www.anthropic.com"}:
                    continue
                stamp = card.find("time")
                if not stamp:
                    raise ValueError("missing Anthropic newsroom publication date: " + url)
                date = (
                    utc_date(stamp["datetime"]) if stamp.get("datetime")
                    else datetime.strptime(stamp.get_text(strip=True), "%b %d, %Y").date().isoformat()
                )
                if not date:
                    raise ValueError("invalid Anthropic newsroom publication date: " + url)
                yield signal(source, url, name or title, url, kind, date, title, model=name)
        return
    if source["id"] == "meta-research":
        yield from meta_research_items(source, soup)
        return
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
    if not hub_metadata_in_scope(row):
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


def model_card(source, row):
    repo = quote(row["id"], safe="/")
    if source["channel"] == "huggingface":
        url = "https://huggingface.co/" + repo
        if row.get("gated"):
            doc = BeautifulSoup(get(url).content, "html.parser").select_one(".model-card-content")
            return doc.get_text("\n", strip=True) if doc else ""
        revision = quote(row.get("sha") or "main", safe="")
        card_url = f"{url}/raw/{revision}/README.md"
    else:
        card_url = f"https://modelscope.cn/models/{repo}/resolve/master/README.md"
    try:
        return get(card_url).content.decode("utf-8", errors="replace")
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code in {401, 403, 404}:
            return ""
        raise


def verify_hub_item(source, row, item):
    evidence = hub_release_evidence(row, model_card(source, row))
    if not evidence:
        return None
    item["base_evidence"] = clean(evidence)
    item["excerpt"] = "发布方提供的基座或官方版本依据：" + item["base_evidence"]
    return item


def lookup_hub_model(repo, channel):
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise ValueError("请填写官方模型仓标识，例如 组织/模型")
    source = next(
        (
            s
            for s in registry()
            if s["channel"] == channel
            and s["owner"].casefold() == repo.split("/")[0].casefold()
        ),
        None,
    )
    if not source:
        raise ValueError("该发布方不在官方模型库清单内")
    if channel == "huggingface":
        row = get("https://huggingface.co/api/models/" + quote(repo)).json()
    else:
        payload = get("https://modelscope.cn/api/v1/models/" + quote(repo)).json()
        row = legacy_model(payload["Data"])
    if row["id"].casefold() != repo.casefold():
        raise ValueError("模型仓返回的模型标识与请求不一致")
    item = hub_signal(source, row)
    return verify_hub_item(source, row, item) if item else None


def checked_model(repo):
    item = lookup_hub_model(repo, "huggingface")
    if not item:
        raise ValueError("该仓库缺少语言基模型或原开发方官方版本的权重及发布依据")
    return item


def hub_items(source, since, seen):
    listing = (
        huggingface_models if source["channel"] == "huggingface" else modelscope_models
    )
    for row, updated in listing(source["owner"], since, request):
        item = hub_signal(source, row)
        if not item or (seen is not None and item["id"] in seen):
            continue
        # The first scan only establishes an ID baseline. Verify previously
        # unseen listings against the publisher's card before queueing them.
        if (since or seen is None) and not verify_hub_item(source, row, item):
            continue
        item["upstream_updated"] = updated
        yield item


def read_source(source, since, seen=None):
    if source["channel"] in {"huggingface", "modelscope"}:
        rows = hub_items(source, since, seen)
    else:
        response = get(source["url"])
        rows = (
            feed_items(source, response.content)
            if source["channel"] == "feeds"
            else page_items(source, response.content, seen)
        )
    return {r["id"]: r for r in rows if announcement_in_scope(r.get("model") or r["name"])}
