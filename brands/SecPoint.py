import re
from urllib.parse import unquote

from util import http
from util.file_type import classify_file_type
from util.oem_helpers import crawl_index, is_dahua_firmware_name, make_firmware, sanitize_name

name = "SecPoint (secpoint.it)"
vendor = "SecPoint"
# An Italian distributor's open file index. Cloudflare only lets it through from EU addresses (checked from Denmark
# on 2026-10-05); elsewhere the listing is empty and the run moves on
kind = "mirror"

firmware_site = "https://secpoint.it/Documenti_prodotti/Firmware/"
# Only the Dahua folders: the rest is ELMO, Hikvision and alarm panels
dahua_folders = ("Tvcc/DAHUA/", "Videocitofonia%20Dahua/")
ARCHIVE = re.compile(r"\.(bin|zip|rar)$", re.IGNORECASE)


def local_name(path):
    """Dahua style names are kept; generic ones ("VTH_SIP.zip") get the folder they're in, so they can't collide."""
    file_name = path.rsplit("/", 1)[-1]
    if is_dahua_firmware_name(file_name):
        return None
    folder = path.rsplit("/", 2)[-2] if "/" in path else ""
    return f"SecPoint_{sanitize_name(folder)}_{file_name}" if folder else f"SecPoint_{file_name}"


MODEL = re.compile(r"\b[A-Z]{2,}[A-Z0-9]*\d[A-Z0-9]*(?:-[A-Z0-9]+)*(?:\(-[A-Z]\))?", re.IGNORECASE)


def model_from_folder(path):
    """Model numbers in the file's folder ("XVR5X04-X _ XVR5X08-X" -> both), else in its name
    ("VTO1210C-X_SIP.zip" -> "VTO1210C-X")."""
    parts = path.split("/")
    folder = parts[-2].split(" con ")[0] if len(parts) > 1 else ""
    models = [m for m in MODEL.findall(folder) if m.upper() not in ("NVR", "DVR", "HDCVI")]
    if not models:
        models = [m for m in MODEL.findall(parts[-1].rsplit(".", 1)[0]) if not m.upper().startswith("V4")]
    return list(dict.fromkeys(models))


def get_firmwares():
    # Get past Cloudflare in the browser once; plain requests reuse its clearance for the crawl
    if not http.get_protected_html(firmware_site):
        print("\tSecPoint: the index isn't reachable from here (Cloudflare lets it through from EU addresses)")
        return []
    firmwares = []
    for folder in dahua_folders:
        for file_url, _ in crawl_index(firmware_site + folder, delay=0.6, max_failures=5):
            path = unquote(file_url.split("/Firmware/", 1)[1])
            file_name = path.rsplit("/", 1)[-1]
            if not ARCHIVE.search(file_name) or classify_file_type(file_name, file_url) != "firmware":
                continue
            if file_name.upper().startswith("CONFIG_"):
                continue  # language/web packs
            firmwares.append(make_firmware(model_from_folder(path), file_url, file_name=local_name(path),
                                           notes=f"secpoint.it folder: {path.rsplit('/', 1)[0]}"))
    return firmwares
