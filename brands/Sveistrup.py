import re
import time
from datetime import datetime
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup

from util import http
from util.mirror import is_firmware_upload
from util.oem_helpers import make_firmware

name = "Sveistrup (sveistrup.dk)"
vendor = "Sveistrup"
# A Danish Dahua distributor; its firmware pages link files under /filer/Teknik/Dahua/fw/ (which can't be listed)
kind = "mirror"

pages = ["https://www.sveistrup.dk/firmware", "https://www.sveistrup.dk/ipc", "https://www.sveistrup.dk/ptz",
         "https://www.sveistrup.dk/dahuatpcfirmware"]
FIRMWARE_LINK = re.compile(r"/filer/Teknik/Dahua/fw/.+\.(bin|zip|rar|img)$", re.IGNORECASE)
# "V2.860.0000000.34.R - 15.04.2025"
DATE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")


def parse_page(page, base_url):
    """[(url, model, release date)] for every firmware link: each is a list item "<model> - <version - date>"."""
    soup = BeautifulSoup(page, "html.parser")
    found = []
    for link in soup.find_all("a", href=True):
        if not FIRMWARE_LINK.search(link["href"]):
            continue
        url = urljoin(base_url, link["href"])
        item = link.find_parent("li")
        text = item.get_text(" ", strip=True) if item else ""
        model = text.split(" - ")[0].strip() if " - " in text else None
        if model == link.get_text(" ", strip=True):
            model = None
        date = DATE.search(link.get_text(" ", strip=True))
        release_date = None
        if date:
            try:
                release_date = datetime(int(date[3]), int(date[2]), int(date[1])).date().isoformat()
            except ValueError:
                pass
        found.append((url, model, release_date))
    return found


def get_firmwares():
    firmwares = {}
    for page_url in pages:
        page = http.get(page_url, timeout=(10, 60))
        if page.status_code != 200:
            print(f"\tSveistrup: {page_url} answered {page.status_code}")
            continue
        for url, model, release_date in parse_page(page.text, page_url):
            file_name = unquote(url.rsplit("/", 1)[-1])
            if url in firmwares or not is_firmware_upload(file_name, url):
                continue
            firmwares[url] = make_firmware([model] if model else [], url, release_date=release_date)
        time.sleep(1)
    # About a quarter of the links are stale (404): skip those
    live = []
    for url, firmware in firmwares.items():
        if link_works(url):
            live.append(firmware)
        time.sleep(0.5)
    return live


def link_works(url):
    try:
        response = http.get(url, stream=True, headers={"Range": "bytes=0-0"}, timeout=(10, 30))
        response.close()
        return response.status_code in (200, 206)
    except Exception as err:
        print(f"\tSveistrup: couldn't check {url}: {err}")
        return False
