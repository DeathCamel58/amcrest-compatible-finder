import os
import re
import threading
import time
from urllib.parse import quote, unquote, urlparse

from util import http
from util.oem_helpers import is_dahua_firmware_name, make_firmware

name = "Wayback Machine"
vendor = "Unknown"
# Copies archived by the Wayback Machine: the way to get firmwares vendors have since removed
kind = "archive"

CDX_URL = "http://web.archive.org/cdx/search/cdx"
FIRMWARE_EXTENSIONS = r"(bin|zip|img|dav|rar|pak|7z)"

# (URL prefix to search, vendor the files came from, only keep Dahua-style file names[, options])
# Lorex and Rhino also host non-Dahua firmware with their own naming, so their names aren't filtered.
# Options:
#   exclude      path fragments to skip
#   include      regex a file's path must match (case-insensitive)
#   drop         regex for file names to skip (case-insensitive)
#   name_prefix  generic (non-Dahua-style) names become <prefix>_<parent folder>_<file>, since they'd clash with
#                other vendors' files of the same name
SOURCES = [
    # Its /Software/ folder holds PC tools (DiskManager, ThermalImagery, ...), not firmware
    ("dahuawiki.com/images/Files/", "Dahua", True, {"exclude": ["/Software/"]}),
    ("dahuawiki.com/images/Firmware/", "Dahua", True),
    # RVI (Russia) also sells non-Dahua recorders, and names some files only by date ("Group_191106.bin")
    ("rvigroup.ru/upload/files/", "RVI", True),
    ("amcrest.com/downloads/", "Amcrest", False),
    # Zuum's old Volusion store; its Software folder also holds HD-TVI recorders and HDMI matrix firmware
    ("www.zuummedia.com/v/vspfiles/", "Zuum", False, {"include": r"LSNVR|LSX|PSNVR|NVR|XVR|IPC"}),
    # Bosch's download store is mostly Bosch's own products; only the DIVAR recorders are Dahua made (DIVAR IP is
    # Bosch's own Windows appliance)
    ("downloadstore.boschsecurity.com/FILES/", "Bosch", False, {"include": r"DIVAR|DVR", "drop": r"divar.?ip"}),
    ("materialfile.dahuasecurity.com/uploads/", "Dahua", True),
    ("material.dahuasecurity.com/uploads/", "Dahua", True),
    ("files.dahuatech.support/Firmwares/", "Dahua", True),
    # Dahua's older sites' upload folders, for firmware removed from the current download center
    ("www.dahuasecurity.com/asset/upload/", "Dahua", True),
    ("us.dahuasecurity.com/wp-content/uploads/", "Dahua", True),
    ("www.dahuatech.com/upload/", "Dahua", True),
    ("www1.dahuatech.com/File/", "Dahua", True),
    ("ftp.asm.cz/Dahua/", "ASM", True),
    ("amcrest-firmwares.s3.amazonaws.com/", "Amcrest", False),
    ("s3.amazonaws.com/amcrest-files/", "Amcrest", False),
    ("sup-files.s3.us-east-2.amazonaws.com/", "Amcrest", False),
    ("www.lorextechnology.com/images/supportimages/supportarticles/firmware/", "Lorex", False),
    ("downloads.rhinoco.com.au/dvr/", "Rhino", True),
    ("gogss.com/wp-content/uploads/", "GSS", False),
    # DH Vision's old site; the live one is a Shopify store without firmware
    ("dh-vision.com/main/firmware/", "DH Vision", False, {
        "exclude": ["main/Uniview/", "main/tools/"],
        # CONFIG..._Logo_change files only replace the boot logo
        "drop": r"logo_?change",
        "name_prefix": "DHVision",
    }),
    # Intelbras' file host also serves routers, ONTs and phones, so only keep CCTV files
    ("backend.intelbras.com/sites/default/files/", "Intelbras", False, {
        # CCTV product lines: VIP cameras, MHDX/iMHDX/VHD analog, NVD recorders, XVR/DVR/IPC, SS face/access readers
        "include": r"(?<![a-z])(vip|i?mhdx|vhd|nvd|xvr|dvr|ipc|ivp|ss[_ -]?\d{3,4})(?![a-z])|c[aâ]mera",
        "drop": r"roteador|router|hotspot|iwr|onu|ont|wifi|wi-fi|telefone|phone|ramal|pabx|switch|access.?point",
        "name_prefix": "Intelbras",
    }),
]

# archive.org throttles heavy users, so keep downloads from it to a couple at a time
download_slots = threading.Semaphore(2)


def query_cdx(prefix, attempts=4):
    """Every successful capture of a firmware-looking URL under prefix: [(original, timestamp, length)]."""
    params = {
        "url": f"{prefix}*",
        "output": "json",
        "fl": "original,timestamp,length",
        "filter": ["statuscode:200", f"original:(?i).*\\.{FIRMWARE_EXTENSIONS}$"],
        "limit": "100000",
    }
    for attempt in range(1, attempts + 1):
        try:
            response = http.get(CDX_URL, params=params)
            if response.status_code == 200:
                rows = response.json() if response.text.strip() else []
                return [tuple(row) for row in rows[1:]]
            print(f"\tWayback CDX returned {response.status_code} for {prefix} (attempt {attempt}/{attempts})")
        except (ValueError, Exception) as err:
            print(f"\tWayback CDX failed for {prefix} (attempt {attempt}/{attempts}): {err!r}")
        # It answers 503 when queried too quickly
        time.sleep(10 * attempt)
    return []


def local_file_name(path, name_prefix=None):
    """The name to store a capture under. Dahua-style names are kept so they merge with other sources' copies."""
    parts = [part for part in path.split("/") if part]
    if not parts:
        return None
    file_name = parts[-1]
    if not name_prefix or is_dahua_firmware_name(file_name):
        return file_name
    folder = parts[-2] if len(parts) > 1 else ""
    return "_".join(part for part in (name_prefix, folder, file_name) if part).replace(" ", "_")


def source_matches(path, options):
    if any(fragment.lower() in path.lower() for fragment in options.get("exclude", [])):
        return False
    file_name = path.rsplit("/", 1)[-1]
    if options.get("include") and not re.search(options["include"], path, re.IGNORECASE):
        return False
    if options.get("drop") and re.search(options["drop"], file_name, re.IGNORECASE):
        return False
    return True


def get_known_files():
    """Files already on disk, which don't need recovering from the archive."""
    try:
        return set(os.listdir("firmware"))
    except FileNotFoundError:
        return set()


def get_firmwares():
    have = get_known_files()
    firmwares = {}

    for source in SOURCES:
        prefix, source_vendor, dahua_names_only = source[:3]
        options = source[3] if len(source) > 3 else {}
        captures = query_cdx(prefix)
        # Between queries, so the CDX server doesn't start refusing
        time.sleep(2)

        # Several captures per URL: keep the largest, since a smaller one is more likely to be cut off
        best = {}
        for original, timestamp, length in captures:
            key = original.split("://", 1)[-1].replace(":80/", "/").replace(":443/", "/").lower()
            size = int(length) if str(length).isdigit() else 0
            if key not in best or size > best[key][2]:
                best[key] = (original, timestamp, size)

        recovered = 0
        for original, timestamp, _ in best.values():
            path = unquote(urlparse(original).path)
            file_name = local_file_name(path, options.get("name_prefix"))
            if not file_name or file_name in have or file_name in firmwares:
                continue
            if dahua_names_only and not is_dahua_firmware_name(path.rsplit("/", 1)[-1]):
                continue
            if not source_matches(path, options):
                continue

            captured = f"{timestamp[0:4]}-{timestamp[4:6]}-{timestamp[6:8]}"
            # id_ serves the original bytes rather than the Wayback page around them
            archive_url = f"https://web.archive.org/web/{timestamp}id_/{quote(original, safe=':/?=&%')}"
            firmware = make_firmware([], archive_url, file_name=file_name,
                                     notes=f"Recovered from the Wayback Machine (captured {captured})")
            firmware["vendor"] = source_vendor
            firmware["source"] = f"Wayback Machine ({urlparse(original).hostname})"
            firmware["original_url"] = original
            firmware["archived_at"] = captured
            firmwares[file_name] = firmware
            recovered += 1

        print(f"\tWayback: {len(best)} archived firmware URLs under {prefix}, {recovered} not on disk")

    return list(firmwares.values())


def download_file(url, part_name):
    with download_slots:
        with http.get(url, stream=True) as r:
            r.raise_for_status()
            # A capture that's really an error page rather than the firmware
            if "text/html" in r.headers.get("Content-Type", ""):
                raise http_error(url, "Wayback returned an HTML page instead of the file")
            with open(part_name, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)


def http_error(url, message):
    from requests import HTTPError
    return HTTPError(f"{message}: {url}")
