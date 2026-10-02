import re

from bs4 import BeautifulSoup

from util import http
from util.oem_helpers import make_firmware

name = "KBVision"
vendor = "KBVision"

# The firmware table is public, but every download is a SharePoint link that refuses anonymous access, so these are
# recorded (model, version, date, notes) without the file
listing_only = True
listing_only_reason = "Downloads are private SharePoint links that need a login"

PAGE_API = "https://kabevision.vn/wp-json/wp/v2/pages"
FIRMWARE_EXTENSION = re.compile(r"\.(bin|zip|img|dav|rar)$", re.IGNORECASE)


def get_firmwares():
    response = http.get(PAGE_API, params={"slug": "firmware", "_fields": "content"})
    response.raise_for_status()
    pages = response.json()
    if not pages:
        return []

    soup = BeautifulSoup(pages[0]["content"]["rendered"], "html.parser")
    firmwares = []
    # One table per product: STT | Ngày phát hành (release date) | Tên Firmware (name) | Ghi chú (notes) | Tải về
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            link = row.find("a", href=True)
            if len(cells) < 4 or not link:
                continue

            firmware_name = cells[2].get_text(" ", strip=True)
            if not firmware_name:
                continue
            file_name = firmware_name if FIRMWARE_EXTENSION.search(firmware_name) else f"{firmware_name}.bin"
            # e.g. KX-HDV5001AB_MultiLang_PN_KBVISION_V2.880... -> KX-HDV5001AB
            model = re.split(r"_(MultiLang|Eng)", firmware_name)[0]

            release_date = None
            match = re.match(r"(\d{1,2})-(\d{1,2})-(\d{4})", cells[1].get_text(strip=True))
            if match:
                day, month, year = match.groups()
                release_date = f"{year}-{int(month):02d}-{int(day):02d}"

            notes = cells[3].get_text(" ", strip=True) or None
            firmware = make_firmware([model], link["href"], file_name=file_name, notes=notes,
                                     release_date=release_date)
            firmware["listing_only"] = True
            firmware["listing_only_reason"] = listing_only_reason
            firmwares.append(firmware)

    return firmwares
