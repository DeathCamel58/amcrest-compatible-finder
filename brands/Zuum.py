import re
from urllib.parse import urljoin, urlparse

from util import dropbox, http
from util.oem_helpers import is_dahua_firmware_name, make_firmware

name = "Zuum"
vendor = "Zuum"

# Redirects to Zuum's public Dropbox folder, which has a "Product Firmware/<model>/" folder per product
firmware_site = "https://www.zuummedia.com/downloads/"
# Where that page redirects to. Used when the page itself is unavailable (it sometimes answers 403)
known_folder = ("https://www.dropbox.com/scl/fo/i888ztms8musfusi2l33h/ALSnqx-hrqCHMwnYkXk9IhU"
                "?rlkey=p9i49mholh44cstdss7ku8rom&dl=0")
firmware_folder = "Product Firmware"


def is_folder_link(url):
    """A Dropbox /scl/fo/ folder link, and not a redirect into a zip of the whole folder (zip_download_get, dl=1)."""
    parsed = urlparse(url or "")
    if not (parsed.hostname or "").endswith("dropbox.com") or "zip_download" in url:
        return False
    return bool(re.match(r"^/scl/fo/[^/]+/[^/]+$", parsed.path)) and "dl=1" not in parsed.query.split("&")


def resolve_folder_link(url, max_redirects=6):
    """Follow the downloads page's redirects one at a time, stopping at the folder link, so a redirect to a zip of
    the whole folder is never downloaded. Returns None when it doesn't lead to a folder link."""
    for _ in range(max_redirects):
        if is_folder_link(url):
            return url
        response = http.get(url, headers={"User-Agent": dropbox.USER_AGENT}, allow_redirects=False, stream=True)
        response.close()
        location = response.headers.get("Location")
        if response.status_code not in (301, 302, 303, 307, 308) or not location:
            return None
        url = urljoin(url, location)
        if "zip_download" in url:
            return None
    return url if is_folder_link(url) else None


def get_firmwares():
    try:
        folder_url = resolve_folder_link(firmware_site)
    except Exception:
        folder_url = None
    if not folder_url:
        folder_url = known_folder

    firmwares = []
    for file in dropbox.walk_folder(folder_url, top_folders=(firmware_folder,)):
        # The folder also holds HDMI matrix drivers, IR codes and non-Dahua NVR firmware
        if not is_dahua_firmware_name(file["file_name"]):
            continue

        # "Product Firmware/LSNVR8CHV2-4K" or "Product Firmware/H10X10V1/Firmware": the model is the first folder
        parts = file["path"].split("/")
        model = parts[1] if len(parts) > 1 else None

        # Zuum's names already carry a Dahua version (often with the model in front), so they're kept as-is
        firmware = make_firmware([model] if model else [], file["url"], file_name=file["file_name"])
        firmware["firmware_size"] = file["size"]
        firmwares.append(firmware)

    return firmwares


def download_file(url, part_name):
    dropbox.download(url, part_name)
