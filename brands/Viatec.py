import re

from util.mirror import NO_MODEL_FOLDER, get_mirror_firmwares, is_firmware_file

name = "Viatec (ftp.viatec.ua)"
vendor = "Viatec"
# A Ukrainian Dahua distributor's public file server
kind = "mirror"

firmware_site = "https://ftp.viatec.ua/Dahua/Firmware/"

# Product groups and firmware branches ("Main FW", "SIP2.0-FW" for intercoms; "Lite", "AI", "OLD DVR", ...)
category_folders = ("Accsess Control", "HCVR & DVR", "IPC", "ITC", "IVSS", "Keyboard", "Mobile", "NVR", "Network",
                    "PTZ", "VDP", "Main FW", "SIP FW", "SIP2.0-FW", "OLD DVR")
# Besides those, folders without a model number (platform code names, languages, "master"/"slave" boards), and
# notes like "for_(P1)-S1" / "only_for_13xx-S4"
generic_folder = re.compile(NO_MODEL_FOLDER.pattern + r"|^(only_)?for|^p2p$|!", re.IGNORECASE)


def keep(file_name, url):
    # CONFIG_ files only change the language and web interface
    return not file_name.upper().startswith("CONFIG_") and is_firmware_file(file_name, url)


def get_firmwares():
    # The server's robots.txt disallows crawlers; this is for archiving, so crawl it, but gently
    return get_mirror_firmwares(firmware_site, category_folders=category_folders, generic_folder=generic_folder,
                                keep=keep, delay=0.5, max_failures=5, cache_file="tmp/viatec_index.json")
