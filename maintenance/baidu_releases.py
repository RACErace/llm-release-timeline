"""Read dated ERNIE model announcements from Baidu's Chinese research index."""

import re
from datetime import date
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from model_scope import announcement_in_scope


MODEL = re.compile(
    r"^(?:ERNIE|文心)[ -]?X?\d+(?:\.\d+)*"
    r"(?:[ -]+(?!API\b)[a-z\d]+(?:\.[a-z\d]+)*)*", re.I,
)
PLANNED = re.compile(r"将(?:于|会|在|向|开放|开源|陆续)|即将|届时|计划|拟于|预告|尚未|暂未|未(?:开放|开源|上线)")
WEIGHTS = re.compile(
    r"(?:发布的|开源|开放|发布|上线)(?:了|模型|预训练终点|全部|完整|基础)*权重|"
    r"权重.{0,22}(?:开源|已开放|已发布|可下载)"
)
API = re.compile(r"API.{0,30}(?:上线|开放)|(?:上线|开放).{0,8}API", re.I)
SPECIALIZED = re.compile(
    r"(?:专项|任务|领域|专用)(?:的|级|定向)?微调|(?:量化|合并|适配器)(?:版|模型|权重)|"
    r"文生图|文生视频|专用嵌入|提示词扩写", re.I,
)


def release_cards(soup, index_url):
    cards = soup.select("main a.article-link")
    if not cards:
        raise ValueError("missing Baidu research article index")
    for card in cards:
        category = card.select_one(".meta span")
        heading = card.select_one("h2, h3")
        if not category or not heading:
            raise ValueError("missing Baidu article title or category")
        if category.get_text(strip=True).split("/")[-1].strip() != "产品动态":
            continue
        title = heading.get_text(" ", strip=True)
        model = MODEL.match(title)
        if not model or not announcement_in_scope(title) or SPECIALIZED.search(title):
            continue
        url = urlsplit(urljoin(index_url, card["href"]))
        if url.scheme != "https" or url.netloc != "ernie.baidu.com":
            continue
        stamp = card.select_one("time[datetime]")
        if not stamp:
            raise ValueError("missing Baidu publication date: " + url.path)
        published = date.fromisoformat(stamp["datetime"]).isoformat()
        yield model[0], title, url._replace(query="", fragment="").geturl(), published


def read_announcement(response, model, title, published):
    final_url = urlsplit(response.url)
    if final_url.scheme != "https" or final_url.netloc != "ernie.baidu.com":
        raise ValueError("Baidu announcement redirected outside the publisher site")
    page = BeautifulSoup(response.content, "html.parser")
    main = page.select_one("main")
    if not main or not main.select_one("h1") or not main.select_one("article"):
        raise ValueError("missing Baidu announcement")
    heading = MODEL.match(main.h1.get_text(" ", strip=True))
    if not heading or re.split(r"[\s-]+", heading[0].casefold()) != re.split(r"[\s-]+", model.casefold()):
        raise ValueError("Baidu article model differs from the index: " + model)
    metadata = main.select_one(".release-date, .article-meta")
    if metadata and (stamp := re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", metadata.get_text(strip=True))):
        if date(*map(int, stamp.groups())).isoformat() != published:
            raise ValueError("Baidu publication date differs from the index: " + model)
    paragraphs = [
        text for p in main.select(".article-body p, .article-section p")
        if (text := p.get_text(" ", strip=True))
    ]
    if not paragraphs:
        raise ValueError("missing Baidu announcement text")
    intro = " ".join(paragraphs[:4])
    if any(SPECIALIZED.search(p) for p in paragraphs[:2] if model.casefold() in p.casefold()):
        return None
    # A product post can describe a launch today and promise weights for a
    # later date. Only a present or completed action can establish its stage.
    clauses = [
        part for part in re.split(r"[，,。！？；;\n]", title + "。" + intro)
        if not PLANNED.search(part)
    ]
    if not any(re.search(r"发布|推出|上线|开放|开源|预览", part) for part in clauses):
        return None
    title_action = title[len(model):].strip(" ：:")
    weight_launch = WEIGHTS.search(title) or re.fullmatch(r"(?:模型|系列|正式|全面|现已)*开源[！!。]?", title_action)
    opened_weights = any(WEIGHTS.search(part) for part in clauses if not re.search(r"此前|此前的|之前|上周|上月|去年", part))
    if (not PLANNED.search(title) and weight_launch) or opened_weights:
        kind = "开放权重"
    elif not PLANNED.search(title) and API.search(title):
        kind = "开放 API"
    elif re.search(r"\b(?:Preview|Beta)\b|预览|公测", model + " " + title, re.I):
        kind = "预览"
    elif any(API.search(part) for part in clauses):
        kind = "开放 API"
    else:
        kind = "发布"
    return kind, intro
