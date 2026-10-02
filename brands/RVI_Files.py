import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote, urljoin

from brands import RVI
from util import http
from util.oem_helpers import FIRMWARE_EXTENSIONS, NOT_FIRMWARE, is_dahua_firmware_name, make_firmware, sanitize_name

name = "RVI file library"
vendor = "RVI"
kind = "vendor"

# Every RVI download is /download/api/?download-id=N, which redirects (302) to the real file under /exupload/.
# The firmware page only links some of them, so walk the ID space. Valid IDs are sparse and fall in these bands
# (mapped by sampling every 100th ID up to 120000), and new uploads get IDs above the highest one seen
API_URL = "https://rvigroup.ru/download/api/?download-id={}"
ID_RANGES = [(10000, 16000), (44000, 67000), (74000, 76200)]
NEW_ID_HEADROOM = 2000

# PC software RVI hosts next to the firmware (SmartPSS, ConfigTool, "VMS" and utility folders)
SOFTWARE = re.compile(r"smartpss|configtool|/vms/|утилит|/software/|\.exe$", re.IGNORECASE)

# id -> resolved path (or None for unused IDs), so later runs only check IDs they haven't seen
CACHE_FILE = "tmp/rvi_download_ids.json"
cache_lock = threading.Lock()


def load_cache():
    try:
        with open(CACHE_FILE) as f:
            return {int(k): v for k, v in json.load(f).items()}
    except (FileNotFoundError, ValueError):
        return {}


def save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    temp_file = f"{CACHE_FILE}.tmp"
    with open(temp_file, "w") as f:
        json.dump({str(k): v for k, v in sorted(cache.items())}, f)
    os.replace(temp_file, CACHE_FILE)


def resolve_id(download_id):
    """The path a download ID redirects to, None for an unused ID, or False if it couldn't be checked."""
    for attempt in range(3):
        try:
            response = http.get_session().head(API_URL.format(download_id), allow_redirects=False,
                                               timeout=(20, 40))
        except Exception:
            time.sleep(2 * (attempt + 1))
            continue
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location") or ""
            # requests decodes headers as latin-1, but RVI sends UTF-8 (Cyrillic folder names)
            location = location.encode("latin-1", "replace").decode("utf-8", "replace")
            return unquote(urljoin(API_URL.format(download_id), location))
        if response.status_code == 200:
            return None
        time.sleep(2 * (attempt + 1))
    return False


def get_ids_to_check(cache):
    ids = set()
    for start, end in ID_RANGES:
        ids.update(range(start, end + 1))
    highest = max((i for i, path in cache.items() if path), default=ID_RANGES[-1][1])
    ids.update(range(highest + 1, highest + NEW_ID_HEADROOM + 1))
    return sorted(ids - set(cache))


def sweep():
    cache = load_cache()
    ids = get_ids_to_check(cache)
    print(f"\tRVI: checking {len(ids)} new download IDs ({len(cache)} already known)")

    checked = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        for download_id, path in zip(ids, pool.map(resolve_id, ids)):
            # False means it failed to check; leave it out of the cache so it's tried again next run
            if path is not False:
                cache[download_id] = path
            checked += 1
            if checked % 2000 == 0:
                with cache_lock:
                    save_cache(cache)
    with cache_lock:
        save_cache(cache)
    return cache


def get_model(file_name, resolved_path):
    model = RVI.get_model(file_name, resolved_path)
    # File names like "Proshivka_dlya_RVi-2NR16240,2NR16240-P,..." list several models
    return re.split(r"[,;]", model)[0] if model else None


def get_firmwares():
    # IDs the firmware page already links are handled (with better model names) by brands/RVI.py
    listed_ids = {int(m[1]) for entry in RVI.get_link_entries()
                  for m in [re.search(r"download-id=(\d+)", entry["link"])] if m}

    firmwares = []
    for download_id, path in sorted(sweep().items()):
        if not path or download_id in listed_ids:
            continue

        file_name = path.rstrip("/").split("/")[-1]
        if not file_name.lower().endswith(FIRMWARE_EXTENSIONS):
            continue
        if RVI.OTHER_OEMS.search(path) or NOT_FIRMWARE.search(file_name) or SOFTWARE.search(path):
            continue
        if "/Dahua/" not in path and not is_dahua_firmware_name(file_name):
            continue

        model = get_model(file_name, path)
        # Same naming as brands/RVI.py, since RVI's generic file names are shared across models
        local_file_name = sanitize_name(f"RVI_{model or download_id}_{file_name}")
        firmwares.append(make_firmware(model, API_URL.format(download_id), local_file_name,
                                       notes=f"RVI download {download_id}"))

    return firmwares
