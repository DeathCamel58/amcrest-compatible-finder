import json
import os
import re
from concurrent.futures import ThreadPoolExecutor

from util import http
from util.oem_helpers import (FIRMWARE_EXTENSIONS, NOT_FIRMWARE, get_content_disposition_name, is_dahua_firmware_name,
                              make_firmware, sanitize_name)

name = "Rhino file library"
vendor = "Rhino"
kind = "vendor"

# Rhino (and VIP Vision, which shares it) serve every download as /file/display/<id>, and IDs are sequential,
# so the whole library can be found by checking each ID. The real file name is in Content-Disposition
file_url = "https://www.rhinoco.com.au/file/display/{}"

# Results of earlier sweeps: {"files": {id: file name or null for missing}, "failed": [ids]}
cache_path = "tmp/rhino_file_ids.json"

# IDs the Wayback Machine had seen when this was written; the sweep goes past this to find newer ones
KNOWN_MAX_ID = 8874
SWEEP_PAST_MAX = 200

FIRMWARE_FILE_EXTENSIONS = FIRMWARE_EXTENSIONS + (".dav",)


def prune_tail(files):
    """Drop cached misses above the highest ID that has a file. New uploads get IDs up there, so those IDs have to
    be checked again on every run rather than remembered as missing."""
    highest = max((file_id for file_id, file_name in files.items() if file_name), default=None)
    if highest is None:
        return files
    return {file_id: file_name for file_id, file_name in files.items() if file_name or file_id <= highest}


def load_cache():
    try:
        with open(cache_path) as f:
            cache = json.load(f)
        return prune_tail({int(k): v for k, v in cache.get("files", {}).items()}), set(cache.get("failed", []))
    except (FileNotFoundError, ValueError):
        return {}, set()


def save_cache(files, failed):
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    temp_path = f"{cache_path}.tmp"
    with open(temp_path, "w") as f:
        json.dump({"files": {str(k): v for k, v in sorted(prune_tail(files).items())}, "failed": sorted(failed)}, f)
    os.replace(temp_path, cache_path)


def check_id(file_id):
    """Returns (file_id, file name or None if there's no file, ok), where ok is False if it couldn't be checked."""
    try:
        response = http.get_session().head(file_url.format(file_id), timeout=http.TIMEOUT, allow_redirects=True)
    except Exception:
        return file_id, None, False
    if response.status_code == 404:
        return file_id, None, True
    if response.status_code != 200:
        return file_id, None, False
    return file_id, get_content_disposition_name(response), True


def sweep_ids():
    """Every {id: file name} in the library, only checking IDs that a previous sweep hasn't settled."""
    files, failed = load_cache()
    highest_seen = max([KNOWN_MAX_ID] + [file_id for file_id, file_name in files.items() if file_name])
    to_check = sorted(failed | {i for i in range(1, highest_seen + SWEEP_PAST_MAX + 1) if i not in files})
    print(f"\tRhino file library: checking {len(to_check)} IDs ({len(files)} cached)")

    failed = set()
    with ThreadPoolExecutor(max_workers=8) as pool:
        for done, (file_id, file_name, ok) in enumerate(pool.map(check_id, to_check), 1):
            if ok:
                files[file_id] = file_name
            else:
                failed.add(file_id)
            # Save every so often so an interrupted sweep keeps its progress
            if done % 500 == 0:
                save_cache(files, failed)
    save_cache(files, failed)

    if failed:
        print(f"\tRhino file library: {len(failed)} IDs couldn't be checked; they'll be retried next run")
    return {file_id: file_name for file_id, file_name in files.items() if file_name}


def is_firmware(file_name):
    lower = file_name.lower()
    if not lower.endswith(FIRMWARE_FILE_EXTENSIONS) or NOT_FIRMWARE.search(file_name):
        return False
    # Dahua style names, Rhino / VIP Vision's descriptive names ("NVR32ULTNPV2, ... Firmware - 2024-10-18.BIN"),
    # or a raw .bin, which in this library is always a device image ("VSIP2MPPTZIR - v2.8 2019 08 27.bin"); zips
    # without either are artwork, price lists and tools
    return is_dahua_firmware_name(file_name) or "firmware" in lower or ".bin" in lower


def parse_models(file_name):
    # "NVR32ULTNPV2, NVR64ULTNPV2 Firmware - 2024-10-18.BIN" -> ["NVR32ULTNPV2", "NVR64ULTNPV2"]
    # "VSIP8MPXXIRMD- v2.622 - 2019-01-09.bin" -> ["VSIP8MPXXIRMD"]
    models_text = re.split(r"\s+firmware\b|\s*-?\s+v\d", file_name, flags=re.IGNORECASE)[0].rstrip(" -")
    if models_text == file_name or "_" in models_text:
        return []
    return [model.strip() for model in re.split(r"\s*(?:,|&|/|\band\b)\s*", models_text) if model.strip()]


def get_vip_vision_ids():
    """IDs VIP Vision's own download page links to. Its module lists those with better model data, so they're
    skipped here instead of being stored twice."""
    from brands import VIPVision
    try:
        links = VIPVision.get_firmware_links()
    except Exception as err:
        print(f"\tCouldn't read VIP Vision's links to skip them: {err!r}")
        return set()
    ids = set()
    for href in links:
        match = re.search(r"/file/display/(\d+)", href)
        if match:
            ids.add(int(match[1]))
    return ids


def get_firmwares():
    vip_vision_ids = get_vip_vision_ids()
    firmwares = []
    for file_id, file_name in sorted(sweep_ids().items()):
        if file_id in vip_vision_ids or not is_firmware(file_name):
            continue

        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", file_name)
        stored_name = file_name if is_dahua_firmware_name(file_name) else sanitize_name(f"RhinoFiles_{file_id}_{file_name}")
        firmware = make_firmware(parse_models(file_name), file_url.format(file_id), stored_name,
                                 release_date=date_match[1] if date_match else None)
        firmwares.append(firmware)

    return firmwares
