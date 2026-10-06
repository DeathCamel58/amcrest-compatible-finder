import re
import threading
import time
from urllib.parse import parse_qs, urlparse

from util import http
from util.download_firmware import download_checked
from util.oem_helpers import parse_dahua_version

name = "Dahua China support portal (support.dahuatech.com)"
vendor = "Dahua"

# The Chinese support site's "upgrade packages" (升级程序) section: Chinese-language recorder, HCVR, IPC and PTZ
# firmware that the international download center doesn't list
api_url = "https://supportapi.dahuatech.com/support/api/extend/tools-software/pageFront"
headers = {"Content-Type": "application/json", "From-Type": "pc", "User-Agent": "Mozilla/5.0"}

# Category 18 is "upgrade packages"; 17 is SDKs. The category tree the site shows anonymously leaves 18 out, but
# pageFront answers for any category ID, so ask for a range of IDs at once and keep the ones under 18
upgrade_root_category = 18
category_ids = list(range(1, 3000))

# The upgrade section also holds tools, plugins (插件), instructions (升级方法) and spreadsheets
not_firmware = re.compile(r"DSS|插件|\.xlsx$|升级方法|说明", re.IGNORECASE)
firmware_name = re.compile(r"_V\d+\.\d{3}\.")
date = re.compile(r"^\d{4}-\d{2}-\d{2}$")
md5_pattern = re.compile(r"^[0-9a-fA-F]{32}$")

# The download links are signed (auth_key=<expiry>-...) and expire after about 4 hours, so a long run fetches a
# fresh one before downloading. Download URL -> item id, and item id -> its published MD5
item_ids = {}
md5_by_id = {}
# Seconds of validity a link needs left to be used as it is
EXPIRY_MARGIN = 300
_refresh_lock = threading.Lock()


def get_items():
    body = {"data": {"categoryIds": category_ids, "categoryType": "his"}, "pageNo": 1, "pageSize": 1000}
    response = http.post(api_url, json=body, headers=headers)
    response.raise_for_status()
    data = response.json()
    if data.get("code") != 200:
        raise ValueError(f"Dahua China API returned {data.get('code')}: {data.get('message')}")
    return data["data"]["data"]


def is_firmware_item(item):
    file_name = item.get("attachmentName") or ""
    return (item.get("rootParentCategoryId") == upgrade_root_category and bool(item.get("publicDownloadUrl"))
            and bool(firmware_name.search(file_name)) and not not_firmware.search(file_name))


def link_expiry(url):
    """The Unix time a signed link expires (auth_key's first field), or None for an unsigned link."""
    auth_key = parse_qs(urlparse(url).query).get("auth_key", [""])[0]
    expiry = auth_key.split("-", 1)[0]
    return int(expiry) if expiry.isdigit() else None


def to_firmware(item):
    file_name = item["attachmentName"]
    version, build_date = parse_dahua_version(file_name)
    # The content lists the models: "此版本适用于以下型号：DH-NVR4104HS-4KS2/L,DH-NVR4108HS-4KS2/L,..."
    models = sorted(set(re.findall(r"DHI?-[A-Z0-9][A-Z0-9/()\-]+", item.get("content") or "")))
    posted = item.get("updateTime") or ""
    md5 = item.get("fileMd5") or ""
    language = "Chinese-language build" if "_Chn_" in file_name else None
    return {
        "camera_name": models or [item.get("title") or file_name],
        "firmware_version": version,
        "firmware_size": None,
        "firmware_notes": "; ".join(n for n in (language, item.get("categoryName")) if n) or None,
        "firmware_changelog": None,
        "firmware_previous": None,
        "firmware_latest": item["publicDownloadUrl"],
        "firmware_latest_file_name": file_name,
        # When the portal published it; the build date in the name is a fallback
        "release_date": posted if date.match(posted) else build_date,
        "md5": md5.lower() if md5_pattern.match(md5) else None,
        "sha256": None,
    }


def get_firmwares():
    firmwares = []
    for item in get_items():
        if not is_firmware_item(item):
            continue
        firmware = to_firmware(item)
        item_ids[firmware["firmware_latest"]] = item["id"]
        if firmware["md5"]:
            md5_by_id[item["id"]] = firmware["md5"]
        firmwares.append(firmware)
    return firmwares


def fresh_url(url):
    """url, or a newly signed link for the same file if url has expired (or nearly has)."""
    expiry = link_expiry(url)
    if expiry is None or expiry - time.time() > EXPIRY_MARGIN or url not in item_ids:
        return url
    with _refresh_lock:
        for item in get_items():
            if item["id"] == item_ids[url] and item.get("publicDownloadUrl"):
                return item["publicDownloadUrl"]
    return url


def download_file(url, part_name):
    item_id = item_ids.get(url)
    listed_url = url
    # HEAD requests to this host hang, so only GET is used. Keyed by the listed URL, so a mismatch is recognised as
    # the same across a refreshed link
    download_checked(listed_url, part_name, md5_by_id.get(item_id), fetch_url=fresh_url(url))
