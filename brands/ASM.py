import re
from urllib.parse import unquote

from util.mirror import get_mirror_firmwares, get_models_from_path
from util.oem_helpers import crawl_index, is_dahua_firmware_name, make_firmware

name = "ASM (ftp.asm.cz)"
vendor = "ASM"
# A Czech Dahua distributor's public file server, not a vendor's own listing
kind = "mirror"

firmware_site = "https://ftp.asm.cz/Dahua/"

# Presentations and the LAN (network switch) tree hold no camera, recorder, intercom or access control firmware
skip_directories = ("prezentace", "LAN")
# kamerove_systemy = camera systems, videovratni = intercoms, pristupove_systemy = access control,
# zabezpecovaci_systemy = alarm systems
category_folders = ("kamerove_systemy", "videovratni", "pristupove_systemy", "zabezpecovaci_systemy", "Firmware",
                    "Firmwares", "FW")

# XtendLan is ASM's own brand of rebadged Dahua recorders, one folder per model (e.g. "DVR-H470PG,H870PG,H1670PG")
xtendlan_site = "https://ftp.asm.cz/XtendLan/"
xtendlan_vendor = "XtendLan"
xtendlan_source = "ASM (ftp.asm.cz) - XtendLan"
# TFTP recovery tools and PC utilities (proxy server, HDD copy tool, ...)
xtendlan_skip_directories = ("TFTP_Upgrade_Tools", "Utility", "Tools")
# Grouping folders below a model folder: TFTP builds, firmware branches like "2.616", ONVIF builds, "stary" (old)
xtendlan_generic_folder = re.compile(r"^(TFTP|Onvif|stary|\d+(\.\d+)+)$", re.IGNORECASE)


def is_xtendlan_firmware(url):
    path = unquote(url[len(xtendlan_site):])
    if any(part in xtendlan_skip_directories for part in path.split("/")[:-1]):
        return False
    file_name = path.split("/")[-1]
    # CONFIG_ files only change the language and web interface
    if file_name.upper().startswith("CONFIG_"):
        return False
    return is_dahua_firmware_name(file_name)


def get_xtendlan_models(relative_directory):
    """Models from an XtendLan folder path, e.g. "DVR-x70-J_JE_JE2_PG_PJ_PK_PM_PU/Firmware/DVR-470,870,1670PK/"."""
    folders = []
    for folder in relative_directory.split("/"):
        # "pouze_pro_1670JE" = "only for 1670JE"
        folder = re.sub(r"^pouze_pro_", "", folder, flags=re.IGNORECASE)
        # "XL-ICA-H662-Z4820, SC110, Z4822": spaces would make it look like a description
        folder = re.sub(r",\s+", ",", folder)
        if folder and not xtendlan_generic_folder.search(folder):
            folders.append(folder)
    return get_models_from_path("/".join(folders), category_folders)


def get_xtendlan_firmwares():
    firmwares = []
    for url, directory in crawl_index(xtendlan_site):
        if not is_xtendlan_firmware(url):
            continue

        relative_directory = unquote(directory[len(xtendlan_site):]).strip("/")
        firmware = make_firmware(get_xtendlan_models(relative_directory), url,
                                 file_name=unquote(url.split("/")[-1]))
        firmware["firmware_notes"] = f"Folder: {relative_directory}" if relative_directory else None
        firmware["vendor"] = xtendlan_vendor
        firmware["source"] = xtendlan_source
        firmwares.append(firmware)
    return firmwares


def get_firmwares():
    firmwares = get_mirror_firmwares(firmware_site, skip_directories=skip_directories, category_folders=category_folders)
    try:
        firmwares += get_xtendlan_firmwares()
    except Exception as err:
        print(f"\tASM: failed to list XtendLan: {err!r}")
    return firmwares
