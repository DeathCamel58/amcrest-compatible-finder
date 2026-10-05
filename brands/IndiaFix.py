import html as html_lib
import json
import os
import re
import time

from bs4 import BeautifulSoup

from util import gdrive, http
from util.mirror import is_firmware_file
from util.oem_helpers import make_firmware

name = "indiafix.in (Dahua India builds)"
vendor = "Dahua India"
# An Indian repair blog whose post links Google Drive copies of Dahua India's recorder firmware (IndiaDahua_ builds)
kind = "mirror"

post_url = "https://www.indiafix.in/2024/11/dahua-xvr-nvr-firmware-download-free.html"
DRIVE_FILE = re.compile(r"drive\.google\.com/file/d/([\w-]+)")
TITLE = re.compile(r"<title>(.*?)(?: - Google Drive)?</title>", re.DOTALL)
# Drive file names, by file id: a Drive page has to be loaded to learn each name, so they're kept between runs
TITLE_CACHE = "tmp/indiafix_titles.json"


def parse_post(page):
    """[(Drive file id, model)] for the post's download links, from its table of model / download rows."""
    soup = BeautifulSoup(page, "html.parser")
    rows = []
    seen = set()
    for link in soup.find_all("a", href=True):
        match = DRIVE_FILE.search(link["href"])
        if not match or match.group(1) in seen:
            continue
        seen.add(match.group(1))
        row = link.find_parent("tr")
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td")] if row else []
        model = next((cell for cell in cells if cell and cell.lower() != "download"), None)
        rows.append((match.group(1), model))
    return rows


def drive_title(file_id):
    page = http.get(f"https://drive.google.com/file/d/{file_id}/view", timeout=(10, 30))
    page.raise_for_status()
    match = TITLE.search(page.text)
    return html_lib.unescape(match.group(1)).strip() if match else None


def load_titles():
    try:
        with open(TITLE_CACHE) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def save_titles(titles):
    os.makedirs(os.path.dirname(TITLE_CACHE), exist_ok=True)
    with open(f"{TITLE_CACHE}.tmp", "w") as f:
        json.dump(titles, f, indent=1, sort_keys=True)
    os.replace(f"{TITLE_CACHE}.tmp", TITLE_CACHE)


def get_firmwares():
    page = http.get(post_url, timeout=(10, 60))
    page.raise_for_status()
    titles = load_titles()
    firmwares = []
    for file_id, model in parse_post(page.text):
        if file_id not in titles:
            try:
                titles[file_id] = drive_title(file_id)
            except Exception as err:
                print(f"\tindiafix: couldn't read Drive file {file_id}: {err}")
                continue
            time.sleep(1)  # one Drive page a second
        file_name = titles[file_id]
        url = gdrive.download_url(file_id)
        if not file_name or not is_firmware_file(file_name, url):
            continue
        firmwares.append(make_firmware([model] if model else [], url, file_name=file_name,
                                       notes="Dahua India build, linked from indiafix.in"))
    save_titles(titles)
    return firmwares


def download_file(url, part_name):
    gdrive.download(url, part_name)
