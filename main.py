import copy
import json
import os.path
import threading
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



camera_json_lock = threading.Lock()


def download_firmware_thread(firmware, firmware_type):
    file_name = f'firmware/{firmware[firmware_type].split("/")[-1].split("?")[0]}'

    downloaded = False
    if not os.path.exists(file_name):
        # file_name is None
        new_file_name = download_firmware(firmware[firmware_type])

        if new_file_name is not None:
            downloaded = True
            file_name = new_file_name

    # TODO: Add a lock here since we're reading, modifying, then writing back a file

    with camera_json_lock:
        cameras_json = get_cameras_json()
        cameras_json_original = copy.deepcopy(cameras_json)
        firmware_json_name = file_name[9:]

        # Store found camera names and notes in array
        ignored_notes = [
            'N/A '
        ]
        ignored_names = []

        camera_names = []
        firmware_notes = []
        if firmware_json_name in cameras_json:
            if 'camera_name' in cameras_json[firmware_json_name] and cameras_json[firmware_json_name]['camera_name']:
                for json_camera_names in cameras_json[firmware_json_name]['camera_name']:
                    if json_camera_names not in camera_names:
                        camera_names.append(json_camera_names)
            if 'notes' in cameras_json[firmware_json_name] and cameras_json[firmware_json_name]['notes']:
                for json_camera_notes in cameras_json[firmware_json_name]['notes']:
                    if json_camera_notes not in firmware_notes:
                        firmware_notes.append(json_camera_notes)

        if firmware['camera_name'] is not None and firmware['camera_name'] not in ignored_names and firmware['camera_name'] not in camera_names:
            camera_names.append(firmware['camera_name'])

        if firmware['firmware_notes'] is not None and firmware['firmware_notes'] not in ignored_notes and firmware['firmware_notes'] not in firmware_notes:
            firmware_notes.append(firmware['firmware_notes'])

        if firmware_json_name in cameras_json:
            for cam_name in cameras_json[firmware_json_name]['camera_name']:
                if cam_name not in ignored_names and cam_name not in camera_names:
                    camera_names.append(cam_name)
            for firmware_note in cameras_json[firmware_json_name]['notes']:
                if firmware_note not in ignored_notes and firmware_note not in firmware_notes:
                    firmware_notes.append(firmware_note)

        firmware_data = {
            'camera_name': camera_names,
            'url': firmware[firmware_type],
            'notes': firmware_notes,
        }

        if downloaded:
            firmware_data['firmware_size']: os.stat(file_name).st_size
            firmware_data['url']: firmware[firmware_type]

        if firmware_json_name in cameras_json:
            cameras_json[firmware_json_name].update(firmware_data)
        else:
            cameras_json[firmware_json_name] = firmware_data

        if cameras_json_original != cameras_json:
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

    firmware_json = get_firmware_json()

    # Only get compatibility for firmwares we don't have yet
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = []

        if (firmware_file in firmware_json and firmware_json[firmware_file] == []) or (firmware_file not in firmware_json):
            futures.append(pool.submit(process_firmware_threaded, firmware_file, file_path, tmp_file_path))

        for future in as_completed(futures):
            future.result()


def process_all_firmwares():
    firmware_files = os.listdir('firmware')

    for firmware_file in firmware_files:
        process_firmware(firmware_file)


def start_full_processing():
    get_all_firmwares()
    process_all_firmwares()


start_full_processing()
