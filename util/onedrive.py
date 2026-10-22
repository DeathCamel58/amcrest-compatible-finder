import os
from urllib.parse import parse_qs, quote, urlparse

import requests
from requests import HTTPError

from util import http

# Helpers for public OneDrive (personal) file links, e.g. https://1drv.ms/u/s!... Since the move of personal OneDrive
# to SharePoint, opening a share link redirects to onedrive.live.com with the file's path ("spopath") and sets an
# anonymous session cookie, which download.aspx then accepts for that file

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def open_share(url):
    """(session with the share's cookie, the file's path on OneDrive) for a share link."""
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    response = session.get(url, timeout=(10, 60))
    response.raise_for_status()
    query = parse_qs(urlparse(response.url).query)
    path = (query.get("spopath") or query.get("id") or [None])[0]
    if not path or not path.startswith("/personal/"):
        raise HTTPError(f"OneDrive link {url} didn't lead to a file ({response.url[:200]})")
    return session, path


def file_name(url):
    return open_share(url)[1].rsplit("/", 1)[-1]


def download(url, part_name):
    """Download a public OneDrive file (by its share link) to part_name, resuming a .part the pipeline left."""
    session, path = open_share(url)
    site = "/".join(path.split("/")[:3])
    download_url = f"https://onedrive.live.com{site}/_layouts/15/download.aspx?SourceUrl={quote(path)}"
    have = os.path.getsize(part_name) if os.path.exists(part_name) else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with session.get(download_url, stream=True, headers=headers, timeout=(10, 120)) as response:
        response.raise_for_status()
        if (response.headers.get("Content-Type") or "").startswith("text/html"):
            raise HTTPError(f"OneDrive returned a page instead of the file for {url}")
        resumed = have and response.status_code == 206
        with open(part_name, "ab" if resumed else "wb") as f:
            for chunk in http.iter_content_with_min_rate(response):
                f.write(chunk)


def is_onedrive(url):
    host = (urlparse(url).hostname or "").lower()
    return host in ("1drv.ms", "onedrive.live.com")
