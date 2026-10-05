from util import mediafire
from util.mirror import is_firmware_file
from util.oem_helpers import make_firmware

name = "ipcamtalk MediaFire folder (IPC-HFW4300S)"
vendor = "ipcamtalk"
# A public MediaFire folder of 2014-2015 IPC-HX5(4)XXX / Adreia builds, posted in ipcamtalk thread 35959
kind = "mirror"

folder_key = "ya1odj5eeubzq"
source_page = "https://www.mediafire.com/folder/ya1odj5eeubzq/IPC-HFW4300S"


def to_firmwares(files):
    firmwares = []
    for file in files:
        if not is_firmware_file(file["name"], file["url"]):
            continue
        firmware = make_firmware(["IPC-HFW4300S"], file["url"], file_name=file["name"],
                                 notes="Posted on ipcamtalk (thread 35959)")
        firmware["firmware_size"] = file["size"]
        if file.get("sha256"):
            firmware["sha256"] = file["sha256"]
        firmwares.append(firmware)
    return firmwares


def get_firmwares():
    return to_firmwares(mediafire.list_folder(folder_key))


def download_file(url, part_name):
    mediafire.download(url, part_name)
