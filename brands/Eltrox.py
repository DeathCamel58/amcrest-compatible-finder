import re

import requests

from util.mirror import NO_MODEL_FOLDER, get_mirror_firmwares, is_firmware_file

name = "Eltrox (ftp.eltrox.pl)"
vendor = "Eltrox"
# A Polish distributor's public file server
kind = "mirror"

firmware_site = "https://ftp.eltrox.pl/FIRMWARE/"

# Folder -> vendor: Dahua's own files, and Polish brands of rebadged Dahua cameras and recorders. Only Dahua style
# firmware names are kept, since some of these brands also sell other makers' devices
brand_folders = {
    "DAHUA": "Eltrox",
    "KENIK": "Kenik",
    "EASYCAM": "EasyCam",
    "NOTIS": "Notis",
    "ZEUS": "Zeus",
    "IRBIS": "Irbis",
    "KONIG": "Konig",
}

# Besides folders without a model number, "KAMERY_2MPX" (2 Mpx cameras)
generic_folder = re.compile(NO_MODEL_FOLDER.pattern + r"|^kamery", re.IGNORECASE)

# The server rate limits (and then stops answering), so list it one request at a time with a pause, and give up
# on it for the run after a few failures in a row
delay = 1.5
max_failures = 3
timeout = (15, 60)
# Listing all of it takes minutes, so directory listings are cached between runs
cache_file = "tmp/eltrox_index.json"
cache_days = 14


def get_vendor(brand_folder):
    return brand_folders.get(brand_folder.strip("/").upper(), vendor)


def is_reachable():
    """One quick try (no retries), so a host that isn't answering costs seconds, not minutes."""
    try:
        status = requests.get(firmware_site, timeout=(10, 20), headers={"User-Agent": "Mozilla/5.0"}).status_code
    except requests.RequestException as err:
        status = err.__class__.__name__
    if status == 200:
        return True
    print(f"\tEltrox: {firmware_site} isn't answering ({status}), listing it from the cache")
    return False


def get_firmwares():
    offline = not is_reachable()
    firmwares = []
    for brand_folder in brand_folders:
        brand_firmwares = get_mirror_firmwares(f"{firmware_site}{brand_folder}/", generic_folder=generic_folder,
                                               keep=is_firmware_file, delay=delay, max_failures=max_failures,
                                               timeout=timeout, cache_file=cache_file, cache_days=cache_days,
                                               offline=offline)
        for firmware in brand_firmwares:
            firmware["vendor"] = get_vendor(brand_folder)
        firmwares += brand_firmwares
    return firmwares
