import os
import re
import time
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup

from util import http
from util.oem_helpers import is_dahua_firmware_name, make_firmware

name = "Sapsan (sapsan.org)"
vendor = "Sapsan"
# A Russian/Ukrainian installer's firmware pages: each link's text is the build inside the archive, followed by the
# models it's for ("DH_NVR41-W_Eng_P_V3.202.0000.1.R.20170401 - NVR4104-W"). The files are under
# /img/source/FirmwareDH/, which can't be listed
kind = "mirror"

pages = ["https://sapsan.org/dahuafirmware", "https://sapsan.org/proshivki_IP_videokamer_Dahua",
         "https://sapsan.org/proshivki_IP_NVR_Dahua", "https://sapsan.org/proshivki_HCVR_Dahua",
         "https://sapsan.org/proshivki_dlya_domofonov_i_vizivnih_panelei"]
FIRMWARE_LINK = re.compile(r"/img/source/FirmwareDH/.+\.(rar|zip|bin|7z)$", re.IGNORECASE)


def local_name(archive_name, build):
    """Archives with generic names ("NVR4xxx.rar") are saved as "Sapsan_<archive>_<build inside>.<ext>": the same
    build is in more than one of them (NVR2x0x.rar and NVR2x16.rar), and other sites use the same generic names"""
    if is_dahua_firmware_name(archive_name) or not build:
        return archive_name
    stem, extension = os.path.splitext(archive_name)
    return f"Sapsan_{stem}{extension}" if build == stem else f"Sapsan_{stem}_{build}{extension}"


def parse_page(page, base_url):
    """[(url, file name, build, models)] for every firmware link on a page."""
    soup = BeautifulSoup(page, "html.parser")
    found = []
    for link in soup.find_all("a", href=True):
        if not FIRMWARE_LINK.search(link["href"]):
            continue
        url = urljoin(base_url, link["href"])
        build = link.get_text(" ", strip=True) or None
        # The models follow the link, up to the next <br>: " - NVR1104HS, NVR1108HS"
        text = ""
        for sibling in link.next_siblings:
            if getattr(sibling, "name", None) in ("br", "a"):
                break
            text += sibling.get_text(" ") if hasattr(sibling, "get_text") else str(sibling)
        models = [model.strip() for model in text.replace("\xa0", " ").strip(" -–").split(",") if model.strip()]
        found.append((url, local_name(unquote(url.rsplit("/", 1)[-1]), build), build, models))
    return found


def get_firmwares():
    firmwares = {}
    for page_url in pages:
        page = http.get(page_url, timeout=(10, 60))
        if page.status_code != 200:
            print(f"\tSapsan: {page_url} answered {page.status_code}")
            continue
        for url, file_name, build, models in parse_page(page.text, page_url):
            if url not in firmwares:
                firmwares[url] = make_firmware(models, url, file_name=file_name,
                                               notes=f"Contains {build}" if build else None)
        time.sleep(1)
    return list(firmwares.values())
