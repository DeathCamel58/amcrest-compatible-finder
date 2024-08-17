import requests
from bs4 import BeautifulSoup

name = "GSS - Blue|LINE Camera"

firmware_site = "https://gogss.com/firmware-download-cameras-blueline/"


def get_firmware_rows():
    page = requests.get(firmware_site)
    soup = BeautifulSoup(page.content, "html.parser")

    results = soup.find_all("tr")
    results.pop(0)

    return results


def parse_firmwares(rows):
    firmwares = []

    for row in rows:
        camera_name = row.find("th").text
        link = row.find("a")
        firmware_version = link.text.replace(" ", "").replace("\n", "")
        firmware_link = link['href']
        firmware_latest = f"https://gogss.com{firmware_link}"
        if firmware_latest.startswith("https://gogss.comhttps://"):
            firmware_latest = firmware_latest[17:]
        firmware = {
            "camera_name": camera_name,
            "firmware_version": firmware_version,
            "firmware_size": None,
            "firmware_notes": None,
            "firmware_changelog": None,
            "firmware_previous": None,
            "firmware_latest": firmware_latest,
        }

        firmwares.append(firmware)

    return firmwares


def get_firmwares():
    rows = get_firmware_rows()

    parsed_firmware = []

    parsed_firmwares = parse_firmwares(rows)

    for parsed in parsed_firmwares:
        parsed_firmware.append(parsed)

    return parsed_firmwares
