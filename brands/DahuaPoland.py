import re

from util.mirror import NO_MODEL_FOLDER, get_mirror_firmwares, is_firmware_file

name = "files.dahua.support"
vendor = "Dahua"
# Dahua Poland's public file server, not a listing of Dahua's own download center
kind = "mirror"

# The root also holds price lists, photos and CAD files, so only these two trees are crawled
firmware_sites = ["https://files.dahua.support/Firmware/", "https://files.dahua.support/Solutions/"]

# Product groups: recorders, intercoms, switches, decoders, thermal monoculars, keyboards, video walls, radars,
# testers, interactive boards, speakers, ...
category_folders = ("Rejestratory", "Domofony", "Switche", "Dekodery", "Monokulary", "Klawiatury", "Macierze",
                    "Radary", "Testery", "TabliceInteraktywne", "Głośniki", "WiFi", "_RTMP", "Access Control",
                    "Digital Signage", "HDCVI", "NVR", "PTZ", "AI", "Non-AI", "Consumer", "Entry", "4G")
# Besides those, folders without a model number: platform code names ("Cauchy", "Nobel"), "PTZ_MCU", ...
generic_folder = re.compile(NO_MODEL_FOLDER.pattern + r"|^4G$", re.IGNORECASE)


def get_firmwares():
    firmwares = []
    for site in firmware_sites:
        firmwares += get_mirror_firmwares(site, category_folders=category_folders, generic_folder=generic_folder,
                                          keep=is_firmware_file)
    return firmwares
