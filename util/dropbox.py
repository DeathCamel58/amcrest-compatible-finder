import re
import threading
from urllib.parse import parse_qs, urlparse

from util import http

# Dropbox's shared-link pages render their listing with JavaScript, but the web UI's own JSON endpoint works for
# anonymous visitors once the page has set a CSRF ("t") cookie
LIST_URL = "https://www.dropbox.com/list_shared_link_folder_entries"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"

_local = threading.local()


def parse_folder_link(url):
    """https://www.dropbox.com/scl/fo/<link_key>/<secure_hash>?rlkey=<rlkey> -> (link_key, secure_hash, rlkey)"""
    parsed = urlparse(url)
    match = re.match(r"^/scl/fo/([^/]+)/([^/?]+)", parsed.path)
    if not match:
        raise ValueError(f"Not a Dropbox shared folder link: {url}")
    rlkey = parse_qs(parsed.query).get("rlkey", [None])[0]
    return match.group(1), match.group(2), rlkey


def resolve_link(url):
    """Follow redirects (e.g. a vendor's /downloads/ page or an old /sh/ link) to the /scl/fo/ folder link."""
    response = http.get(url, headers={"User-Agent": USER_AGENT})
    response.close()
    return response.url


def get_csrf_token():
    # The first GET of any Dropbox page sets the "t" cookie the listing endpoint checks
    if not getattr(_local, "token", None):
        response = http.get_session().get("https://www.dropbox.com/", headers={"User-Agent": USER_AGENT},
                                          timeout=http.TIMEOUT)
        response.close()
        _local.token = http.get_session().cookies.get("t", domain=".dropbox.com") or \
            http.get_session().cookies.get("t")
    return _local.token


def list_entries(link_key, secure_hash, rlkey, sub_path=""):
    """One folder's entries and the share tokens for its sub-folders (each sub-folder has its own secure hash)."""
    entries, tokens, voucher = [], [], None
    while True:
        data = {
            "is_xhr": "true",
            "t": get_csrf_token(),
            "link_key": link_key,
            "link_type": "c",
            "secure_hash": secure_hash,
            "rlkey": rlkey or "",
            "sub_path": sub_path,
        }
        if voucher:
            data["voucher"] = voucher
        response = http.get_session().post(LIST_URL, data=data, timeout=http.TIMEOUT,
                                           headers={"User-Agent": USER_AGENT, "Origin": "https://www.dropbox.com"})
        response.raise_for_status()
        page = response.json()
        entries += page.get("entries") or []
        tokens += page.get("share_tokens") or []
        voucher = page.get("next_request_voucher")
        if not page.get("has_more_entries") or not voucher:
            return entries, tokens


def walk_folder(url, max_depth=8, top_folders=None):
    """Every file below a public Dropbox shared folder. top_folders limits the walk to those top-level folders.

    Returns [{"path": "Product Firmware/LSNVR8CH", "file_name": ..., "size": bytes or None, "url": dl=1 link}]."""
    link_key, secure_hash, rlkey = parse_folder_link(url)
    files = []

    def walk(secure_hash, sub_path, depth):
        if depth > max_depth:
            return
        entries, tokens = list_entries(link_key, secure_hash, rlkey, sub_path)
        hashes = {token.get("subPath"): token.get("secureHash") for token in tokens}
        for entry in entries:
            path = f"{sub_path}/{entry['filename']}"
            if entry.get("is_dir"):
                if depth == 0 and top_folders is not None and entry["filename"] not in top_folders:
                    continue
                if hashes.get(path):
                    walk(hashes[path], path, depth + 1)
                continue
            files.append({
                "path": sub_path.strip("/"),
                "file_name": entry["filename"],
                "size": entry.get("bytes"),
                "url": to_download_url(entry["href"]),
            })

    walk(secure_hash, "", 0)
    return files


def to_download_url(href):
    """A shared-link URL with dl=1, which serves the file itself instead of the preview page."""
    href = re.sub(r"([?&])dl=0", r"\1dl=1", href)
    return href if "dl=1" in href else href + ("&" if "?" in href else "?") + "dl=1"


def download(url, part_name):
    with http.get(to_download_url(url), stream=True, headers={"User-Agent": USER_AGENT}) as r:
        r.raise_for_status()
        if "text/html" in r.headers.get("Content-Type", ""):
            from requests import HTTPError
            raise HTTPError(f"Dropbox returned a page instead of the file: {url}")
        with open(part_name, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
