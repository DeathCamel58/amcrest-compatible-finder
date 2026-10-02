from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup

from util import http
from util.oem_helpers import FIRMWARE_EXTENSIONS

name = "DahuaFileDirectory"
vendor = "Dahua"

firmware_site = "https://dahuawiki.com/images/Files/Firmware/"


def get_links():
    # Download and parse the Dahua firmware directory listing
    # NOTE: This does not search other directories
    page = http.get(firmware_site)
    soup = BeautifulSoup(page.content, "html.parser")

    return soup.find_all("a", href=True)


def parse_firmwares(links):
    firmwares = []

    for link in links:
        href = link["href"]
        # Skip sort links, parent/sub directories and anything outside this directory
        if href.startswith(("?", "/")) or href.endswith("/"):
            continue

        file_name = unquote(href.split("/")[-1])
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
            "firmware_latest": urljoin(firmware_site, href),
            # The URL is percent-encoded (spaces, brackets), so give the real file name explicitly
            "firmware_latest_file_name": file_name,
        }

        firmwares.append(firmware)

    return firmwares


def get_firmwares():
    return parse_firmwares(get_links())
