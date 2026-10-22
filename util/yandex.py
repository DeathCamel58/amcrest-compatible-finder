import os
from urllib.parse import quote, unquote, urlparse

from requests import HTTPError

from util import http

# Helpers for public Yandex Disk links (yadi.sk/d/..., disk.yandex.*/d/...). The public API lists a shared folder and
# hands out download links without a login

API = "https://cloud-api.yandex.net/v1/disk/public/resources"
PAGE_SIZE = 1000


def split_url(url):
    """(public link, path inside it) for a file URL in the form <public link>[/<path>]; a single-file share has no
    path. Paths in these URLs are percent-encoded."""
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")
    # /d/<hash> or /i/<hash>, then the path
    public_url = f"{parsed.scheme}://{parsed.netloc}/{'/'.join(parts[:2])}"
    path = "/" + "/".join(unquote(part) for part in parts[2:]) if len(parts) > 2 else None
    return public_url, path


def file_url(public_url, path):
    return public_url.rstrip("/") + "/" + "/".join(quote(part) for part in path.strip("/").split("/"))


def list_resource(public_url, path=None, offset=0):
    params = {"public_key": public_url, "limit": PAGE_SIZE, "offset": offset}
    if path:
        params["path"] = path
    response = http.get(API, params=params, timeout=(10, 60))
    response.raise_for_status()
    return response.json()


def walk(public_url, max_depth=8):
    """Every file in a public folder (or the one file of a single-file share): dicts with name, path (inside the
    share, None for a single file), url (file_url), size, md5 and modified."""
    files = []

    def visit(path, depth):
        offset = 0
        while True:
            resource = list_resource(public_url, path, offset)
            if resource.get("type") == "file":
                files.append(to_file(resource, None))
                return
            items = resource.get("_embedded", {}).get("items", [])
            for item in items:
                if item.get("type") == "dir":
                    if depth < max_depth:
                        visit(item["path"], depth + 1)
                else:
                    files.append(to_file(item, item["path"]))
            offset += len(items)
            if not items or offset >= resource.get("_embedded", {}).get("total", 0):
                return

    def to_file(item, path):
        return {"name": item["name"], "path": path, "size": item.get("size"), "md5": item.get("md5"),
                "modified": item.get("modified"), "url": file_url(public_url, path) if path else public_url}

    visit(None, 0)
    return files


def direct_link(url):
    public_url, path = split_url(url)
    params = {"public_key": public_url}
    if path:
        params["path"] = path
    response = http.get(f"{API}/download", params=params, timeout=(10, 60))
    response.raise_for_status()
    return response.json()["href"]


def download(url, part_name):
    """Download a public Yandex Disk file (by its file_url) to part_name, resuming a .part the pipeline left."""
    href = direct_link(url)
    have = os.path.getsize(part_name) if os.path.exists(part_name) else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with http.get(href, stream=True, headers=headers, timeout=(10, 120)) as response:
        response.raise_for_status()
        if (response.headers.get("Content-Type") or "").startswith("text/html"):
            raise HTTPError(f"Yandex Disk returned a page instead of the file for {url}")
        resumed = have and response.status_code == 206
        with open(part_name, "ab" if resumed else "wb") as f:
            for chunk in http.iter_content_with_min_rate(response):
                f.write(chunk)


def is_yandex(url):
    host = (urlparse(url).hostname or "").lower()
    return host == "yadi.sk" or host.startswith("disk.yandex.")
