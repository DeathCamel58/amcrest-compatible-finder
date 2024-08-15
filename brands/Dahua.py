# Dahua processing
import pandas as pd
import requests
from bs4 import BeautifulSoup

name = "Dahua"


def get_firmware_boxes():
    # Download and parse the Dahua firmware site
    firmware_site = "https://dahuawiki.com/Cameras"
    page = requests.get(firmware_site)
    soup = BeautifulSoup(page.content, "html.parser")

    results = soup.find_all(class_="wikitable")

    return results


def parse_firmwares(firmware_box):
    firmwares = []

    rows = firmware_box.find_all("tr")
    row_titles = []
    for col in rows[0].find_all("th"):
        row_titles.append(col.text.replace("\n", "").replace(" ", ""))

    df = pd.DataFrame(columns=row_titles)

    rows.pop(0)

    for row in rows:
        data = {}

        cols = row.find_all("td")
        for i in range(len(cols)):
            # Sanity check that the data isn't longer than the columns
            if i < len(row_titles):
                col_title = row_titles[i]
                col_data = cols[i].text.replace("\n", "").replace(" ", "")

                icons = cols[i].find_all('i')
                if len(icons) > 0:
                    links = cols[i].find_all("a")
                    if len(links) > 0:
                        col_data = links[0]["href"]

                data[col_title] = col_data

        new_df = pd.DataFrame(data, index=[0])
        df = pd.concat([df, new_df], ignore_index=True)

    if len(df.index) == 0:
        return firmwares

    for index, row in df.iterrows():
        firmware = {
            "camera_name": None,
            "firmware_version": None,
            "firmware_size": None,
            "firmware_notes": None,
            "firmware_changelog": None,
            "firmware_previous": None,
            "firmware_latest": None,
        }

        if 'Models' in row:
            firmware["camera_name"] = row['Models']
        else:
            print("Models not found")

        if 'Firmware' in row:
            firmware["firmware_latest"] = row['Firmware']
        elif 'MainFirmware' in row:
            firmware["firmware_latest"] = row['MainFirmware']

        if 'PTZFirmware' in row:
            firmware["firmware_previous"] = row['PTZFirmware']

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
