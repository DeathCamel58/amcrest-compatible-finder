import re
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup

from util import http
from util.mirror import is_firmware_file
from util.oem_helpers import make_firmware

name = "BITS (bits.com.ec)"
vendor = "BITS"
# A Dahua distributor in Guayaquil, Ecuador; its downloads page links firmware in its WordPress uploads
kind = "mirror"

downloads_page = "http://bits.com.ec/bits/descargas/"
FILE_LINK = re.compile(r"\.(bin|zip|rar|img)$", re.IGNORECASE)
# XiongMai builds the page lists next to Dahua's (AHB/NBD/MBD recorders), and language/web configuration packs
SKIP = re.compile(r"^(SimpGeneral_|General_General_|CONFIG_)", re.IGNORECASE)


def parse_page(page, base_url):
    """[(url, model)]: the model is the link text or the list item it's in (e.g. "DVR21xx")."""
    soup = BeautifulSoup(page, "html.parser")
    found = []
    for link in soup.find_all("a", href=True):
        if not FILE_LINK.search(link["href"].split("?")[0]):
            continue
        url = urljoin(base_url, link["href"])
        model = link.get_text(" ", strip=True) or None
        if model and (FILE_LINK.search(model) or len(model) > 40):
            model = None
        found.append((url, model))
    return found


def get_firmwares():
    page = http.get(downloads_page, timeout=(10, 60))
    page.raise_for_status()
    firmwares = {}
    for url, model in parse_page(page.text, downloads_page):
        file_name = unquote(url.split("?")[0].rsplit("/", 1)[-1])
        if url in firmwares or SKIP.search(file_name) or not is_firmware_file(file_name, url):
            continue
        firmwares[url] = make_firmware([model] if model else [], url)
    return list(firmwares.values())
