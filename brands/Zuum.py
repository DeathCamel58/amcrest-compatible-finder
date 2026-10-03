from util import dropbox
from util.oem_helpers import is_dahua_firmware_name, make_firmware

name = "Zuum"
vendor = "Zuum"

# Redirects to Zuum's public Dropbox folder, which has a "Product Firmware/<model>/" folder per product
firmware_site = "https://www.zuummedia.com/downloads/"
# Where that page redirects to. Used when the page itself is unavailable (it sometimes answers 403)
known_folder = ("https://www.dropbox.com/scl/fo/i888ztms8musfusi2l33h/ABXb55qEbCWmtggTrypfMgs"
                "?rlkey=p9i49mholh44cstdss7ku8rom&dl=0")
firmware_folder = "Product Firmware"


def get_firmwares():
    try:
        folder_url = dropbox.resolve_link(firmware_site)
    except Exception:
        folder_url = None
    if not folder_url or "/scl/fo/" not in folder_url:
        folder_url = known_folder

    firmwares = []
    for file in dropbox.walk_folder(folder_url, top_folders=(firmware_folder,)):
        # The folder also holds HDMI matrix drivers, IR codes and non-Dahua NVR firmware
        if not is_dahua_firmware_name(file["file_name"]):
            continue

        # "Product Firmware/LSNVR8CHV2-4K" or "Product Firmware/H10X10V1/Firmware": the model is the first folder
        parts = file["path"].split("/")
        model = parts[1] if len(parts) > 1 else None

        # Zuum's names already carry a Dahua version (often with the model in front), so they're kept as-is
        firmware = make_firmware([model] if model else [], file["url"], file_name=file["file_name"])
        firmware["firmware_size"] = file["size"]
        firmwares.append(firmware)

    return firmwares


def download_file(url, part_name):
    dropbox.download(url, part_name)
