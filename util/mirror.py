import re
from urllib.parse import unquote

from util.hardware import FIRMWARE_LIKE
from util.oem_helpers import DAHUA_VERSION, crawl_index, is_dahua_firmware_name, make_firmware

# Folder names that organise files rather than name a model (languages, old/new builds, years, ...)
GENERIC_FOLDER = re.compile(
    r"^(old|new|latest|archive|previous|fw|update|beta|test|backup|other|others|misc|"
    r"english|eng|polish|czech|german|french|spanish|italian|russian|multilang|"
    r"general|dh|dhi|dahua|"
    r"\d{4}(_a_starsi)?|v\d[\d.]*|\d+)$"
    # Descriptions rather than models: "PTZ firmware", "old-firmware", "fimrware", "Greek config", "Upgrade_z_V1.0",
    # numbered groups like "02-Pin-hole", notes like "SIP_4.5_210812_CZ" or "Custom_FW_pro_..."
    r"|firm?ware|fimrware|config|upgrade|^custom|^sip_|^\d{2}-|\s"
    # Numbered category folders like "04IPC", "05SD", "060-Lite", "06VDP"
    r"|^\d{2,3}-?[A-Za-z]+$",
    re.IGNORECASE,
)


def get_models_from_path(relative_directory, category_folders=()):
    """The nearest folder that names models, split into models (e.g. "DVR0804HD-A,DVR1604HD-L/old/").
    category_folders are the mirror's own grouping folders (e.g. "IPC_Cameras"), which never name a model."""
    for folder in reversed([part for part in relative_directory.split("/") if part]):
        # Some mirrors put each firmware in a folder named after it
        if FIRMWARE_LIKE.search(folder) or DAHUA_VERSION.search(folder):
            continue
        if not GENERIC_FOLDER.search(folder) and folder not in category_folders:
            return [model.strip() for model in re.split(r",|;", folder) if model.strip()]
    return []


def get_mirror_firmwares(start_url, headers=None, skip_directories=(), category_folders=()):
    """Every Dahua firmware below a directory index, with models taken from the folder names."""
    firmwares = []
    for url, directory in crawl_index(start_url, headers=headers, skip_directories=skip_directories):
        file_name = unquote(url.split("/")[-1])
        if not is_dahua_firmware_name(file_name):
            continue

        relative_directory = unquote(directory[len(start_url):])
        firmware = make_firmware(get_models_from_path(relative_directory, category_folders), url, file_name=file_name)
        firmware["firmware_notes"] = f"Folder: {relative_directory.strip('/')}" if relative_directory.strip("/") else None
        firmwares.append(firmware)

    return firmwares
