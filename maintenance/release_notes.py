"""Publisher changelogs: explicit dates and full model names, not product features."""

import re
from datetime import datetime
from urllib.parse import quote

from model_scope import announcement_in_scope
from publisher_releases import Release, first_party, iso, named_tables, text
from release_text import headline_model, normalized_text


DATE = re.compile(r"\b\d{4}-\d\d-\d\d\b")
MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"


def english_date(value):
    value = value.strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError("missing or invalid publisher date: " + value)


def stage(value, default="开放 API"):
    return "预览" if re.search(r"preview|\bbeta\b|预览|公测", value, re.I) else default


def section_text(heading, level=2):
    parts = []
    boundary = {"h" + str(n) for n in range(1, level + 1)}
    for node in heading.next_elements:
        if getattr(node, "name", None) in boundary:
            break
        if getattr(node, "name", None) in {"p", "li"} and not node.find_parent("li"):
            parts.append(text(node))
    return "\n".join(parts)


def deepseek(soup, url):
    headings = soup.select("article h3")
    if not headings:
        raise ValueError("missing DeepSeek release headings")
    for h in headings:
        title = text(h)
        if not re.match(r"DeepSeek[- ]", title, re.I):
            continue
        day = h.find_previous("h2")
        stamp = DATE.search(text(day)) if day else None
        if not stamp or not h.get("id"):
            raise ValueError("missing DeepSeek date or anchor")
        name = headline_model(title, "DeepSeek") or title
        kind = "版本更新" if re.search(r"\bUpdate\b", title, re.I) else stage(title, "发布")
        yield Release(name, iso(stamp[0]), kind, url + "#" + quote(h["id"]), section_text(h, 3))


def kimi(soup, url):
    headings = soup.select(".vp-doc h2")
    if not headings:
        raise ValueError("missing Kimi dated sections")
    for h in headings:
        title = text(h.select_one(".ignore-header") or h)
        if not re.match(r"(?:Kimi )?K\d", title, re.I):
            continue
        stamp = h.select_one(".wn-date")
        if not stamp or not h.get("id"):
            raise ValueError("missing Kimi release date or anchor")
        body = section_text(h)
        # Explicit model names in the launch paragraph preserve Code,
        # Highspeed, Preview, and other siblings beyond the first 180 chars.
        name = re.sub(r"\s+(?:Series is live|is live)$", "", title, flags=re.I)
        names = [name]
        names += re.findall(r"(?:also launched|also released|introducing)\s+"
                            r"((?:Kimi )?K\d+(?:\.\d+)*(?:-[\w.]+| [A-Z][a-zA-Z0-9]*)*)", body)
        names = sorted({"Kimi " + re.sub(r"^Kimi ", "", n, flags=re.I) for n in names})
        if not names:
            raise ValueError("missing model name in Kimi launch")
        for name in names:
            yield Release(name, english_date(text(stamp)), stage(name), url + "#" + quote(h["id"]), body)


def xai(soup, url):
    main = soup.select_one("main")
    if not main or not main.select("h3[id]"):
        raise ValueError("missing xAI release headings")
    # The current year is implicit. Derive it from explicit older year labels
    # and the December->January transition, never from the machine's clock.
    years, year, previous = {}, None, None
    for h in reversed(main.select("h2")):
        label = re.fullmatch(rf"({MONTH})(?: (\d{{4}}))?", text(h))
        if not label:
            continue
        month = datetime.strptime(label[1], "%B").month
        if label[2]:
            year = int(label[2])
        elif year and previous and month < previous:
            year += 1
        if year:
            years[id(h)] = year
        previous = month
    for h in main.select("h3[id]"):
        title = text(h)
        if not re.match(r"Grok[- ]\d", title, re.I) or re.search(r"retir|deprecat|\bin the EU\b", title, re.I):
            continue
        month = h.find_previous("h2")
        year = years.get(id(month))
        card = next((p for p in h.parents if p.name == "div" and "grid" in p.get("class", [])), None)
        label = card.find("div", recursive=False) if card else None
        if not year or not label:
            raise ValueError("missing xAI release year/date container")
        stamp = english_date(text(label) + " " + str(year))
        name = re.split(r"\s+(?:is|are|available)\b", title, maxsplit=1, flags=re.I)[0]
        yield Release(name, stamp, stage(title), url + "#" + quote(h["id"]), text(card))


def mercury(soup, url):
    cards = soup.select("a[href] time[datetime]")
    if not cards:
        raise ValueError("missing Inception dated blog cards")
    for stamp in cards:
        card = stamp.find_parent("a")
        h = card.select_one("h2, h3")
        if not h or not re.search(r"introduc|launch|releas|unveil", text(h), re.I):
            continue
        name = headline_model(text(h), "Inception Labs")
        if not name or re.search(r"voice|audio|speech", name, re.I):
            continue
        day = datetime.fromisoformat(stamp["datetime"]).date().isoformat()
        yield Release(name, day, stage(text(h), "发布"), first_party(url, card["href"]), text(h))


def longcat(soup, url):
    headings = soup.select("main h3[id]")
    if not headings:
        raise ValueError("missing LongCat release headings")
    for h in headings:
        match = re.match(r"(LongCat[-\w.]+)\s*(?:上线|发布)", text(h))
        if not match or not announcement_in_scope(match[1]):
            continue
        day = h.find_previous("h2")
        stamp = DATE.search(text(day)) if day else None
        if not stamp:
            raise ValueError("missing LongCat release date")
        yield Release(match[1], iso(stamp[0]), stage(match[1], "发布"), url + "#" + quote(h["id"]), section_text(h, 3))


def mimo(soup, url):
    headings = soup.select(".mdxContent h2")
    if not headings:
        raise ValueError("missing MiMo release sections")
    for h in headings:
        stamp = DATE.search(text(h))
        if not stamp:
            raise ValueError("missing MiMo release date")
        body = section_text(h)
        names = re.findall(r"\b(mimo-[a-z0-9.-]+)\s*[：:]", body, re.I)
        if not names:
            names = re.findall(r"\b(mimo-[a-z0-9.-]+)\b", text(h), re.I)
        for name in sorted(set(names)):
            if announcement_in_scope(name):
                yield Release(name, iso(stamp[0]), stage(name), url + "#" + quote(h["id"]), body)


def zai(soup, url):
    cards = soup.select("#content .update-container")
    if not cards:
        raise ValueError("missing Z.ai release entries")
    for card in cards:
        stamp = iso(card["id"])
        label = card.select_one("[data-component-part='update-label']")
        body = text(card).removeprefix(text(label) if label else stamp).strip()
        match = re.match(r"(GLM-[\w.-]+)\b", body)
        if match and announcement_in_scope(match[1]):
            yield Release(match[1], stamp, stage(match[1]), url + "#" + stamp, body)


def cohere(soup, url):
    labels = soup.select(".fern-changelog-label")
    if not labels:
        raise ValueError("missing Cohere dated changelog entries")
    for label in labels:
        link = label.select_one("a[href]")
        card = label.parent.parent
        h = card.select_one("h2")
        if not link or not h:
            raise ValueError("missing Cohere date/headline")
        title = re.sub(r"Cohere['’]s\s+", "", text(h))
        name = headline_model(title, "Cohere")
        if name and re.search(r"introduc|announc|releas|available", title, re.I):
            yield Release(name, english_date(text(link)), stage(title), first_party(url, link["href"]), text(card))


def minimax(soup, url):
    cards = soup.select("a[href] article")
    if not cards:
        raise ValueError("missing MiniMax research cards")
    for card in cards:
        h, stamp = card.select_one("h2"), card.select_one("time[datetime]")
        if not h or not stamp:
            raise ValueError("missing MiniMax research title/date")
        title = re.sub(r"^Introducing ", "", text(h))
        name = title.split(":", 1)[0]
        if not re.fullmatch(r"MiniMax[- ](?:M\d|Text-\d)[\w. -]*", name):
            continue
        # Use the publisher's displayed local date, not the previous UTC day.
        day = iso(text(stamp))
        yield Release(name, day, stage(title, "发布"), first_party(url, card.parent["href"]), title)


def doubao(soup, url):
    tables = soup.select("table")
    if not tables:
        raise ValueError("missing Doubao model release tables")
    found = False
    for table in tables:
        h = table.find_previous("h2")
        month = re.match(r"(\d{4})(\d{2})\b", text(h)) if h else None
        if not month:
            continue
        found = True
        # The document supplies month precision. Do not turn a model ID suffix
        # or the page's last-edit date into a fabricated release day.
        stamp = month[1] + "-" + month[2]
        iso(stamp + "-01")
        for row in named_tables(table, ("提供方", "模型 ID", "模型类型", "版本说明")):
            if row["提供方"] != "字节跳动" or row["模型类型"] not in {"深度思考模型", "文本生成模型", "角色模型", "视觉理解模型"}:
                continue
            name = row["模型 ID"]
            if not announcement_in_scope(name):
                continue
            kind = "版本更新" if row["版本说明"] == "更新" else stage(name)
            yield Release(name, stamp, kind, url + "#" + h["id"], row.get("其它说明", name))
    if not found:
        raise ValueError("missing Doubao release month")


def markdown_releases(raw, url, provider):
    """Official Markdown changelogs with one dated section per publication day."""
    sections = re.split(r"(?m)^## (.+)\s*$", raw)
    if len(sections) < 3 or not re.search(r"(?:Release notes|Changelog)", sections[0], re.I):
        raise ValueError("missing Markdown release sections")
    for heading, body in zip(sections[1::2], sections[2::2]):
        if provider == "Google":
            label = heading.split("{%", 1)[0].strip()
            stamp = english_date(label)
            anchor = re.search(r'id="([^"]+)"', heading)
        else:
            stamp, anchor = iso(heading.strip()), None
        # Only top-level bullets introduce releases. Nested bullets describe
        # parameters, prices, comparisons, or promised future availability.
        for bullet in re.split(r"(?m)^(?:\*|-)[ \t]+", body)[1:]:
            lead = re.split(r"\n(?=\s*[-*]\s)", bullet, maxsplit=1)[0]
            if provider == "Google":
                names = re.findall(r"`(gemini-[\w.-]+)`", lead)
            else:
                names = re.findall(r"`((?:mistral|magistral|ministral|codestral|devstral|leanstral)[\w.-]*)`", lead, re.I)
            if not re.search(r"releas|available|introduc|launch", lead, re.I) or re.search(r"deprecat|retir|price|pricing update|shut down", lead.split("\n", 1)[0], re.I):
                continue
            for name in sorted(set(names)):
                if not announcement_in_scope(name) or re.search(r"embedding|decision|image|video|audio|tts|transcri|robotics|computer-use", name, re.I):
                    continue
                if re.search(r"(?:video|image|music|speech)[ -](?:generation|editing|synthesis)", lead, re.I) and not re.search(r"text generation|language model", lead, re.I):
                    continue
                target = url.removesuffix(".md") + "#" + (anchor[1] if anchor else stamp)
                kind = stage(lead, "发布" if re.search(r"\breleased?\b", lead, re.I) else "开放 API")
                yield Release(name, stamp, kind, target, normalized_text(lead))


PARSERS = {"deepseek": deepseek, "kimi": kimi, "xai": xai, "mercury": mercury,
           "longcat-api": longcat, "mimo-api": mimo, "zai-api": zai, "cohere-api": cohere,
           "minimax-news": minimax, "doubao-api": doubao}
