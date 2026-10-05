import re
from urllib.parse import unquote, urlparse

from requests import HTTPError

from util import http

# Helpers for public MediaFire files and folders. The listed URL is a file's page (www.mediafire.com/file/<key>/...),
# which stays valid; its direct download link (downloadNNN.mediafire.com/...) expires, so it's looked up at download time

FILE_LINK = re.compile(r"https?://(?:www\.)?mediafire\.com/(?:file|download)/(\w+)(?:/([^/?#\"'<>]+))?", re.IGNORECASE)
DIRECT_LINK = re.compile(r'href="(https?://download\d*\.mediafire\.com/[^"]+)"')
FOLDER_API = "https://www.mediafire.com/api/1.5/folder/get_content.php"


def file_links(html):
    """[(quickkey, file name or None, page url)] for every MediaFire file link in a page, first occurrence only."""
    seen = {}
    for match in FILE_LINK.finditer(html):
        key, name = match.group(1), match.group(2)
        if key in seen:
            continue
        name = unquote(unquote(name)) if name and name.lower() != "file" else None
        seen[key] = (key, name, f"https://www.mediafire.com/file/{key}/{match.group(2) or 'file'}/file")
    return list(seen.values())


def list_folder(folder_key, chunk_size=100, max_chunks=50):
    """Files in a public folder (not its subfolders): dicts with name, size, sha256, quickkey and page url."""
    files = []
    for chunk in range(1, max_chunks + 1):
        response = http.get(FOLDER_API, params={"folder_key": folder_key, "content_type": "files",
                                                "response_format": "json", "chunk": chunk,
                                                "chunk_size": chunk_size}, timeout=(10, 60))
        response.raise_for_status()
        content = response.json()["response"]["folder_content"]
        for item in content.get("files") or []:
            files.append({
                "name": item["filename"],
                "size": int(item["size"]) if str(item.get("size", "")).isdigit() else None,
                "sha256": item.get("hash") if re.fullmatch(r"[0-9a-f]{64}", item.get("hash") or "") else None,
                "quickkey": item["quickkey"],
                "url": f"https://www.mediafire.com/file/{item['quickkey']}/file",
                "created": item.get("created_utc"),
            })
        if content.get("more_chunks") != "yes":
            break
    return files


def direct_link(page_url):
    """The current direct download link of a file page."""
    response = http.get(page_url, timeout=(10, 60))
    response.raise_for_status()
    match = DIRECT_LINK.search(response.text)
    if not match:
        # The file was removed, or MediaFire showed an interstitial page
        raise HTTPError(f"no download link on {page_url}")
    return match.group(1)


def download(page_url, part_name):
    """Download a MediaFire file (by its page URL) to part_name, resuming a .part the pipeline left."""
    import os
    url = direct_link(page_url)
    have = os.path.getsize(part_name) if os.path.exists(part_name) else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with http.get(url, stream=True, headers=headers, timeout=(10, 120)) as response:
        response.raise_for_status()
        if (response.headers.get("Content-Type") or "").startswith("text/html"):
            raise HTTPError(f"MediaFire returned a page instead of the file for {page_url}")
        resumed = have and response.status_code == 206
        with open(part_name, "ab" if resumed else "wb") as f:
            for chunk in http.iter_content_with_min_rate(response):
                f.write(chunk)


def file_name_from_url(url):
    match = FILE_LINK.search(url)
    if match and match.group(2) and match.group(2).lower() != "file":
        return unquote(unquote(match.group(2)))
    return unquote(urlparse(url).path.rstrip("/").split("/")[-1])
