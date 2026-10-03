import hashlib
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from util import http
from util.download_firmware import check_published_md5
from util.oem_helpers import make_firmware

name = "Bosch DIVAR"
vendor = "Bosch"

# Bosch's DIVAR recorders are Dahua-made (their cameras run Bosch's own firmware and aren't listed here)
store_url = "https://downloadstore.boschsecurity.com/index.php"
product_types = {
    "DIVARAN": "DIVAR AN",
    "DIVARHY": "DIVAR hybrid",
    "DIVARNET": "DIVAR network",
}

# e.g. DIVAR_network_v3.4.0.R.20240306.bin, DVR-V2.6.0.R.20180330.bin
VERSION = re.compile(r"[vV](\d+\.\d+\.\d+)\.R\.(\d{8})")

# The store answers 403 to non-browser user agents
# File URL -> MD5 the store publishes, so downloads can be verified
md5_by_url = {}

headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"}


def get_rows(product_type):
    response = http.get_session().post(store_url, data={"debug": "", "lang": "en", "command": "Select",
                                                        "type": product_type}, headers=headers,
                                     timeout=http.TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")
    return [row for row in soup.select("tr.M-Table__row") if "M-Table__headlineRow" not in row.get("class", [])]


def get_firmwares():
    firmwares = []
    for product_type, label in product_types.items():
        for row in get_rows(product_type):
            cells = row.find_all("td")
            if len(cells) < 5:
                continue
            link = cells[2].find("a", href=True)
            if not link:
                continue

            url = urljoin(store_url, link["href"])
            file_name = url.rsplit("/", 1)[-1]
            match = VERSION.search(file_name)
            version = f"V{match.group(1)}.R" if match else cells[1].get_text(strip=True) or None
            release_date = f"{match.group(2)[:4]}-{match.group(2)[4:6]}-{match.group(2)[6:]}" if match else None

            firmware = make_firmware([label], url, file_name=file_name, version=version, release_date=release_date)
            checksum = cells[3].find("button", attrs={"data-clipboard-text": True})
            if checksum:
                firmware["md5"] = checksum["data-clipboard-text"].strip().lower()
                md5_by_url[url] = firmware["md5"]
            notes = cells[4].find("a", href=True)
            if notes:
                firmware["firmware_changelog"] = urljoin(store_url, notes["href"])
            firmwares.append(firmware)

    return firmwares


def download_file(url, part_name):
    md5 = hashlib.md5()
    with http.get(url, stream=True, headers=headers) as r:
        r.raise_for_status()
        with open(part_name, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
                md5.update(chunk)

    check_published_md5(url, md5.hexdigest(), md5_by_url.get(url))
