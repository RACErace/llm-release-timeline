"""Shared GitHub CLI transport for the collector and release maintainer."""

import json
import subprocess

LABEL = "release-candidate"


def api(endpoint, data=None, paginate=False, *, method=None, page_key=None):
    cmd = ["gh", "api", endpoint]
    if method:
        cmd.extend(["--method", method])
    if paginate:
        cmd.extend(["--paginate", "--slurp"])
    if data is not None:
        cmd.extend(["--input", "-"])
    result = subprocess.run(
        cmd,
        check=False,
        input=json.dumps(data, ensure_ascii=False) if data is not None else None,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=60,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[:500])
    value = json.loads(result.stdout)
    if paginate:
        return [
            item for page in value for item in (page[page_key] if page_key else page)
        ]
    return value
