"""Read public first-party sources. Every returned item is evidence for a human to check."""

import hashlib
import json
import re
import threading
import time
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from anthropic_releases import release as anthropic_release

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
    r"(?:^|[-_/])(?:gguf|gptq|awq|mlx|lora|adapter|bnb|bitsandbytes)(?:[-_/]|$)",
    re.IGNORECASE,
)
PAPER_TOPIC = re.compile(
    r"\b(?:language model(?:s|ing)?|foundation models?|reasoning models?|"
    r"(?:vision[- ]language|multimodal) models?|(?:[msv]?llm|[vs]lm)s?)\b",
    re.IGNORECASE,
)
AUTHOR_CLAIM = re.compile(
    r"\b(?:we (?:also )?(?:(?:train|pretrain|develop) and )?"
    r"(?:introduce|present|propose|release|open[- ]source|develop|pretrain)|"
    r"this (?:paper|work|report) (?:introduces|presents|proposes|releases))\s+",
    re.IGNORECASE,
)
# Inspect what the authors introduce, before its purpose, inputs or comparisons.
OBJECT_END = re.compile(
    r"[;:!?]|\.(?=\s|$)|\b(?:for|using|via|based on|through|to|from|with|"
    r"across|over|on|by|as|in|where|how|which|that)\b",
    re.IGNORECASE,
)
MODEL_OBJECT = re.compile(
    r"\b(?:models?|(?:[msv]?llm|[vs]lm)s?|checkpoints?|weights)\b", re.IGNORECASE
)
DRAFTER = re.compile(
    r"\b(?:draft|drafter)\s+(?:models?|checkpoints?|weights)\b", re.IGNORECASE
)
MODEL_SIZE = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)\s*[- ]?(billion|million|trillion|[bmt])\b",
    re.IGNORECASE,
)
# A variant name is evidence when the authors use it in their own release
# claim. Benchmark scores, baselines, prose edits and version numbers aren't.
VARIANT = re.compile(
    r"(?<![-\w])(?:base|instruct(?:ion)?[- ]tuned|instruct|chat|unified|decision-only|distilled)\b",
    re.IGNORECASE,
)
NAMED_ARTIFACT = re.compile(r"\b[A-Z][\w]*(?:[-.][\w]+)+\b")
INTRODUCED_NAME = re.compile(
    r"([A-Z][\w.-]*(?: [A-Z][\w.-]*){0,3}),\s+(?:a|an|our|the)\s", re.ASCII
)
NON_MODEL_OBJECT = re.compile(
    r"\b(?:benchmarks?|datasets?|corpora|corpus|frameworks?|methods?|metrics?|suites?|"
    r"surveys?|evaluations?|strateg(?:y|ies)|algorithms?|analys(?:is|es)|"
    r"protocols?|pipelines?|toolkits?|techniques?|approaches?|agents?|taxonom(?:y|ies))\b",
    re.IGNORECASE,
)
ARXIV_LINK = re.compile(
    r"https?://arxiv\.org/abs/(\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]+)?/\d{7})(v\d+)?"
)
# Muse is also the name of an app, a coding client and several media models.
META_MODEL = re.compile(
    r"\b(?:Llama|Muse\s+(?!(?:Code|Image|Video|Voice|Models?)\b)[A-Z][\w-]*)\b"
)


def model_paper_claim(title, abstract):
    """Return the authors' model disclosure, to include as evidence in the Issue."""
    if not PAPER_TOPIC.search(title + " " + abstract) and not MODEL.search(title):
        return ""
    name, colon, _ = title.partition(":")
    # Some papers release multiple artifacts together and describe the named
    # model elsewhere in the abstract (e.g. a corpus and its trained model).
    named_model = (
        colon
        and len(name.split()) <= 6
        and re.search(
            re.escape(name) + r",\s+(?:an?|our|the)\s+(?:[\w-]+\s+){0,5}model\b"
            r"(?=[,.]|\s+(?:with|which|that|trained|for)\b)",
            abstract,
            re.IGNORECASE,
        )
    )
    for claim in AUTHOR_CLAIM.finditer(abstract):
        tail = abstract[claim.end() :]
        obj = OBJECT_END.split(tail, maxsplit=1)[0]
        if (
            MODEL_OBJECT.search(DRAFTER.sub("", obj))
            and not NON_MODEL_OBJECT.search(obj)
        ) or (
            named_model
            and re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", obj, re.IGNORECASE)
        ):
            return (
                abstract[claim.start() : claim.end()]
                + re.split(r"(?<=[.!?])\s+", tail, maxsplit=1)[0]
            )
    return ""


def arxiv_announcement(text):
    match = re.search(r"\bAnnounce Type:\s*([\w-]+)", text)
    return match[1] if match else ""


def paper_sizes(sentence, model_claim):
    """Parameter counts for the authors' models, excluding training data and baselines."""
    own = re.split(
        r"\b(?:outperforms?|than|against|compared|versus)\b",
        sentence,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    specifications = re.split(
        r"\b(?:trained|evaluated)\b", own, maxsplit=1, flags=re.IGNORECASE
    )[0]
    reported = re.search(
        r"\bwe report\b.{0,75}\b(?:completed|preliminary|trained)\b.{0,50}\b(?:members?|checkpoints?|models?)\b",
        own,
        re.IGNORECASE,
    )
    for match in MODEL_SIZE.finditer(own):
        after = own[match.end() :]
        if re.match(
            r"[- ]*(?:tokens?|words?|examples?|decisions?|rows?)\b",
            after,
            re.IGNORECASE,
        ):
            continue
        in_specifications = (model_claim or reported) and match.start() < len(
            specifications
        )
        reported_member = reported and re.match(
            r"[- ]*(?:models?|checkpoints?|backbones?)\b", after, re.IGNORECASE
        )
        explicit_count = re.match(
            r"[- ]*parameters?\b", after, re.IGNORECASE
        ) and re.search(
            r"\b(?:our|training|scaling to)\b|\w[-.]\w+\s*\($",
            own[: match.start()],
            re.IGNORECASE,
        )
        if in_specifications or reported_member or explicit_count:
            number = match[1].rstrip("0").rstrip(".") if "." in match[1] else match[1]
            yield "size:" + number + match[2][0].lower()


def paper_artifacts(abstract):
    """Facts about the introduced artifacts, independent of how a revision is announced."""
    facts = {"model"}
    for sentence in re.split(r"(?<=[.!?])\s+", abstract):
        claim = AUTHOR_CLAIM.search(sentence)
        model_claim = False
        if claim:
            obj = OBJECT_END.split(sentence[claim.end() :], maxsplit=1)[0]
            if MODEL_OBJECT.search(obj) and not NON_MODEL_OBJECT.search(obj):
                model_claim = True
                facts.update(
                    "variant:" + word.casefold() for word in VARIANT.findall(obj)
                )
                names = obj.partition(",")[0].partition("(")[0]
                facts.update(
                    "name:" + word.casefold() for word in NAMED_ARTIFACT.findall(names)
                )
                if named := INTRODUCED_NAME.match(obj):
                    facts.add("name:" + named[1].casefold())
                if re.search(
                    r"\b(?:release|open[- ]source)\b", claim[0], re.IGNORECASE
                ) and re.search(r"\b(?:weights|checkpoints?)\b", obj, re.IGNORECASE):
                    facts.add("open-weights")
        facts.update(paper_sizes(sentence, model_claim))
    return sorted(facts)


def paper_signal(source, title, url, abstract, announced, announcement, version=0):
    claim = model_paper_claim(title, abstract)
    if not claim:
        return None
    match = ARXIV_LINK.fullmatch(url)
    if not match:
        raise ValueError("expected an arXiv abstract link")
    url = "https://arxiv.org/abs/" + match[1]
    facts = paper_artifacts(abstract)
    item = signal(source, key(url, *facts), title[:145], url, "研究公开", excerpt=claim)
    # RSS pubDate describes this feed announcement, including cross-lists and
    # replacements. Even a first paper announcement need not date the model.
    item["arxiv"] = {
        "type": announcement,
        "date": announced,
        "artifacts": facts,
        "version": version,
    }
    return item


def arxiv_abstracts(urls):
    """Read full author abstracts, preserving any explicitly requested revision."""
    identifiers = {}
    for url in urls:
        match = ARXIV_LINK.fullmatch(url)
        if not match:
            raise ValueError("expected an arXiv abstract link")
        identifiers[match[1] + (match[2] or "")] = url
    result = {}
    ids = list(identifiers)
    for start in range(0, len(ids), 100):
        if start:
            time.sleep(3)
        batch = ids[start : start + 100]
        response = get(
            "https://export.arxiv.org/api/query",
            params={
                "id_list": ",".join(batch),
                "max_results": len(batch),
            },
        )
        for entry in ET.fromstring(response.content).findall("{*}entry"):
            match = ARXIV_LINK.fullmatch(entry.findtext("{*}id", ""))
            title, abstract = (
                entry.findtext("{*}title", ""),
                entry.findtext("{*}summary", ""),
            )
            if match and title.strip() and abstract.strip():
                for ident in (match[1], match[1] + (match[2] or "")):
                    if ident in batch:
                        result[identifiers[ident]] = (clean(title), clean(abstract))
    return result


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
        if source.get("discovery"):
            description = plain(body)
            abstract = description.partition("Abstract:")[2] or description
            announcement = node.findtext("{*}announce_type") or arxiv_announcement(
                description
            )
            guid = node.findtext("{*}guid", "").strip()
            version = re.search(r"v(\d+)$", guid) or re.match(
                r"arXiv:\S+?v(\d+)\s", description
            )
            if paper := paper_signal(
                source,
                title,
                url,
                abstract,
                date,
                announcement,
                int(version[1]) if version else 0,
            ):
                yield paper
            continue
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


def page_items(source, raw):
    soup = BeautifulSoup(raw, "html.parser")
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
                item["upstream_updated"] = updated
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
