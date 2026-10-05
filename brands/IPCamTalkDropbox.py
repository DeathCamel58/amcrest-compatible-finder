from util import dropbox
from util.mirror import is_firmware_file
from util.oem_helpers import make_firmware

name = "ipcamtalk Dropbox folder (Dahua OEM firmware list)"
vendor = "ipcamtalk"
# The Dropbox folder of ipcamtalk's "Dahua (OEM) Firmware list" thread (22680): 2016-2017 General_ recorder builds
kind = "mirror"

folder_url = ("https://www.dropbox.com/scl/fo/dmis5l5io13gm6n9g26wi/AAW3TJARyPi1gS44qI-x0XE"
              "?rlkey=x8aswlr60t4i2xh4ryfrgc3vv&dl=0")


def to_firmwares(files):
    firmwares = []
    for file in files:
        if not is_firmware_file(file["file_name"], file["url"]):
            continue
        model = file["path"].split("/")[0] if file.get("path") else None
        firmware = make_firmware([model] if model else [], file["url"], file_name=file["file_name"],
                                 notes="ipcamtalk Dahua (OEM) firmware list")
        firmware["firmware_size"] = file.get("size")
        firmwares.append(firmware)
    return firmwares


def get_firmwares():
    return to_firmwares(dropbox.walk_folder(folder_url))


def download_file(url, part_name):
    dropbox.download(url, part_name)
