import re
from urllib.parse import unquote

from util.file_type import classify_file_type
from util.oem_helpers import NOT_FIRMWARE, crawl_index, make_firmware

name = "RVI FTP (ftp.rvi-cctv.ru)"
vendor = "RVI"
# RVI's old file server: one folder per RVI model under CVI/, DVR/, IP Camera/, NVR/ and O`zero/, with the upgrade
# file (2012-2018 Group_ and General_ builds, or RVI's own names like "HDR04LACV2_20161512.bin") next to the parts
# of a TFTP recovery (update.img, u-boot.bin.img, *.cramfs.img, flash.bin), manuals and pictures
kind = "vendor"

firmware_site = "https://ftp.rvi-cctv.ru/TFTP/"
UPGRADE_EXTENSIONS = (".bin", ".zip", ".rar", ".7z")
# TFTP recovery parts and whole-flash dumps, which aren't upgrade files
RECOVERY_PART = re.compile(r"^(flash|u-boot|uboot|kernel|rootfs|dhboot|safeenv|sign|update)[\w.-]*\.(bin|img)$|\.img$",
                           re.IGNORECASE)
MODEL_FOLDER = re.compile(r"^RVi-", re.IGNORECASE)


def model_from_path(path):
    """The RVI model folder a file is in ("RVi-HDR04LA-C v.2"), or None"""
    return next((folder for folder in reversed(path.split("/")[:-1]) if MODEL_FOLDER.match(folder)), None)


def is_upgrade_file(file_name, url):
    return (file_name.lower().endswith(UPGRADE_EXTENSIONS) and not RECOVERY_PART.search(file_name)
            and not NOT_FIRMWARE.search(file_name) and classify_file_type(file_name, url) == "firmware")


def get_firmwares():
    firmwares = []
    for url, _ in crawl_index(firmware_site, delay=0.5, max_failures=5, cache_file="tmp/rvi_ftp_index.json"):
        path = unquote(url[len(firmware_site):])
        file_name = path.split("/")[-1]
        if not is_upgrade_file(file_name, url):
            continue
        model = model_from_path(path)
        # Named like the RVI file library's files: RVI_<model>_<file>, since RVI's own names are short and generic
        local_name = f"RVI_{model.replace(' ', '_')}_{file_name}" if model else f"RVI_{file_name}"
        firmware = make_firmware([model] if model else [], url, file_name=local_name,
                                 notes=f"Folder: {'/'.join(path.split('/')[:-1])}")
        firmwares.append(firmware)
    return firmwares
