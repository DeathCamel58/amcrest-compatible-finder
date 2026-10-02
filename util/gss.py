from urllib.parse import urljoin

from bs4 import BeautifulSoup

from util import http

# Shared parser for the gogss.com /firmware-category/ pages, which all use the same table layout:
# Product Model Type | Compatible Models | Firmware Version (one or more download links)


def get_firmware_rows(firmware_site):
    # gogss.com is behind Cloudflare
    page = http.get_protected_html(firmware_site)
    if page is None:
        return []

    soup = BeautifulSoup(page, "html.parser")

    # Skip the header row
    return [row for row in soup.find_all("tr") if row.find("td")]


def parse_firmwares(rows):
    firmwares = []

    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 3:
            continue

        camera_name = cols[0].get_text(strip=True)
        compatible_models = cols[1].get_text(strip=True)

        for link in cols[2].find_all("a"):
            firmware = {
                "camera_name": camera_name,
                "firmware_version": link.get_text(strip=True).replace("new!", ""),
                "firmware_size": None,
                "firmware_notes": f"Compatible models: {compatible_models}" if compatible_models else None,
                "firmware_changelog": None,
                "firmware_previous": None,
                "firmware_latest": urljoin("https://gogss.com/", link["href"]),
            }

            firmwares.append(firmware)

    return firmwares


def get_firmwares(firmware_site):
    return parse_firmwares(get_firmware_rows(firmware_site))
