from urllib.parse import unquote

from util.oem_helpers import crawl_index, is_dahua_firmware_name, make_firmware

name = "Optiview"
vendor = "Optiview"

# The main site is behind Cloudflare, but the legacy IIS directory listing is still public
firmware_site = "http://support.optiviewusa.com/IPC/IPxx_firmware/"


def get_firmwares():
    firmwares = []

    for url, _ in crawl_index(firmware_site):
        file_name = unquote(url.split("/")[-1])
        if not is_dahua_firmware_name(file_name):
            continue

        # Optiview prefixes the Dahua file name with their own model, e.g. IP4MIAB-36_IPC-HX5X3X-Rhea_...
        firmwares.append(make_firmware(file_name.split("_")[0], url))

    return firmwares
