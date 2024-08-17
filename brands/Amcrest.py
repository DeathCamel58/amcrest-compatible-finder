import requests
from bs4 import BeautifulSoup

from util.general import get_href_if_exists

name = "Amcrest"


def get_firmware_boxes():
    # Download and parse the Amcrest firmware site
    firmware_site = "https://amcrest.com/firmware"
    page = requests.get(firmware_site)
    soup = BeautifulSoup(page.content, "html.parser")

    results = soup.find_all(class_="frmwr-box")

    return results


def parse_firmwares(firmware_box):
    firmwares = []

    firmware_rows = firmware_box.find_all("tr")

    if len(firmware_rows) == 0:
        return firmwares

    firmware_rows.pop(0)

    for firmware_row in firmware_rows:
        cols = firmware_row.find_all("td")

        changelog = get_href_if_exists(cols[4])
        firmware_previous = get_href_if_exists(cols[5])
        firmware_latest = get_href_if_exists(cols[6])

        firmware = {
            "camera_name": cols[0].text.replace("\n", ""),
            "firmware_version": cols[1].text.replace("\n", ""),
            "firmware_size": cols[2].text.replace("\n", ""),
            "firmware_notes": cols[3].text.replace("\n", ""),
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
