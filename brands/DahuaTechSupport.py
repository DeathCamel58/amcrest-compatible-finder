from util.mirror import get_mirror_firmwares

name = "files.dahuatech.support"
vendor = "Dahua"
# A public Dahua support file server (directory index), organised by product category and model
kind = "mirror"

firmware_site = "https://files.dahuatech.support/Firmwares/"

# It answers 406 to non-browser user agents
headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"}

skip_directories = ("Software", "webplugin")
category_folders = ("Access Control", "IPC_Cameras", "ITC_Cameras", "NVD", "Others", "Recorders", "SD_Cameras(PTZ)",
                    "TPC_Cameras", "VDP", "WhiteBoard_Deephub")


def get_firmwares():
    return get_mirror_firmwares(firmware_site, headers=headers, skip_directories=skip_directories,
                                category_folders=category_folders)


def download_file(url, part_name):
    from util import http
    with http.get(url, stream=True, headers=headers) as r:
        r.raise_for_status()
        with open(part_name, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
