import re

from util import gdrive

name = "ENS Diamond"
vendor = "ENS Security"

# ENS Security (formerly Eastern CCTV) publishes firmware in a public Google Drive folder, linked from enssecurity.com
firmware_folder_id = "1ABATkC9nNyXDnwvXoe4PrExKmDoiELMV"

# Top level product lines that aren't Dahua based (Uniview, Akuvox, Hikvision .dav, .fls recorders)
excluded_lines = {"UNV", "Akuvox", "H Series", "Titanium", "Emerald"}

# Folders that hold versions of a model's firmware rather than being the model itself
version_folder = re.compile(r"^(new(est)?|old(er)?( firmware| version)?|latest|special|(version )?[\d.]+( \(latest\))?)$", re.I)

firmware_extension = re.compile(r"\.(bin|zip|img|rar)$", re.I)
# Dahua firmware names, e.g. General_IPC-HX1XXX-Kant_EngSpnRus_PN_V2.860.0000000.40.R.260515.bin
dahua_prefix = re.compile(r"^(\d+_)?(General|Customer|DH|DHI|ENS|TEMP)_", re.I)
dahua_version = re.compile(r"V\d\.\d{3}\.[\w.]*?[RT][._]\d{6,8}", re.I)
# Pieces of already extracted firmware that sometimes get uploaded alongside the real image
extracted_part = re.compile(r"^(dhboot|kernel|sign|update|crypt|safeEnv|wiegand|.*-x\.(squash|cram)fs)", re.I)


def should_enter(path):
    return path[0] not in excluded_lines


def is_dahua_firmware(file_name):
    if not firmware_extension.search(file_name) or extracted_part.match(file_name):
        return False
    return bool(dahua_prefix.match(file_name) or dahua_version.search(file_name))


def get_model_names(path):
    # Walk up from the file's folder past New/Old/version folders to the model folder
    for folder in reversed(path):
        if not version_folder.match(folder):
            # Model folders are often a comma separated list of models
            return [model.strip(" _") for model in folder.split(",") if model.strip(" _")]
    return []


def get_version_and_date(file_name):
    # R is a release build, T a test build
    match = re.search(r"V\d\.\d+\.[\w.]+?[RT][._](\d{6,8})", file_name, re.I)
    if not match:
        return None, None

    version = match.group(0)
    date = match.group(1)
    if len(date) == 6:
        date = f"20{date}"
    return version, f"{date[0:4]}-{date[4:6]}-{date[6:8]}"


def get_firmwares():
    files = gdrive.walk(firmware_folder_id, should_enter=should_enter)

    # The same image is often uploaded to several model folders (and both New and Old), so merge by file name
    firmwares_by_name = {}
    for file in files:
        if not is_dahua_firmware(file["name"]):
            continue

        models = get_model_names(file["path"])
        existing = firmwares_by_name.get(file["name"])
        if existing:
            for model in models:
                if model not in existing["camera_name"]:
                    existing["camera_name"].append(model)
            continue

        version, release_date = get_version_and_date(file["name"])
        firmwares_by_name[file["name"]] = {
            "camera_name": models,
            "firmware_version": version,
            "firmware_size": None,
            # e.g. Diamond/IPC; files sitting directly in a model folder only get the line
            "firmware_notes": f"Product line: {'/'.join(file['path'][:2] if len(file['path']) > 2 else file['path'][:1])}",
            "firmware_changelog": None,
            "firmware_previous": None,
            "firmware_latest": gdrive.download_url(file["id"]),
            "firmware_latest_file_name": file["name"],
            "release_date": release_date,
        }

    return list(firmwares_by_name.values())


def download_file(url, part_name):
    gdrive.download(url, part_name)
