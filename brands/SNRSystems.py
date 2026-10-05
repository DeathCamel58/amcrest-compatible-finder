import re

from util.mirror import NO_MODEL_FOLDER, get_mirror_firmwares, is_firmware_file

name = "SNR (snr.systems)"
vendor = "SNR"
# A Russian network-equipment distributor's public file server, with Dahua firmware from about 2015-2018
kind = "mirror"

firmware_site = "https://snr.systems/site/data-files/Dahua/"

# Product groups above the model folders
category_folders = ("Firmware", "DVR", "NVR", "IPC", "VTO", "VTH", "PTZ", "XVR", "HCVR")
generic_folder = re.compile(NO_MODEL_FOLDER.pattern + r"|^(Russian|Rus|English|Eng)$", re.IGNORECASE)


def keep(file_name, url):
    # CONFIG_ files only change the language and web interface
    return not file_name.upper().startswith("CONFIG_") and is_firmware_file(file_name, url)


def get_firmwares():
    # About 27 KB/s and a small tree, so crawl it gently and cache the listing
    return get_mirror_firmwares(firmware_site, category_folders=category_folders, generic_folder=generic_folder,
                                keep=keep, delay=0.5, max_failures=5, cache_file="tmp/snr_index.json")
