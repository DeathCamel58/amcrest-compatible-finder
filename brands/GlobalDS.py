import re

from util import gdrive
from util.file_type import classify_file_type

name = "Global DS Hungary"
vendor = "Global DS"
# Global DS (DSBOLT), a Hungarian Dahua distributor, keeps its firmware in a public Google Drive folder linked from
# https://dsszaki.dsbolt.hu/dahua.html: about 300 firmwares in product-line folders (VDP intercoms, DVR/HCVR/XVR,
# NVR, IPC, ANPR, TPC, ACS, PTZ), with many EMEA intercom builds from 2019-2025
kind = "mirror"
firmware_folder_id = "1W8E57ZXQLepJqLvaRfyulVZl3nH2cZ60"

firmware_extension = re.compile(r"\.(bin|zip|img|rar)$", re.I)
dahua_version = re.compile(r"V\d\.\d{3}\.[\w.]*?[RT][._](\d{6,8})", re.I)
# Pieces of already extracted firmware that sometimes get uploaded next to the real image
extracted_part = re.compile(r"^(dhboot|kernel|sign|update|crypt|safeEnv|wiegand|.*-x\.(squash|cram)fs)", re.I)
# Folders are Hungarian descriptions ("VTO - Kapuegységek", "XVR5-ös széria") or model names, sometimes with a note
# after them ("VTO3211_P1_2_4 (Ha régi VT-s eszközöd van, ...)", "VTH5422HB;VTH5422HW"). Only the leading model part
# counts, and only when it names a model or series (has a number)
model_folder = re.compile(r"^(?:DHI?-)?(?:IPC|NVR|XVR|HCVR|DVR|VTO|VTH|VTS|ASI|ASC|TPC|SD|PTZ|ITC|ARC)[A-Za-z0-9()_;/-]*")


def get_version_and_date(file_name):
    match = dahua_version.search(file_name)
    if not match:
        return None, None
    date = match.group(1)
    if len(date) == 6:
        date = f"20{date}"
    return match.group(0), f"{date[0:4]}-{date[4:6]}-{date[6:8]}"


def is_firmware(file_name):
    # Dahua-style names with a version, or a file named after a model ("NVR608-XX-4KS2.zip")
    named = bool(dahua_version.search(file_name)) or bool(re.match(model_folder.pattern + r"\.(bin|zip)$", file_name))
    return (bool(firmware_extension.search(file_name)) and named and not extracted_part.match(file_name)
            and classify_file_type(file_name) == "firmware")


def get_models(path):
    """Model names from the folders a file is in, e.g. ["VTH5422HB", "VTH5422HW"] from "VTH5422HB;VTH5422HW"."""
    models = []
    for folder in path:
        match = model_folder.match(folder)
        if not match:
            continue
        for model in match.group(0).split(";"):
            model = model.strip(" _-/")
            if re.search(r"\d", model) and model not in models:
                models.append(model)
    return models


def get_firmwares():
    firmwares_by_name = {}
    for file in gdrive.walk(firmware_folder_id):
        if not is_firmware(file["name"]):
            continue

        # The first folder is the product line ("VDP - IP video kaputelefonok"); models are below it
        models = get_models(file["path"][1:])
        existing = firmwares_by_name.get(file["name"])
        if existing:
            existing["camera_name"] += [m for m in models if m not in existing["camera_name"]]
            continue

        version, release_date = get_version_and_date(file["name"])
        firmwares_by_name[file["name"]] = {
            "camera_name": models,
            "firmware_version": version,
            "firmware_size": None,
            "firmware_notes": f"Folder: {'/'.join(file['path'])}",
            "firmware_changelog": None,
            "firmware_previous": None,
            "firmware_latest": gdrive.download_url(file["id"]),
            # The published name, including the "1_"/"2_" order prefixes some intercom folders use (the order to
            # install them in)
            "firmware_latest_file_name": file["name"],
            "release_date": release_date,
        }
    return list(firmwares_by_name.values())


def download_file(url, part_name):
    gdrive.download(url, part_name)
