# Lorex processing
import os

import requests
import pdfquery

name = "Lorex"

firmware_urls = [
    'https://www.lorextechnology.com/images/supportimages/supportarticles/firmware/Lorex_DVR_NVR_Firmware_012121.pdf',
    'https://www.lorextechnology.com/images/supportimages/supportarticles/firmware/Lorex_DVR_NVR_Firmware_022221.pdf',
    'https://www.lorextechnology.com/images/supportimages/supportarticles/firmware/Lorex_DVR_NVR_Firmware_033121.pdf',
    'https://www.lorextechnology.com/images/supportimages/supportarticles/firmware/Lorex_DVR_NVR_Firmware_032521_v2.pdf',
    'https://www.lorextechnology.com/images/supportimages/supportarticles/firmware/Lorex_DVR_NVR_Firmware_040921.pdf',
    'https://www.lorextechnology.com/images/supportimages/supportarticles/firmware/Lorex_DVR_NVR_Firmware_092920.pdf',
    'https://www.lorextechnology.com/images/supportimages/supportarticles/firmware/Lorex_DVR_NVR_Firmware_102820.pdf',
    'https://www.lorextechnology.com/images/supportimages/supportarticles/firmware/Lorex_DVR_NVR_Firmware_112420.pdf',
    'https://www.lorextechnology.com/images/supportimages/supportarticles/firmware/Lorex_DVR_NVR_Firmware.pdf',
    'https://www.lorextechnology.com/images/supportimages/supportarticles/firmware/Lorex_DVR_NVR_Firmware_Version_1_3_S_9_11.pdf',
    'https://www.lorextechnology.com/images/supportimages/supportarticles/firmware/Lorex_DVR_NVR_Firmware_January-19-2021.pdf',
]


def get_firmware_links():
    links = []

    # Download and parse the Lorex firmware PDFs
    for url in firmware_urls:
        requests_file = requests.get(url)
        if requests_file.status_code == 200:
            with open('tmp/lorex.pdf', 'wb') as f:
                f.write(requests_file.content)

        pdf = pdfquery.PDFQuery('tmp/lorex.pdf')
        pdf.load()

        if os.path.exists('tmp/lorex.pdf'):
            os.remove('tmp/lorex.pdf')

        raw_links = pdf.pq('Annot')
        for link in raw_links:
            link_uri = link.attrib['URI']
            if link_uri not in links:
                links.append(link.attrib['URI'])

    return links


def parse_firmware(firmware_link):
    return {
        "camera_name": None,
        "firmware_version": None,
        "firmware_size": None,
        "firmware_notes": None,
        "firmware_changelog": None,
        "firmware_previous": None,
        "firmware_latest": firmware_link,
    }


def get_firmwares():
    firmware_links = get_firmware_links()

    parsed_firmwares = []
    for firmware_link in firmware_links:
        parsed_firmware = parse_firmware(firmware_link)

        parsed_firmwares.append(parsed_firmware)

    return parsed_firmwares
