import json
import os
import re
import threading
import time
from urllib.parse import quote

from requests import HTTPError

from util import http
from util.download_firmware import _http_download
from util.oem_helpers import make_firmware

name = "Softpedia (drivers.softpedia.com)"
vendor = "Softpedia"
# A driver download site that kept old Dahua, Amcrest and Lorex firmware (2013-2016 recorders and IP cameras), one
# product page per model. Its pages are behind Cloudflare and its download links are signed and expire within hours,
# so the catalogue is a fixed list (brands/data/softpedia_pages.json, mapped 2026-10-22; Softpedia adds nothing new
# for these brands) and each download gets a fresh link from the product page
kind = "mirror"

PAGES_FILE = os.path.join(os.path.dirname(__file__), "data", "softpedia_pages.json")
# The page script holds the POST body for its download dialog: "t=15&y=25&zgz=<token>&id=<prog id>&tsf="
DOWNLOAD_PARAMS = re.compile(r"body: new URLSearchParams\('([^']+)'\+spjs_prog_tsf")
# Server 4 is Softpedia's own US server; the "external mirror" gives no usable link
POSTDOWNLOAD_LINK = re.compile(r'href="([^"]*dyn-postdownload\.php/[^"]*/4/1)"')
# The folder in the path can have a space ("drivers/network camera/..."), so the link runs to the closing quote
SECURE_LINK = re.compile(r'https://[a-z0-9]+\.softpedia-secure-download\.com/dl/[^"\'<>]+')
# Cloudflare starts answering with empty pages after a few dozen quick loads
PAGE_INTERVAL = 22
_pace_lock = threading.Lock()
_last_page = [0.0]


def load_pages():
    with open(PAGES_FILE) as f:
        return json.load(f)


def get_firmwares():
    firmwares = []
    for page in load_pages():
        firmware = make_firmware([page["model"]], page["page"], file_name=page["file_name"],
                                 notes=f"Softpedia: {page['title']}")
        firmware["vendor"] = page["vendor"]
        firmware["firmware_size"] = page.get("size")
        firmwares.append(firmware)
    return firmwares


def signed_link(page_url):
    """A fresh signed download link from a product page, by running its download dialog in a stealth browser."""
    from scrapling.fetchers import StealthyFetcher

    result = {}

    def action(page):
        params = DOWNLOAD_PARAMS.search(page.content())
        if not params:
            result["error"] = "no download dialog on the page"
            return
        dialog = page.evaluate("async (p) => { const r = await fetch('/_xaja/dlinfo.php', "
                               "{method: 'POST', body: new URLSearchParams(p)}); return await r.text(); }",
                               params.group(1) + "0&skipa=0")
        link = POSTDOWNLOAD_LINK.search(dialog)
        if not link:
            result["error"] = "no download server in the dialog"
            return
        postdownload = page.evaluate("async (u) => { const r = await fetch(u); return await r.text(); }",
                                     link.group(1).replace("&amp;", "&"))
        secure = SECURE_LINK.search(postdownload)
        if secure:
            result["url"] = secure.group(0).replace("&amp;", "&")
        else:
            result["error"] = "no download link on the post-download page"

    with _pace_lock:
        wait = _last_page[0] + PAGE_INTERVAL - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_page[0] = time.time()
    with http._browser_lock:
        StealthyFetcher.fetch(page_url, headless=True, solve_cloudflare=True, network_idle=True, page_action=action)
    if "url" not in result:
        raise HTTPError(f"Softpedia: {result.get('error', 'no download link')} ({page_url})")
    return quote(result["url"], safe=":/?&=%()")


def download_file(url, part_name):
    link = signed_link(url)
    # A link with a bad signature answers 206 with a 13-byte "404 Not Found" page
    with http.get(link, stream=True, headers={"Range": "bytes=0-0"}, timeout=(10, 60)) as response:
        response.raise_for_status()
        if (response.headers.get("Content-Type") or "").startswith("text/html"):
            raise HTTPError(f"Softpedia served a page instead of the file for {url}")
    _http_download(link, part_name)
