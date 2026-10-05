import re
from datetime import datetime
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup

from util import http
from util.mirror import is_firmware_file
from util.oem_helpers import make_firmware

name = "Santronic (service.santronic.ch)"
vendor = "Santronic"
# A Swiss Dahua distributor; one page lists every file as dlc/<brand>/firmware/<type>/<name> (folders can't be listed)
kind = "mirror"

page_url = "http://service.santronic.ch/"
DAHUA_FIRMWARE = re.compile(r"(^|/)dlc/dahua/firmware/", re.IGNORECASE)
DATE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")


def parse_page(page, base_url):
    """[(url, model, release date)] from the page's tables: model, new version, date, download."""
    soup = BeautifulSoup(page, "html.parser")
    found = []
    for link in soup.find_all("a", href=True):
        if not DAHUA_FIRMWARE.search(link["href"]):
            continue
        url = urljoin(base_url, link["href"])
        row = link.find_parent("tr")
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td")] if row else []
        model = cells[0] if cells and cells[0] else None
        release_date = None
        for cell in cells:
            date = DATE.match(cell)
            if date:
                try:
                    release_date = datetime(int(date[3]), int(date[2]), int(date[1])).date().isoformat()
                except ValueError:
                    pass
        found.append((url, model, release_date))
    return found


def get_firmwares():
    page = http.get(page_url, timeout=(10, 60))
    page.raise_for_status()
    firmwares = {}
    for url, model, release_date in parse_page(page.text, page_url):
        file_name = unquote(url.rsplit("/", 1)[-1])
        if not is_firmware_file(file_name, url):
            continue
        if url in firmwares:
            # The same file is listed for several models
            if model and model not in firmwares[url]["camera_name"]:
                firmwares[url]["camera_name"].append(model)
            continue
        firmwares[url] = make_firmware([model] if model else [], url, release_date=release_date)
    return list(firmwares.values())
