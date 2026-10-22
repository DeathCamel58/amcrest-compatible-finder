import re
from urllib.parse import unquote_plus, urljoin, urlparse

from bs4 import BeautifulSoup

from util import http
from util.general import get_href_if_exists
from util.oem_helpers import parse_dahua_version

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

    if page is None:
        return []

    return find_firmware_tables(page)


def find_firmware_tables(page):
    """The firmware tables on the page. Since the move to Shopify (October 2026) the tables are page blocks whose
    class names end in a random block ID, so they're found by their "Latest Firmware Version" header instead"""
    soup = BeautifulSoup(page, "html.parser")
    return [table for table in soup.find_all("table")
            if any("latest firmware" in th.get_text(" ", strip=True).lower() for th in table.find_all("th"))]


def clean_changelog_link(href):
    # Rows without a change log link to the text "No Change Log Available" instead of leaving the cell empty
    if not href or not urlparse(href.strip()).scheme:
        return None
    return href.strip()


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


NOTES_MODEL = re.compile(r"^(?:only )?for ([A-Z0-9][A-Z0-9-]{3,})(?: only)?$", re.IGNORECASE)
# The S3 folder a firmware is in is named after its model, except for version folders ("V1")
FOLDER_VERSION = re.compile(r"^V\d+$", re.IGNORECASE)


def fallback_camera_name(notes, *links):
    """The model for a row whose product cell is empty (on the Shopify page some rows have a template error where the
    product should be): the one the notes name ("Only for NV5232"), or the S3 folder the firmware is in"""
    match = NOTES_MODEL.match(notes or "")
    if match and match.group(1).lower() != "current":
        return match.group(1)
    for link in links:
        # Skip the file name and the top folder ("Curent firmware", "Amcrest Organized Firmwares")
        folders = [unquote_plus(part) for part in urlparse(link or "").path.strip("/").split("/")[1:-1]]
        for folder in reversed(folders):
            if not FOLDER_VERSION.match(folder):
                return folder
    return None


def parse_firmwares(firmware_box):
    firmwares = []

    for firmware_row in firmware_box.find_all("tr"):
        cols = firmware_row.find_all("td")
        # The header row has <th> cells
        if len(cols) < 7:
            continue

        changelog = clean_changelog_link(get_href_if_exists(cols[4]))
        firmware_previous = clean_download_link(get_href_if_exists(cols[5]))
        firmware_latest = clean_download_link(get_href_if_exists(cols[6]))

        firmware_version, release_date = parse_version_cell(get_cell_text(cols[1]))

        camera_name = get_cell_text(cols[0]) or fallback_camera_name(get_cell_text(cols[3]), firmware_latest,
                                                                      firmware_previous)

        firmware = {
            "camera_name": camera_name or [],
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


# Builds in Amcrest's S3 buckets that the firmware page doesn't link (older builds it replaced, or ones only a
# changelog names), found by trying names in the folders it does link: (URL, models, notes)
UNLINKED_FILES = [
    ("https://s3.amazonaws.com/amcrest-files/Firmware/General_IPC-HX1X2X-Themis_Eng_N_V2.400.0000.15.R.20160906.bin",
     ["IP3M-954E", "IP3M-956E"], "Named in https://s3.amazonaws.com/amcrest-files/Firmware/Changelog/"
                                 "IP3M-954E_IP3M-956E_Changelog_4-26-17.txt"),
    ("https://s3.amazonaws.com/amcrest-files/Firmware/General_IPC-HX4X2X-Themis_Eng_N_V2.400.0000.15.R.20160906.bin",
     ["IP4M-1025E"], "Named in https://s3.amazonaws.com/amcrest-files/Firmware/IP4M-1025E_Changelog_8-10-17.txt"),
    ("https://amcrest-firmwares.s3.us-east-1.amazonaws.com/Amcrest+Organized+Firmwares/NV5XXX-EI+Series/"
     "Amcrest_NVR5XXX-EI_MultiLang_NP_V4.000.00AC001.0.R.240910.bin", ["NV5XXX-EI Series"], None),
    ("https://amcrest-firmwares.s3.amazonaws.com/Curent+firmware/IP5M-1190W/"
     "Amcrest_SD-Hertz_Eng_NP_V2.810.00AC000.0.R.211218.bin", ["IP5M-1190W"], None),
]


def unlinked_firmwares():
    firmwares = []
    for url, models, notes in UNLINKED_FILES:
        version, release_date = parse_dahua_version(unquote_plus(url.rsplit("/", 1)[-1]))
        firmwares.append({
            "camera_name": models,
            "firmware_version": version,
            "release_date": release_date,
            "firmware_size": None,
            "firmware_notes": " ".join(filter(None, ["Not linked from the firmware page.", notes])),
            "firmware_changelog": None,
            "firmware_previous": None,
            "firmware_latest": url,
        })
    return firmwares


def get_firmwares():
    firmware_boxes = get_firmware_boxes()

    parsed_firmwares = []
    for firmware_box in firmware_boxes:
        parsed_firmware_box = parse_firmwares(firmware_box)

        for parsed_firmware in parsed_firmware_box:
            parsed_firmwares.append(parsed_firmware)

    return parsed_firmwares + unlinked_firmwares()
