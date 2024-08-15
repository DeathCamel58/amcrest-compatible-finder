import json
import os.path
from concurrent.futures import ThreadPoolExecutor, as_completed

import random

from brands import Amcrest
from brands import Dahua
from brands import Dahua_FileDirectory
from brands import GSSRedLINE_Camera
from brands import GSSRedLINE_NVR
from brands import GSSBlueLINE_Camera
from brands import GSSBlueLINE_NVR
from util.download_firmware import download_firmware
from util.firmware_processing import process_firmware_threaded
from util.json_tools import get_cameras_json, save_cameras_json, get_firmware_json, save_firmware_json

oem_modules = [
    Amcrest,
    Dahua,
    Dahua_FileDirectory,
    GSSRedLINE_Camera,
    GSSRedLINE_NVR,
    GSSBlueLINE_Camera,
    GSSBlueLINE_NVR,
]

# TODO: Support additional Dahua OEMs
#       https://securitycamcenter.com/dahua-oem-list/


def download_firmware_thread(firmware, firmware_type):
    file_name = f'firmware/{firmware[firmware_type].split("/")[-1].split("?")[0]}'

    downloaded = False
    if not os.path.exists(file_name):
        # file_name is None
        new_file_name = download_firmware(firmware[firmware_type])

        if new_file_name is not None:
            downloaded = True
            file_name = new_file_name

    cameras_json = get_cameras_json()

    # Store found camera names and notes in array
    ignored_notes = [
        'N/A '
    ]
    camera_names = []
    if firmware['camera_name'] is not None:
        camera_names.append(firmware['camera_name'])

    camera_notes = []
    if firmware['firmware_notes'] is not None and firmware['firmware_notes'] not in ignored_notes:
        camera_names.append(firmware['firmware_notes'])

    firmware_json_name = file_name[9:]

    if firmware_json_name in cameras_json:
        for cam_name in cameras_json[firmware_json_name]['camera_name']:
            camera_names.append(cam_name)
        for camera_note in cameras_json[firmware_json_name]['notes']:
            if camera_note not in ignored_notes:
                camera_notes.append(camera_note)

    firmware_data = {
        'camera_name': camera_names,
        'url': firmware[firmware_type],
        'notes': camera_notes,
    }

    if downloaded:
        firmware_data['firmware_size']: os.stat(file_name).st_size
        firmware_data['url']: firmware[firmware_type]

    if firmware_json_name in cameras_json:
        cameras_json[firmware_json_name].update(firmware_data)
    else:
        cameras_json[firmware_json_name] = firmware_data

    save_cameras_json(cameras_json)


# Download all firmwares
def get_all_firmwares():
    firmwares = []

    for oem in oem_modules:
        oem_firmwares = oem.get_firmwares()
        print(f'Got a list of {len(oem_firmwares)} {oem.name} firmwares!')

        for oem_firmware in oem_firmwares:
            firmwares.append(oem_firmware)

    if len(firmwares) == 0:
        return

    # Shuffle the order of firmwares to hit different vendors at once (to prevent a slow vendor from stopping downloads)
    random.shuffle(firmwares)

    # Download all firmwares
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = []
        for firmware in firmwares:
            for firmware_type in ["firmware_previous", "firmware_latest"]:
                if firmware[firmware_type] is not None and firmware[firmware_type] != '':
                    futures.append(pool.submit(download_firmware_thread, firmware, firmware_type))
        for future in as_completed(futures):
            future.result()


def process_firmware(firmware_file):
    file_path = f"firmware/{firmware_file}"
    tmp_file_path = f"tmp/{firmware_file}"

    unknown_compatible = ['Unknown']
    firmware_json = get_firmware_json()

    # Only get compatibility for firmwares we don't have yet
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = []

        if (firmware_file in firmware_json and firmware_json[firmware_file] == unknown_compatible) or (firmware_file not in firmware_json):
            futures.append(pool.submit(process_firmware_threaded, firmware_file, file_path, tmp_file_path, firmware_json))

        for future in as_completed(futures):
            future.result()


def process_all_firmwares():
    firmware_files = os.listdir('firmware')

    for firmware_file in firmware_files:
        if not firmware_file.endswith(".json"):
            process_firmware(firmware_file)


def start_full_processing():
    get_all_firmwares()
    process_all_firmwares()


start_full_processing()
