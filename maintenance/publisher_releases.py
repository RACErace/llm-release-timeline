"""Dated first-party announcements, independent of collection and delivery state."""

import re
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup


@dataclass(frozen=True)
class Release:
    name: str
    date: str
    kind: str
    url: str
    excerpt: str


def text(node):
    return " ".join(node.get_text(" ", strip=True).replace("\ufeff", "").split())


def iso(value):
    return date.fromisoformat(value).isoformat()


def first_party(base, href):
    url = urljoin(base, href)
    target = urlsplit(url)
    if target.scheme != "https" or target.hostname != urlsplit(base).hostname:
        raise ValueError("publisher link leaves the official site")
    return url


def table_rows(table):
    """Expand publisher HTML row/column spans before interpreting column names."""
    spans = {}
    for row in table.select("tr"):
        if row.find_parent("table") is not table:
            continue
        values = []

        def carry():
            while len(values) in spans:
                column = len(values)
                value, remaining = spans.pop(column)
                values.append(value)
                if remaining > 1:
                    spans[column] = (value, remaining - 1)

        for cell in row.find_all(["th", "td"], recursive=False):
            carry()
            width, height = (int(cell.get(attr, 1)) for attr in ("colspan", "rowspan"))
            if not 1 <= width <= 32 or not 1 <= height <= 1000:
                raise ValueError("invalid publisher table span")
            value = text(cell)
            for _ in range(width):
                column = len(values)
                if column in spans:
                    raise ValueError("overlapping publisher table spans")
                values.append(value)
                if height > 1:
                    spans[column] = (value, height - 1)
        carry()
        yield values
    if spans:
        raise ValueError("unfinished publisher table span")


def named_tables(soup, required):
    found = False
    for table in ([soup] if soup.name == "table" else soup.select("table")):
        rows = table_rows(table)
        headers = next(rows, [])
        if not set(required) <= set(headers):
            continue
        found = True
        for cells in rows:
            if not cells:
                continue
            if len(cells) != len(headers):
                raise ValueError("publisher release table has an incomplete row")
            yield dict(zip(headers, cells))
    if not found:
        raise ValueError("missing publisher release table")


def huawei_releases(soup, url):
    for row in named_tables(soup, ("模型名称", "模型类型", "模型版本", "预置服务")):
        name = row["模型名称"]
        # MaaS also hosts other vendors. Their arrival is not a Huawei release.
        if not re.match(r"(?:open)?Pangu[-\d]", name, re.I):
            continue
        if row["模型类型"] not in {"文本生成", "文本对话", "多模态理解"}:
            continue
        version = row["模型版本"]
        if not re.fullmatch(r"\d{8}", version):
            # Older tables use V1/V2, which do not provide a daily release date.
            continue
        stamp = datetime.strptime(version, "%Y%m%d").date().isoformat()
        if row["预置服务"] not in {"√", "✓"}:
            continue
        yield Release(name, stamp, "开放 API", url, row.get("版本说明", name))


TENCENT_MODEL = re.compile(r"\b(?:Hy\d+(?:\.\d+)?(?:[- ](?:flash|preview|pro|lite|turbo))?|"
                           r"Hunyuan[- ][\w.-]+)\b", re.I)


def tencent_releases(soup, url):
    for row in named_tables(soup, ("动态名称", "动态描述", "发布时间")):
        if "新增支持" not in row["动态名称"]:
            continue
        for match in TENCENT_MODEL.finditer(row["动态描述"]):
            name = match[0]
            kind = "预览" if re.search(r"preview", name, re.I) else "开放 API"
            yield Release(name, iso(row["发布时间"]), kind, url, row["动态描述"])


QWEN_MODEL = re.compile(r"^(?:qwen(?![- ]?(?:image|audio|tts|asr|omni-realtime))|qwq|qvq)", re.I)


def bailian_releases(soup, url):
    updates = soup.select(".rkb-update")
    if not updates:
        raise ValueError("missing Model Studio dated update sections")
    for update in updates:
        heading = update.select_one(".rkb-update-meta h3")
        if not heading:
            raise ValueError("missing Model Studio release date")
        stamp = iso(text(heading))
        for body in update.select(".rkb-update-body"):
            model = body.find("h4")
            if not model or not QWEN_MODEL.search(text(model)):
                continue
            name = text(model)
            link = next((a for a in body.select("a[href]")
                         if re.sub(r"[-\s]", "", text(a)).casefold()
                         == re.sub(r"[-\s]", "", name).casefold()), None)
            target = first_party(url, link["href"]) if link else url + "#" + update["id"]
            kind = "预览" if re.search(r"preview|预览", name, re.I) else "开放 API"
            yield Release(name, stamp, kind, target, text(body))


def thinking_releases(soup, url, get):
    cards = soup.select("a.post-item-link")
    if not cards:
        raise ValueError("missing Thinking Machines news index")
    for card in cards:
        title = card.find("h2")
        if not title:
            raise ValueError("missing Thinking Machines news title")
        title = text(title)
        match = re.match(r"(?:Introducing\s+)?(Inkling(?:[- ]\d+)?(?:[- ]Small)?)\b", title, re.I)
        if not match:
            continue
        name = match[1].replace("-", " ")
        stamp = card.find("time", datetime=True)
        if not stamp:
            raise ValueError("missing Thinking Machines publication date")
        published = iso(stamp["datetime"][:10])
        target = first_party(url, card["href"])
        response = get(target)
        first_party(url, response.url)
        article = BeautifulSoup(response.content, "html.parser")
        heading = article.select_one("main h1")
        if not heading or text(heading) != title:
            raise ValueError("Thinking Machines announcement title differs from index")
        body = article.select_one("article.content")
        if not body:
            raise ValueError("missing Thinking Machines announcement body")
        # Re-read dated updates even for known articles: weights can follow the
        # initial closed launch in the same article, without changing its URL.
        yield Release(name, published, "发布", target, title)
        for quote in body.select("blockquote"):
            content = text(quote)
            update = re.match(r"([A-Z][a-z]{2} \d{1,2}, \d{4}) Update:\s*(.*)", content)
            if not update:
                continue
            weight_claim = (re.escape(name).replace(r"\ ", r"[- ]")
                            + r"(?:[’']s)?\s+weights are now available\b")
            if re.search(weight_claim, update[2], re.I):
                when = datetime.strptime(update[1], "%b %d, %Y").date().isoformat()
                if when < published:
                    raise ValueError("weight release precedes the model announcement")
                yield Release(name, when, "开放权重", target, update[2])


PARSERS = {
    "huawei-maas": huawei_releases,
    "tencent-tokenhub": tencent_releases,
    "bailian-releases": bailian_releases,
}
