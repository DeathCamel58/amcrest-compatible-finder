import json
import os
from urllib.parse import unquote

from util import http
from util.file_type import classify_file_type
from util.oem_helpers import is_dahua_firmware_name, make_firmware

name = "Dahua asset uploads (www.dahuasecurity.com/asset/upload)"
vendor = "Dahua"
# Files Dahua's old website linked from its product pages; the folders can't be listed, so this uses URLs known to
# exist: ones verified by the 2026-10-05 source search, plus what the Wayback Machine captured under the path
kind = "vendor"

prefix = "www.dahuasecurity.com/asset/upload/uploads/soft/"
KNOWN_URLS = os.path.join(os.path.dirname(__file__), "data", "dahua_asset_upload_urls.json")


def wayback_urls():
    """Live URLs of the firmware files the Wayback Machine captured under the path (an empty list if CDX fails)."""
    try:
        response = http.get("https://web.archive.org/cdx/search/cdx", timeout=(15, 120), params={
            "url": prefix, "matchType": "prefix", "output": "json", "fl": "original",
            "filter": "statuscode:200", "collapse": "urlkey"})
        rows = response.json()[1:] if response.ok and response.text.strip() else []
    except Exception as err:
        print(f"\tDahua asset uploads: Wayback lookup failed ({err!r}); using the known URLs only")
        return []
    return [row[0].replace("http://", "https://", 1).replace(":80/", "/") for row in rows]


def is_wanted_file(url):
    file_name = unquote(url.rsplit("/", 1)[-1])
    return (file_name.lower().endswith((".bin", ".zip", ".rar")) and is_dahua_firmware_name(file_name)
            and classify_file_type(file_name, url) == "firmware")


def get_firmwares():
    with open(KNOWN_URLS) as f:
        urls = json.load(f)
    urls = sorted({url for url in urls + wayback_urls() if is_wanted_file(url)})
    return [make_firmware([], url, notes="Linked from Dahua's old product pages") for url in urls]
