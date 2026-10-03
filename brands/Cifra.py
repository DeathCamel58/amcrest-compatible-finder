from util.mirror import get_mirror_firmwares
from brands.Viatec import generic_folder, keep

name = "Cifra (ftp.cifra.cv.ua)"
vendor = "Cifra"
# A Ukrainian Dahua distributor's public file server (laid out like Viatec's)
kind = "mirror"

firmware_site = "https://ftp.cifra.cv.ua/Dahua/Firmware/"

category_folders = ("Accsess Control", "DVR", "HCVR", "IPC", "NVR", "PTZ", "VDP", "XVR", "Main FW", "SIP FW",
                    "SIP2.0-FW")


def get_firmwares():
    return get_mirror_firmwares(firmware_site, category_folders=category_folders, generic_folder=generic_folder,
                                keep=keep, max_failures=5)
