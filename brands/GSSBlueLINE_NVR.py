# Dahua processing
import requests
from bs4 import BeautifulSoup

name = "GSS - Blue|LINE NVR"

firmware_site = "https://gogss.com/firmware-download-network-recorders-blueline/"


def get_firmware_rows():
    # Download and parse the Dahua firmware directory listing
    # NOTE: This does not search other directories
    page = requests.get(firmware_site)
    soup = BeautifulSoup(page.content, "html.parser")

    results = soup.find_all("tr")
    results.pop(0)

    return results


def parse_firmwares(rows):
    firmwares = []

    for i in range(len(rows)):
        th = rows[i].find("th")
        try:
            nvr_name = th.text
        except Exception:
            previous = rows[i - 1]
            th = previous.find("th")
            nvr_name = th.text

        links = rows[i].find_all("a")
        for link in links:
            firmware_link = link['href']
            firmware_notes = rows[i].find("td").text
            firmware_latest = f"https://gogss.com{firmware_link}"
            if firmware_latest.startswith("https://gogss.comhttps://"):
                firmware_latest = firmware_latest[17:]
            firmware = {
                "camera_name": nvr_name,
                "firmware_version": None,
                "firmware_size": None,
                "firmware_notes": firmware_notes,
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
