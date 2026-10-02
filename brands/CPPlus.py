import json
import os
import re
import time
from urllib.parse import urlparse

from util import http
from util.oem_helpers import make_firmware, parse_dahua_version, sanitize_name

name = "CP Plus"
vendor = "CP Plus"

BASE_URL = "https://cpplusworld.com"
API_URL = f"{BASE_URL}/Firmware/GetByFilter"
CDX_URL = "http://web.archive.org/cdx/search/cdx"
# The site's API has listed nothing for a while, so also use its archived answers and archived file URLs
SERIES_IDS = range(1, 61)

HEADER_CACHE = "tmp/cpplus_headers.json"
FIRMWARE_PATH = re.compile(r"/prodassets/firmware/([0-9a-fA-F-]{36})\.(bin|zip|img|dav|rar)$", re.IGNORECASE)

# Firmware path -> Wayback copy, for when the live file is gone
wayback_copies = {}


def query_cdx(url_pattern, extra_filters=()):
    params = {"url": url_pattern, "output": "json", "fl": "original,timestamp",
              "filter": ["statuscode:200", *extra_filters], "limit": "5000"}
    for attempt in range(1, 4):
        try:
            response = http.get(CDX_URL, params=params)
            if response.status_code == 200:
                rows = response.json()
                return [tuple(row) for row in rows[1:]]
        except Exception as err:
            print(f"\tCP Plus: Wayback CDX failed ({err!r})")
        # It refuses rapid queries (429) and is sometimes down for maintenance
        time.sleep(30 * attempt)
    return []


def parse_api_entries(data):
    """Entries from a GetByFilter answer: {"status": true, "result": [{modelnumber, version, builddate,
    firmwarefilepath, releasenotepath}, ...]}."""
    if not isinstance(data, dict) or not data.get("status"):
        return []
    entries = []
    for item in data.get("result") or []:
        path = item.get("firmwarefilepath") or ""
        if FIRMWARE_PATH.search(path):
            entries.append(item)
    return entries


def get_live_entries():
    entries = []
    for series_id in SERIES_IDS:
        try:
            response = http.get(API_URL, params={"seriesid": series_id, "productid": ""})
            entries.extend(parse_api_entries(response.json()))
        except Exception:
            continue
    return entries


def get_archived_entries():
    entries = []
    for original, timestamp in query_cdx("cpplusworld.com/Firmware/GetByFilter*"):
        try:
            response = http.get(f"https://web.archive.org/web/{timestamp}id_/{original}")
            entries.extend(parse_api_entries(response.json()))
        except Exception:
            continue
    return entries


def get_archived_files():
    """Firmware files the Wayback Machine has captured: {path: wayback url}."""
    files = {}
    for original, timestamp in query_cdx("cpplusworld.com/prodassets/firmware/*"):
        path = urlparse(original).path
        if FIRMWARE_PATH.search(path):
            files[path] = f"https://web.archive.org/web/{timestamp}id_/{original}"
    return files


def load_header_cache():
    try:
        with open(HEADER_CACHE) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def save_header_cache(cache):
    os.makedirs(os.path.dirname(HEADER_CACHE), exist_ok=True)
    with open(HEADER_CACHE, "w") as f:
        json.dump(cache, f, indent=1, sort_keys=True)


def read_header(url):
    try:
        response = http.get(url, stream=True, headers={"Range": "bytes=0-1", "Accept-Encoding": "identity"})
        content = response.raw.read(2) if response.status_code in (200, 206) else b""
        response.close()
        return content
    except Exception:
        return None


def get_platform_header(path, cache):
    """'DH' for Dahua firmware, 'other' for anything else, None if it couldn't be checked (rechecked next run).
    CP Plus also sells Uniview and other makers' products under the same firmware page."""
    if cache.get(path) in ("DH", "other"):
        return cache[path]
    header = read_header(BASE_URL + path)
    if not header and path in wayback_copies:
        header = read_header(wayback_copies[path])
    if header is None or header == b"":
        return None
    cache[path] = "DH" if header[:2] == b"DH" else "other"
    return cache[path]


def get_firmwares():
    entries_by_path = {}
    for entry in get_live_entries() + get_archived_entries():
        entries_by_path.setdefault(FIRMWARE_PATH.search(entry["firmwarefilepath"]).group(0), entry)

    wayback_copies.update(get_archived_files())
    paths = set(entries_by_path) | set(wayback_copies)

    cache = load_header_cache()
    firmwares = []
    for path in sorted(paths):
        if get_platform_header(path, cache) != "DH":
            continue

        uuid, extension = FIRMWARE_PATH.search(path).groups()
        entry = entries_by_path.get(path, {})
        model = (entry.get("modelnumber") or "").strip()
        version = (entry.get("version") or "").strip()
        # The files are named by UUID only, so give them a readable, unique name
        if model and version:
            file_name = f"CPPlus_{sanitize_name(model)}_{sanitize_name(version)}_{uuid}.{extension.lower()}"
        else:
            file_name = f"CPPlus_{uuid}.{extension.lower()}"

        release_date = None
        # e.g. "3/2/23 12:00:00 AM"
        match = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})\b", entry.get("builddate") or "")
        if match:
            month, day, year = match.groups()
            year = f"20{year}" if len(year) == 2 else year
            release_date = f"{year}-{int(month):02d}-{int(day):02d}"
        parsed_version, parsed_date = parse_dahua_version(version)

        series = entry.get("seriesname")
        firmware = make_firmware([model] if model else [], BASE_URL + path, file_name=file_name,
                                 notes=f"Series: {series}" if series else None,
                                 version=parsed_version or version or None,
                                 release_date=release_date or parsed_date)
        if entry.get("releasenotepath"):
            firmware["firmware_changelog"] = BASE_URL + entry["releasenotepath"]
        firmwares.append(firmware)

    save_header_cache(cache)
    return firmwares


def download_file(url, part_name):
    """Download from cpplusworld.com, falling back to the Wayback Machine's copy if the live file is gone."""
    from requests import HTTPError
    path = urlparse(url).path
    try:
        with http.get(url, stream=True) as r:
            r.raise_for_status()
            if "text/html" in r.headers.get("Content-Type", ""):
                raise HTTPError(f"got an HTML page instead of {url}")
            with open(part_name, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)
        return
    except HTTPError:
        if path not in wayback_copies:
            raise
    with http.get(wayback_copies[path], stream=True) as r:
        r.raise_for_status()
        with open(part_name, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
