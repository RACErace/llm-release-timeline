"""Read public organization listings from Hugging Face and ModelScope."""

from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import requests


def recent_models(pages, modified_field, since):
    """Walk newest-first pages through the previous check, with a day of overlap."""
    cutoff = datetime.fromisoformat(since) - timedelta(hours=24) if since else None
    for rows in pages:
        if not isinstance(rows, list) or (not rows and not since):
            raise ValueError("expected a non-empty initial public organization listing")
        for row in rows:
            updated = row[modified_field]
            if cutoff and datetime.fromisoformat(updated) < cutoff:
                return
            yield row, updated
        if not since:
            return


def huggingface_pages(owner, request):
    url = "https://huggingface.co/api/models"
    params = dict(author=owner, sort="lastModified", direction=-1, limit=50, full="true")
    while url:
        response = request("GET", url, params=params)
        yield response.json()
        url = response.links.get("next", {}).get("url")
        if url and urlsplit(url).netloc != "huggingface.co":
            raise ValueError("invalid pagination host")
        params = None


def huggingface_models(owner, since, request):
    return recent_models(huggingface_pages(owner, request), "lastModified", since)


def modelscope_pages(owner, request):
    url = "https://modelscope.cn/openapi/v1/models"
    number, size = 1, 50
    while True:
        params = dict(
            owner=owner, sort="last_modified", page_number=number, page_size=size
        )
        data = request("GET", url, params=params).json()["data"]
        rows = data["models"]
        if not rows or len(rows) != min(size, data["total_count"] - (number - 1) * size):
            raise ValueError("incomplete ModelScope OpenAPI page")
        yield rows
        if number * size >= data["total_count"]:
            return
        number += 1


def legacy_model(row):
    visibility = row["Visibility"]
    gated = visibility == 1 and row["ProtectedMode"] == 1
    if visibility != 5 and not gated:
        raise ValueError("unexpected visibility in public ModelScope listing")
    return {
        "id": row["Path"] + "/" + row["Name"],
        "last_modified": datetime.fromtimestamp(row["LastUpdatedTime"], UTC).isoformat(),
        "tasks": [task["Name"] for task in row.get("Tasks") or []],
        "tags": ["library:" + lib for lib in row.get("Libraries") or []],
        "params": ((row.get("ModelInfos") or {}).get("safetensor") or {}).get(
            "model_size"
        ),
        "gated": gated,
    }


def modelscope_inventory(owner, request):
    # The domestic SDK uses PUT for this read-only query. Offsets can shift on
    # tied timestamps, so check the unique inventory before accepting any rows.
    url = "https://modelscope.cn/api/v1/models/"
    number, size = 1, 500
    inventory = {}
    total = None
    while True:
        query = dict(Path=owner, PageNumber=number, PageSize=size, SortBy="GmtModified")
        # Include the list parameters in the URL as well as the required body
        # so cached responses are specific to the owner, page and ordering.
        payload = request("PUT", url, params=query, json=query).json()
        if payload.get("Success") is not True or payload["Code"] != 200:
            raise ValueError("ModelScope SDK listing returned an application error")
        data = payload["Data"]
        if total is None:
            total = data["TotalCount"]
        if total != data["TotalCount"] or not data["Models"]:
            raise ValueError("incomplete ModelScope SDK inventory")
        for raw in data["Models"]:
            if raw["Path"].casefold() != owner.casefold():
                raise ValueError("unexpected owner in ModelScope SDK inventory")
            row = legacy_model(raw)
            if row["id"] in inventory:
                raise ValueError("duplicate model in ModelScope SDK inventory")
            inventory[row["id"]] = row
        if len(inventory) > total:
            raise ValueError("inconsistent ModelScope SDK inventory count")
        if len(inventory) == total:
            return sorted(
                inventory.values(), key=lambda row: row["last_modified"], reverse=True
            )
        number += 1


def modelscope_models(owner, since, request):
    try:
        # Complete the requested window before returning it. A later page can
        # fail even when page one succeeded.
        pages = modelscope_pages(owner, request)
        return list(recent_models(pages, "last_modified", since))
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code not in {500, 502, 503, 504}:
            raise
        reason = "HTTP " + str(exc.response.status_code)
    except (requests.Timeout, requests.ConnectionError) as exc:
        reason = type(exc).__name__

    print(f"modelscope:{owner} OpenAPI {reason}，改用国内 SDK 模型目录。", flush=True)
    rows = modelscope_inventory(owner, request)
    print(f"modelscope:{owner} 国内 SDK 目录校验通过，共 {len(rows)} 个唯一模型。", flush=True)
    return recent_models([rows], "last_modified", since)
