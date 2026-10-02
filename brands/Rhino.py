import re
from urllib.parse import unquote

from util.oem_helpers import crawl_index, is_dahua_firmware_name, make_firmware

name = "Rhino"
vendor = "Rhino"

firmware_site = "http://downloads.rhinoco.com.au/dvr/"

# Folder names that are categories rather than models
CATEGORY_FOLDERS = {"dvr", "dahua", "cameras", "hdcvi", "nvrpro", "intercom", "firmware", "software", "old", "new"}


def get_model(file_name, directory_url):
    # Use the deepest folder that looks like a model, falling back to the file name prefix
    folders = [unquote(folder) for folder in directory_url[len(firmware_site):].strip("/").split("/") if folder]
    for folder in reversed(folders):
        if folder.lower() not in CATEGORY_FOLDERS:
            return folder

    match = re.match(r'(?:General|DH|DHI|Customer|Group)_([^_]+)', file_name, re.IGNORECASE) or re.match(r'([^_]+)_', file_name)
    return match[1] if match else None


def get_firmwares():
    firmwares = []

    for url, directory_url in crawl_index(firmware_site):
        file_name = unquote(url.split("/")[-1])
        # Rhino also resells non Dahua OEMs, so only keep Dahua firmware
        if not is_dahua_firmware_name(file_name):
            continue

        firmwares.append(make_firmware(get_model(file_name, directory_url), url))

    return firmwares
