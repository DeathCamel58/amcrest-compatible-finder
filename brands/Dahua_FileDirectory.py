from urllib.parse import unquote

from util.oem_helpers import FIRMWARE_EXTENSIONS, crawl_index

name = "DahuaFileDirectory"
vendor = "Dahua"

firmware_site = "https://dahuawiki.com/images/Files/Firmware/"


# Subfolders that hold no firmware
SKIP_DIRECTORIES = ("Release Notes",)


def get_links():
    """URLs of every file in the directory listing and its subfolders (General_Temp, Custom Firmware, ...)"""
    return [file_url for file_url, _ in crawl_index(firmware_site, skip_directories=SKIP_DIRECTORIES, delay=1,
                                                 max_failures=5, cache_file="tmp/dahuawiki_index.json")]


def parse_firmwares(links):
    firmwares = []

    for url in links:
        file_name = unquote(url.split("/")[-1])
        # Dahua uses .bin, .BIN, .zip and .img; this is Dahua's own file server, so every firmware here counts
        if not file_name.lower().endswith(FIRMWARE_EXTENSIONS):
            continue

        firmware = {
            "camera_name": None,
            "firmware_version": None,
            "firmware_size": None,
            "firmware_notes": None,
            "firmware_changelog": None,
            "firmware_previous": None,
            "firmware_latest": url,
            # The URL is percent-encoded (spaces, brackets), so give the real file name explicitly
            "firmware_latest_file_name": file_name,
        }

        firmwares.append(firmware)

    return firmwares


def get_firmwares():
    return parse_firmwares(get_links())
