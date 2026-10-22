import json
import os
from urllib.parse import unquote, urlparse

from util import dropbox, gdrive, mediafire, mega, onedrive, yandex
from util.download_firmware import _http_download
from util.mirror import is_firmware_upload
from util.oem_helpers import make_firmware

name = "Shared links (forums and cloud drives)"
vendor = "Shared links"
# Firmware people shared on forums (ipcamtalk mostly) through cloud drives and file hosts, found by hand: a list of
# folders and files in brands/data/shared_links.json. Each says which vendor's firmware it is
kind = "mirror"

# Hosts that refuse requests without a browser's User-Agent (406)
BROWSER_UA_HOSTS = {"www.jjdigital.com.au"}
BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"

LINKS_FILE = os.path.join(os.path.dirname(__file__), "data", "shared_links.json")


def load_links():
    with open(LINKS_FILE) as f:
        return json.load(f)


def to_firmware(link, url, file_name):
    notes = [note for note in (link.get("notes"), f"Shared at {link['source_page']}" if link.get("source_page") else None)
             if note]
    firmware = make_firmware(link.get("model") or [], url, file_name=file_name, notes="; ".join(notes) or None)
    firmware["vendor"] = link.get("vendor") or "Dahua"
    return firmware


def list_folder(url):
    if yandex.is_yandex(url):
        return [(file["url"], file["name"]) for file in yandex.walk(url)]
    if "dropbox.com" in url:
        return [(file["url"], file["file_name"]) for file in dropbox.walk_folder(url)]
    if "drive.google.com" in url:
        folder_id = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
        return [(gdrive.download_url(file["id"]), file["name"]) for file in gdrive.walk(folder_id)]
    raise ValueError(f"don't know how to list {url}")


def get_firmwares():
    links = load_links()
    firmwares = []
    for folder in links.get("folders", []):
        try:
            files = list_folder(folder["url"])
        except Exception as err:
            print(f"\tCouldn't list {folder['url']}: {err!r}")
            continue
        firmwares += [to_firmware(folder, url, file_name) for url, file_name in files
                      if is_firmware_upload(file_name, url)]
    for file in links.get("files", []):
        file_name = file.get("file_name") or unquote(urlparse(file["url"]).path.rsplit("/", 1)[-1])
        firmwares.append(to_firmware(file, file["url"], file_name))
    return firmwares


def download_file(url, part_name):
    host = (urlparse(url).hostname or "").lower()
    if yandex.is_yandex(url):
        yandex.download(url, part_name)
    elif onedrive.is_onedrive(url):
        onedrive.download(url, part_name)
    elif host == "mega.nz":
        mega.download(url, part_name)
    elif host.endswith("google.com"):
        gdrive.download(url, part_name)
    elif host.endswith("dropbox.com"):
        dropbox.download(url, part_name)
    elif host.endswith("mediafire.com"):
        mediafire.download(url, part_name)
    else:
        _http_download(url, part_name, headers={"User-Agent": BROWSER_UA} if host in BROWSER_UA_HOSTS else None)
