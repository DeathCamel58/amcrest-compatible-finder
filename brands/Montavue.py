from urllib.parse import unquote

from util.oem_helpers import FIRMWARE_EXTENSIONS, crawl_index, is_dahua_firmware_name, make_firmware, sanitize_name

name = "Montavue"
vendor = "Montavue"

# Only the Classic line is Dahua based (Nexus is not)
firmware_site = "https://firmware-downloads.fragrant-snow-5a3f.workers.dev/Classic%2F"


def is_directory(href):
    # This index encodes the path separators in its links
    return href.endswith("/") or href.endswith("%2F")


def get_firmwares():
    firmwares = []

    for url, directory_url in crawl_index(firmware_site, is_directory=is_directory):
        file_name = unquote(url.split("%2F")[-1].split("/")[-1])
        if not file_name.lower().endswith(FIRMWARE_EXTENSIONS):
            continue

        # Model folders list every model sharing the firmware, e.g. "MTT4095 | MTB4095 | MTD4095"
        model_folder = unquote(directory_url).rstrip("/").split("/")[-1]
        models = [model.strip() for model in model_folder.split("|") if model.strip()]

        # The URL's path separators are encoded, so the file name always has to be given explicitly.
        # Some files have generic names like "4102 Firmware.zip", so make those unique
        local_file_name = file_name
        if not is_dahua_firmware_name(file_name):
            local_file_name = sanitize_name(f"Montavue_{models[0] if models else ''}_{file_name}")

        firmwares.append(make_firmware(models, url, local_file_name))

    return firmwares
