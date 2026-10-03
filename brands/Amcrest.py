import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from util import http
from util.general import get_href_if_exists

name = "Amcrest"
vendor = "Amcrest"

firmware_site = "https://amcrest.com/firmware"


def clean_download_link(href):
    """The link as an absolute URL, or None if it can't be a file download:
    - links with a literal "*" (an unfilled placeholder in the table, e.g. "..._10002_*.bin"), which S3 refuses
    - AWS console pages (s3.console.aws.amazon.com), which need an AWS login and aren't files
    - amcrest.com/downloads/... links, which redirect to a help-centre category page rather than a file
    s3:// links are kept; main.py turns them into https."""
    if not href:
        return None
    href = href.strip()
    if href.startswith("s3://"):
        return href
    url = urljoin(firmware_site, href)
    host = (urlparse(url).hostname or "").lower()
    if "*" in url:
        return None
    if host.endswith("console.aws.amazon.com"):
        return None
    if host in ("amcrest.com", "www.amcrest.com") and urlparse(url).path.startswith("/downloads/"):
        return None
    return url


def get_firmware_boxes():
    # Download and parse the Amcrest firmware site (it's behind Cloudflare)
    page = http.get_protected_html(firmware_site)

    # with open("/home/deathcamel57/Downloads/Firmware Upgrade _ Amcrest Technologies.html", "rb") as file:
    #    page = file.read()

    if page is None:
        return []

    soup = BeautifulSoup(page, "html.parser")

    results = soup.find_all(class_="frmwr-box")

    return results


def get_cell_text(cell):
    # Cells use <br> between lines, which .text would run together ("IP2M-841German")
    return cell.get_text(" ", strip=True)


def parse_version_cell(text):
    # e.g. "V2.623.00AC005.0.R 8/13/21" -> ("V2.623.00AC005.0.R", "2021-08-13"); "-" means none listed
    version, release_date = text, None
    match = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2}|\d{4})$", text)
    if match:
        month, day, year = match.groups()
        # Most rows are month/day/year, but a few are day/month/year (e.g. 16/05/17)
        if int(month) > 12 >= int(day):
            month, day = day, month
        year = f"20{year}" if len(year) == 2 else year
        release_date = f"{year}-{int(month):02d}-{int(day):02d}"
        version = text[:match.start()].strip()

    if version in ("", "-"):
        version = None

    return version, release_date


def parse_firmwares(firmware_box):
    firmwares = []

    firmware_rows = firmware_box.find_all("tr")

    if len(firmware_rows) == 0:
        return firmwares

    firmware_rows.pop(0)

    for firmware_row in firmware_rows:
        cols = firmware_row.find_all("td")

        changelog = get_href_if_exists(cols[4])
        firmware_previous = clean_download_link(get_href_if_exists(cols[5]))
        firmware_latest = clean_download_link(get_href_if_exists(cols[6]))

        firmware_version, release_date = parse_version_cell(get_cell_text(cols[1]))

        firmware = {
            "camera_name": get_cell_text(cols[0]),
            "firmware_version": firmware_version,
            "release_date": release_date,
            "firmware_size": get_cell_text(cols[2]),
            "firmware_notes": get_cell_text(cols[3]),
            "firmware_changelog": changelog,
            "firmware_previous": firmware_previous,
            "firmware_latest": firmware_latest,
        }

        firmwares.append(firmware)

    return firmwares


def get_firmwares():
    firmware_boxes = get_firmware_boxes()

    parsed_firmwares = []
    for firmware_box in firmware_boxes:
        parsed_firmware_box = parse_firmwares(firmware_box)

        for parsed_firmware in parsed_firmware_box:
            parsed_firmwares.append(parsed_firmware)

    return parsed_firmwares
