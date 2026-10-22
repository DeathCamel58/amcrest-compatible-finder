"""Dahua's 2016-2018 download center files, still on material.dahuasecurity.com.

The old www.dahuasecurity.com download links (down.php?file=download/firmware/encryption/<date>/<name> and
/asset/upload/Firmware/<date>/<name>) are dead, but the same paths still download from material.dahuasecurity.com.
There's no listing (a missing file returns 403) and each file is in a folder named after its upload date, so the
files were found by hand: the folders and names in Wayback Machine captures of the old download pages and release
notes (each firmware sits next to its release-note PDF under the same name), and wanted builds tried in those folders.
They're in brands/data/dahua_old_download_files.json, with the MD5 the server gives as its ETag.
"""
import json
import os
from urllib.parse import unquote

from util.oem_helpers import make_firmware

name = "Dahua download center (2016-2018 files)"
vendor = "Dahua"
kind = "vendor"

FILES = os.path.join(os.path.dirname(__file__), "data", "dahua_old_download_files.json")


def get_firmwares():
    with open(FILES) as f:
        files = json.load(f)
    firmwares = []
    for file in files:
        folder = file["url"].split("material.dahuasecurity.com/", 1)[-1].rsplit("/", 1)[0]
        firmware = make_firmware([], file["url"], file_name=unquote(file["url"].rsplit("/", 1)[-1]),
                                 notes=f"Folder: {folder}")
        firmware["firmware_size"] = file.get("size")
        if file.get("md5"):
            firmware["md5"] = file["md5"]
        firmwares.append(firmware)
    return firmwares
