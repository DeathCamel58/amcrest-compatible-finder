import re
import socket
from urllib.parse import quote, unquote, urlparse, urlunparse

from bs4 import BeautifulSoup
from requests import HTTPError

from util import http
from util.mirror import is_firmware_file
from util.oem_helpers import make_firmware

name = "TVC Mexico (soporte.tvc.mx)"
vendor = "TVC"
# Mexico's main Dahua distributor. Its forum's download centre links firmware on soporte.tvc.mx, which stopped
# answering in 2026 (checked 2026-10-05); the download step's Wayback Machine fallback fetches the files it captured
kind = "mirror"

forum_page = "https://foro.tvc.mx/docs/centro-de-descargas-dahua-technology"
FIRMWARE_PATH = re.compile(r"soporte\.tvc\.mx/Ingenieria/DAHUA/FIRMWARE/", re.IGNORECASE)
server = "soporte.tvc.mx"

# Set by get_firmwares: whether the file server answers, so a dead server costs one check instead of a timeout per file
server_up = [True]


def quote_url(url):
    """The forum links unescaped paths with spaces and brackets."""
    parts = urlparse(url)
    return urlunparse(parts._replace(path=quote(unquote(parts.path), safe="/()")))


def parse_page(page):
    """[(url, model)] for firmware links: each is in a table row whose first cell names the model."""
    soup = BeautifulSoup(page, "html.parser")
    found = {}
    for link in soup.find_all("a", href=True):
        if not FIRMWARE_PATH.search(link["href"]):
            continue
        url = quote_url(link["href"].strip())
        row = link.find_parent("tr")
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td")] if row else []
        model = cells[0] if cells and cells[0] else None
        found.setdefault(url, model)
    return list(found.items())


def is_up(host, timeout=8):
    for port in (443, 80):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def get_firmwares():
    page = http.get(forum_page, timeout=(10, 60))
    page.raise_for_status()
    server_up[0] = is_up(server)
    if not server_up[0]:
        print(f"\tTVC: {server} isn't answering; its files can only come from the Wayback Machine")
    firmwares = []
    for url, model in parse_page(page.text):
        file_name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
        if is_firmware_file(file_name, url):
            firmwares.append(make_firmware([model] if model else [], url))
    return firmwares


def download_file(url, part_name):
    if not server_up[0]:
        # An HTTPError isn't retried: the download step goes straight to the Wayback Machine's copy
        raise HTTPError(f"{server} isn't answering")
    with http.get(url, stream=True, timeout=(10, 120)) as response:
        response.raise_for_status()
        with open(part_name, "wb") as f:
            for chunk in http.iter_content_with_min_rate(response):
                f.write(chunk)
