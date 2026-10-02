import re
from urllib.parse import unquote

from bs4 import BeautifulSoup

from util import http
from util.oem_helpers import DAHUA_VERSION, FIRMWARE_EXTENSIONS, make_firmware, sanitize_name

name = "Speco"
vendor = "Speco"

# Lists a release notes page per recorder series, which is where the firmware links are
firmware_site = "https://specotech.com/recorder-software-updates-2/"


def get_release_notes_pages():
    page = http.get(firmware_site)
    soup = BeautifulSoup(page.content, "html.parser")

    pages = []
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if re.search(r'specotech\.com/[^/]+-software-release-notes/?$', href) and href not in pages:
            pages.append(href)

    return pages


def get_firmwares():
    firmwares = []
    seen_urls = set()

    for page_url in get_release_notes_pages():
        # e.g. https://specotech.com/nxl-series-software-release-notes/ -> NXL
        series = page_url.rstrip("/").split("/")[-1].replace("-software-release-notes", "").replace("-series", "").upper()

        page = http.get(page_url)
        soup = BeautifulSoup(page.content, "html.parser")
        for link in soup.find_all("a", href=True):
            url = link["href"]
            file_name = unquote(url.split("/")[-1])
            if url in seen_urls or "/wp-content/uploads/" not in url or not file_name.lower().endswith(FIRMWARE_EXTENSIONS):
                continue
            # Most Speco recorders aren't Dahua, so only keep files with a Dahua style version string
            match = DAHUA_VERSION.search(file_name.upper())
            if not match:
                continue
            seen_urls.add(url)

            version = f"V{match[1]}"
            date = match[2] if len(match[2]) == 8 else f"20{match[2]}"
            release_date = f"{date[0:4]}-{date[4:6]}-{date[6:8]}"

            # Names like v3.215.00sp000.0.r.20190326.bin_.zip don't say what they're for
            firmwares.append(make_firmware(
                f"{series} Series",
                url,
                sanitize_name(f"Speco_{series}_{file_name}"),
                version=version,
                release_date=release_date,
            ))

    return firmwares
