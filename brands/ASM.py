from util.mirror import get_mirror_firmwares

name = "ASM (ftp.asm.cz)"
vendor = "ASM"
# A Czech Dahua distributor's public file server, not a vendor's own listing
kind = "mirror"

firmware_site = "https://ftp.asm.cz/Dahua/"

# Presentations and the LAN (network switch) tree hold no camera, recorder, intercom or access control firmware
skip_directories = ("prezentace", "LAN")
# kamerove_systemy = camera systems, videovratni = intercoms, pristupove_systemy = access control,
# zabezpecovaci_systemy = alarm systems
category_folders = ("kamerove_systemy", "videovratni", "pristupove_systemy", "zabezpecovaci_systemy", "Firmware",
                    "Firmwares", "FW")


def get_firmwares():
    return get_mirror_firmwares(firmware_site, skip_directories=skip_directories, category_folders=category_folders)
