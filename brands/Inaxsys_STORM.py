import re

from util.sharepoint import SharePointShare

name = "Inaxsys STORM"
vendor = "Inaxsys"

# Inaxsys publishes firmware through an anonymous SharePoint share linked from https://www.inaxsys.com/en/support/download-center
share = SharePointShare(
    "https://inaxsys.sharepoint.com/:f:/s/Download/EtosnRrO1yBDkxCvhOnvFcEBcmhe1-IxCWkEJbrlWXa44A?e=eemIuJ",
    "https://inaxsys.sharepoint.com/sites/Download",
)

# Only the STORM line is Dahua (Legend/Legend NX are Uniview, "Older Inaxsys Cameras" are ACTi)
storm_root = "/sites/Download/Shared Documents/Cloud/STORM"
# NVR firmware is in <root>/<model>/..., camera firmware in <root>/<category>/<model>/...
nvr_root = f"{storm_root}/Storm NVR Firmware"
camera_root = f"{storm_root}/Storm Cameras Firmware"

firmware_extensions = (".bin", ".zip")

# Dahua version strings, e.g. V4.002.0000000.8.R.241030. Files without one are tools (config tool, player, ...)
version_regex = re.compile(r"V\d+\.\d+\.[0-9A-Za-z]+\.\d+\.[A-Z]\.\d{6,8}", re.IGNORECASE)


def download_file(url, part_name):
    share.download_file(url, part_name)


def get_model_names(model_folder):
    # Folders are named after models, e.g. "INS324K", "Old INS3216NVR", "INS8824KN INS161624KN (S2)" or "DO4IRF (Old)"
    cleaned = re.sub(r"\(\s*old\s*\)|^old\s+|\s+latest$", "", model_folder, flags=re.IGNORECASE).strip()
    models = re.findall(r"INS[0-9A-Z-]+", cleaned)
    return models if models else [cleaned]


def add_firmwares(firmwares, folder, models):
    for path, files in share.walk(folder):
        # Skip folders holding an already extracted firmware (update.img, *.cramfs.img, ...)
        if any(file["Name"].lower().endswith(".img") for file in files):
            continue

        for file in files:
            file_name = file["Name"]
            version = version_regex.search(file_name)
            if not file_name.lower().endswith(firmware_extensions) or not version:
                continue

            # The same file is often in several model folders, so merge the models
            if file_name in firmwares:
                for model in models:
                    if model not in firmwares[file_name]["camera_name"]:
                        firmwares[file_name]["camera_name"].append(model)
                continue

            firmwares[file_name] = {
                "camera_name": list(models),
                "firmware_version": version[0],
                "firmware_size": None,
                "firmware_notes": None,
                "firmware_changelog": None,
                "firmware_previous": None,
                "firmware_latest": share.file_url(file["ServerRelativeUrl"]),
                "firmware_latest_file_name": file_name,
                "release_date": file["TimeLastModified"][:10],
            }


def get_firmwares():
    firmwares = {}

    model_folders = list(share.list_folders(nvr_root))
    for category in share.list_folders(camera_root):
        model_folders += share.list_folders(category["ServerRelativeUrl"])

    for model_folder in model_folders:
        add_firmwares(firmwares, model_folder["ServerRelativeUrl"], get_model_names(model_folder["Name"]))

    return list(firmwares.values())
