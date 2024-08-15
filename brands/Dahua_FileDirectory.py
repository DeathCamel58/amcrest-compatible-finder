# Dahua processing
import requests
from bs4 import BeautifulSoup

name = "DahuaFileDirectory"

firmware_site = "https://dahuawiki.com/images/Files/Firmware/"


def get_links():
    # Download and parse the Dahua firmware directory listing
    # NOTE: This does not search other directories
    page = requests.get(firmware_site)
    soup = BeautifulSoup(page.content, "html.parser")

    results = soup.find_all("table")

    return results


def parse_firmwares(link):
    firmwares = []

    links = link.find_all("a")
    links.pop(0)
    links.pop(0)
    links.pop(0)
    links.pop(0)
    links.pop(0)

    for link in links:
        link_text = link.text

        if link_text.endswith(".bin"):
            link = f"{firmware_site}{link_text}"
            firmware = {
                "camera_name": None,
                "firmware_version": None,
                "firmware_size": None,
                "firmware_notes": None,
                "firmware_changelog": None,
                "firmware_previous": None,
                "firmware_latest": link,
            }

            firmwares.append(firmware)

    return firmwares


def get_firmwares():
    table = get_links()

    parsed_firmware = []

    parsed_firmwares = parse_firmwares(table[0])

    for parsed in parsed_firmwares:
        parsed_firmware.append(parsed)

    return parsed_firmwares
