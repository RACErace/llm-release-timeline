"""Discover Mercury announcements in the search index published by Inception."""

import re
from datetime import datetime
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from publisher_releases import first_party, text
from release_text import headline_model, normalized_text


def model_articles(home, base, fetch):
    meta = home.select_one('meta[name="framer-search-index"][content]')
    if not meta:
        raise ValueError("missing Inception site search index")
    index = urlsplit(urljoin(base, meta["content"]))
    if index.scheme != "https" or index.netloc not in {urlsplit(base).netloc, "framerusercontent.com"}:
        raise ValueError("unexpected Inception search index host")
    entries = fetch(index.geturl()).json()
    if not isinstance(entries, dict):
        raise ValueError("invalid Inception site search index")
    articles = {path: entry for path, entry in entries.items() if path.rstrip("/").startswith("/blog/")}
    if not articles:
        raise ValueError("missing Inception blog articles in site search index")
    for path, entry in articles.items():
        headings = entry.get("h1") if isinstance(entry, dict) else None
        heading = headings[0] if isinstance(headings, list) and headings else None
        if not isinstance(heading, str) or not heading.strip():
            raise ValueError("missing Inception blog headline: " + path)
        title = normalized_text(heading)
        if not re.search(r"introduc|launch|releas|unveil|announc|\bmeet\b", title, re.I):
            continue
        name = headline_model(title, "Inception Labs")
        if name and not re.search(r"voice|audio|speech", name, re.I):
            yield name, title, first_party(base, path)


def publication_date(response, url, title):
    soup = BeautifulSoup(response.content, "html.parser")
    if response.url.rstrip("/") != url.rstrip("/") or not any(
        normalized_text(text(h)) == title for h in soup.select("h1")
    ):
        raise ValueError("Inception article differs from its index entry: " + url)
    stamp = soup.select_one('meta[property="article:published_time"][content], meta[itemprop="datePublished"][content]')
    if stamp:
        return datetime.fromisoformat(stamp["content"]).date().isoformat()
    for stamp in soup.select("time[datetime]"):
        link = stamp.find_parent("a", href=True)
        if link:
            if urljoin(response.url, link["href"]).rstrip("/") != url.rstrip("/"):
                continue
        elif stamp.get("itemprop") != "datePublished":
            continue
        return datetime.fromisoformat(stamp["datetime"]).date().isoformat()
    # Older posts may only show dates on links to other articles.
    # Leave an undated new candidate for the normal verification step.
    return ""
