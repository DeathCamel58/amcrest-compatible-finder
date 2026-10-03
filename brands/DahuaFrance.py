import hashlib
import html
import re
from urllib.parse import unquote, urljoin

from util import http
from util.download_firmware import check_published_md5
from util.file_type import classify_file_type
from util.oem_helpers import make_firmware

name = "Dahua France"
vendor = "Dahua"

# Dahua France's document centre: one page ("Microprogrammes") linking every firmware, each with its release date and
# MD5, mostly 2015-2021 builds that Dahua's main download centre no longer lists
firmware_page = "https://france.dahuatech.com/DocThird?cId=648"

BLOCK = re.compile(r'<a class="title" href="(?P<href>[^"]+)"[^>]*>.*?</a>\s*<div class="wenben">(?P<text>.*?)</div>',
                   re.DOTALL)
RELEASE_DATE = re.compile(r"Release date:\s*(\d{4}-\d{2}-\d{2})")
MD5 = re.compile(r"Checksum \(md5\):\s*([0-9a-fA-F]{32})")
RELEASE_NOTES = re.compile(r'<a href="([^"]+\.pdf)"', re.IGNORECASE)
# The model or series is the file name before the language and version: DH_HCVR5x04-S2_Eng_P_V3.200... -> HCVR5x04-S2
MODEL = re.compile(r"^(?:Customer_|General_|DH_)?(.+?)(?:_(?:Eng|MultiLang|Multi|FrnEng|EngFrn)(?=_|\.|$).*|_V\d+\.\d.*)$")

# Firmware URL -> MD5 from the page, so downloads can be verified
md5_by_url = {}


def parse_page(page):
    """[(url, release date, md5, release notes URL)] for every linked file, in page order."""
    files = []
    for match in BLOCK.finditer(page):
        text = match.group("text")
        date = RELEASE_DATE.search(text)
        md5 = MD5.search(text)
        notes = RELEASE_NOTES.search(text)
        files.append((urljoin(firmware_page, html.unescape(match.group("href")).strip()),
                      date.group(1) if date else None, md5.group(1).lower() if md5 else None,
                      urljoin(firmware_page, html.unescape(notes.group(1))) if notes else None))
    return files


def get_model(file_name):
    match = MODEL.match(file_name)
    return match.group(1) if match else None


def get_firmwares():
    response = http.get(firmware_page)
    response.raise_for_status()
    files = parse_page(response.text)

    firmwares = []
    for url, release_date, md5, release_notes in files:
        file_name = unquote(url.rsplit("/", 1)[-1])
        # Apps and tools are listed too, but aren't firmware
        if classify_file_type(file_name, url) != "firmware":
            continue
        model = get_model(file_name)
        firmware = make_firmware([model] if model else [], url, release_date=release_date)
        if release_notes:
            firmware["firmware_changelog"] = f"Release notes: {release_notes}"
        if md5:
            firmware["md5"] = md5
            md5_by_url[url] = md5
        firmwares.append(firmware)
    return firmwares


def download_file(url, part_name):
    md5 = hashlib.md5()
    with http.get(url, stream=True) as r:
        r.raise_for_status()
        with open(part_name, "wb") as f:
            for chunk in http.iter_content_with_min_rate(r):
                f.write(chunk)
                md5.update(chunk)
    check_published_md5(url, md5.hexdigest(), md5_by_url.get(url))
