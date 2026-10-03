import copy
from contextlib import contextmanager
import hashlib
import os.path
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import re
import sys
import tempfile
import time
import shutil
from datetime import date, datetime, timezone
from urllib.parse import unquote, urlparse

from brands import Amcrest
from brands import Dahua
from brands import Dahua_FileDirectory
from brands import GSSRedLINE_Camera
from brands import GSSRedLINE_NVR
from brands import GSSBlueLINE_Camera
from brands import GSSBlueLINE_NVR
from brands import GSSRedLINE_DVR
from brands import GSSBlueLINE_DVR
from brands import Lorex
from brands import DahuaOfficial
from brands import ENS_Diamond
from brands import Inaxsys_STORM
from brands import EmpireTech
from brands import VIPVision
from brands import Rhino
from brands import Montavue
from brands import RVI
from brands import Speco
from brands import Optiview
from brands import ASM
from brands import DahuaTechSupport
from brands import RhinoFiles
from brands import RVI_Files
from brands import ICRealtime
from brands import SecurityTronix
from brands import Bosch_DIVAR
from brands import Zuum
from brands import Winic
from brands import Intelbras
from brands import CPPlus
from brands import KBVision
from brands import Wayback
from util.scheduler import HostScheduler
from util.archive import archive_firmware, needs_refresh, refresh_archive
from util.download_firmware import download_firmware
from util.file_integrity import check_integrity
from util.firmware_platform import detect_platform
from util import http
from util.general import merge_unique_text, normalize_firmware_url
from util.file_type import classify_file, classify_file_content, classify_file_type
from util.hardware import split_model_names
from util.oem_helpers import parse_dahua_version
from util.file_identity import assign_duplicates, get_file_hashes, needs_hashing
from util.firmware_processing import firmware_processing_lock, mark_not_dahua, needs_processing, process_firmware_threaded
from util.json_tools import get_cameras_json, save_cameras_json, get_firmware_json, save_firmware_json

oem_modules = [
    Amcrest,
    Dahua,
    Dahua_FileDirectory,
    GSSRedLINE_Camera,
    GSSRedLINE_NVR,
    GSSBlueLINE_Camera,
    GSSBlueLINE_NVR,
    GSSRedLINE_DVR,
    GSSBlueLINE_DVR,
    Lorex,
    DahuaOfficial,
    ENS_Diamond,
    Inaxsys_STORM,
    EmpireTech,
    VIPVision,
    Rhino,
    Montavue,
    RVI,
    Speco,
    Optiview,
    ASM,
    DahuaTechSupport,
    RhinoFiles,
    RVI_Files,
    ICRealtime,
    SecurityTronix,
    Bosch_DIVAR,
    Zuum,
    Winic,
    Intelbras,
    CPPlus,
    KBVision,
    # Last, so it only recovers what no live source still has
    Wayback,
]

# TODO: Support additional Dahua OEMs
#       https://securitycamcenter.com/dahua-oem-list/



camera_json_lock = threading.Lock()

# Software, OS images and documents some sources list next to firmware are moved here (not deleted), and kept out of
# the JSONs, which only describe firmware
NON_FIRMWARE_DIR = os.path.join(os.path.dirname(os.path.realpath('firmware')), 'firmware-non-firmware')

# Truncated firmwares that were replaced by a complete download are kept here rather than deleted
TRUNCATED_DIR = os.path.join(os.path.dirname(os.path.realpath('firmware')), 'firmware-truncated')

# Leaves room for the ".part" / ".redownload" suffixes under the usual 255-byte limit
MAX_FILE_NAME_BYTES = 200

# Used for listing first_seen / last_seen, so every listing seen in one run gets the same date
RUN_DATE = date.today().isoformat()


# Optional per-firmware metadata that brand modules can provide. These describe firmware_latest only,
# since vendors that list a previous firmware don't give its details
LATEST_METADATA_FIELDS = ['firmware_version', 'release_date', 'md5', 'sha256']


def split_version_build_date(firmware):
    # Dahua versions end in the build date ("V2.800.0000032.0.R.250224"), but devices show the version
    # ("V2.800.0000032.0.R") and build date separately, so store them that way for every vendor
    version = firmware.get('firmware_version')
    match = re.match(r'^(V?\d+\.\d+\.[0-9A-Za-z]+\.\d+\.[A-Z])\.(\d{6}|\d{8})$', version.strip()) if isinstance(version, str) else None
    if not match:
        return

    firmware['firmware_version'], date = match.groups()
    if not firmware.get('release_date'):
        date = f"20{date}" if len(date) == 6 else date
        firmware['release_date'] = f"{date[0:4]}-{date[4:6]}-{date[6:8]}"


def get_firmware_file_name(firmware, firmware_type):
    # Some download URLs don't end in the file name (Google Drive, redirect APIs, ...), so modules can set it explicitly
    name = firmware.get(f'{firmware_type}_file_name') or firmware[firmware_type].split("/")[-1].split("?")[0]
    # Decode %28-style escapes, so the same file isn't stored under both its encoded and decoded name
    name = unquote(name).replace("/", "_")
    # File names are limited to 255 bytes; some (RVI's model lists in Cyrillic) are longer. Shorten them, keeping the
    # end (version and extension) and a hash of the full name so they stay unique
    if len(name.encode()) > MAX_FILE_NAME_BYTES:
        stem, extension = os.path.splitext(name)
        # A "name.<long tail>" with the only dot near the start has no real extension; without this the budget goes
        # negative and the loop below never ends
        if len(extension.encode()) > 16:
            stem, extension = name, ''
        digest = hashlib.sha1(name.encode()).hexdigest()[:8]
        budget = MAX_FILE_NAME_BYTES - len(f'-{digest}{extension}'.encode())
        while len(stem.encode()) > budget:
            stem = stem[:len(stem) // 2] + stem[len(stem) // 2 + 1:]
        name = f'{stem}-{digest}{extension}'
    return name


def merge_listing(listings, firmware, firmware_type, new_camera_names, firmware_file):
    """Keep what each vendor page says about this file, since the top-level fields combine every vendor.

    One listing per vendor page (source); a page that lists the same file in several rows gets merged."""
    listings = [dict(listing) for listing in listings]
    listing = next((l for l in listings if l.get('vendor') == firmware['vendor'] and l.get('source') == firmware['source']), None)
    if listing is None:
        listing = {'vendor': firmware['vendor'], 'source': firmware['source']}
        listings.append(listing)

    listing['url'] = firmware[firmware_type]
    models, series, descriptions = split_model_names(
        merge_unique_text((listing.get('camera_name') or []) + (listing.get('series') or []) + new_camera_names),
        firmware_file)
    listing['camera_name'] = models
    if series:
        listing['series'] = series
    listing['notes'] = merge_unique_text((listing.get('notes') or []) + [firmware['firmware_notes']] + descriptions)
    if firmware.get('firmware_changelog'):
        listing['changelog'] = firmware['firmware_changelog']
    if firmware_type == 'firmware_latest':
        for field in ['firmware_version', 'release_date']:
            if firmware.get(field):
                listing[field] = firmware[field].strip() if isinstance(firmware[field], str) else firmware[field]

    # Previous-firmware listings (and some vendors) give no version or date, but Dahua file names contain both
    if not listing.get('firmware_version'):
        version, release_date = parse_dahua_version(firmware_file)
        if version:
            listing['firmware_version'] = version
            listing.setdefault('release_date', release_date)

    # Freshness: when this page first and last listed the file, and whether it's that page's current firmware
    listing.setdefault('first_seen', RUN_DATE)
    listing['last_seen'] = RUN_DATE
    # Where a copy was archived from (Wayback Machine captures), and when
    for field in ('original_url', 'archived_at'):
        if firmware.get(field):
            listing[field] = firmware[field]

    # Mirrors (distributors' file servers) and archives just hold files; they don't say which firmware is current
    listing['kind'] = firmware.get('source_kind', 'vendor')
    if listing['kind'] != 'vendor':
        listing.pop('latest', None)
        listing.pop('last_seen_latest', None)
    else:
        if firmware_type == 'firmware_latest':
            listing['last_seen_latest'] = RUN_DATE
        listing['latest'] = listing.get('last_seen_latest') == listing['last_seen']

    return listings


def get_expected_size(firmware, firmware_type):
    """The size of what this listing would download, if it can be found cheaply, or None."""
    size = firmware.get('firmware_size') if firmware_type == 'firmware_latest' else None
    if isinstance(size, int) or (isinstance(size, str) and size.isdigit()):
        return int(size)
    if firmware.get('downloader'):
        # Special hosts (MEGA, Drive, SharePoint) only give sizes through their module
        return None
    try:
        # Without identity encoding some servers send a compressed body and no usable Content-Length
        response = http.get(firmware[firmware_type], stream=True, headers={'Accept-Encoding': 'identity'})
        length = response.headers.get('Content-Length')
        response.close()
        if response.status_code == 200 and length and length.isdigit():
            return int(length)
    except Exception as err:
        print(f'\tCould not check the size of {firmware[firmware_type]}: {err!r}')
    return None


def resolve_file_name(firmware, firmware_type):
    """The file name to store this listing's firmware under.

    A file with the same name from a different source might be a different firmware. If its size differs, store it
    under the name plus a short hash instead of treating it as the file we already have."""
    name = get_firmware_file_name(firmware, firmware_type)
    path = f'firmware/{name}'
    if not os.path.exists(path):
        return name

    entry = get_cameras_json().get(name, {})
    known_urls = {entry.get('url')} | {listing.get('url') for listing in entry.get('listings') or []}
    if firmware[firmware_type] in known_urls:
        return name

    expected_size = get_expected_size(firmware, firmware_type)
    if expected_size is None or expected_size == os.path.getsize(path):
        return name

    stem, extension = os.path.splitext(name)
    renamed = f'{stem}-{hashlib.sha1(firmware[firmware_type].encode()).hexdigest()[:8]}{extension}'
    print(f'\t{name} already exists with a different size ({os.path.getsize(path)} vs {expected_size}); '
          f'storing {firmware[firmware_type]} as {renamed}')
    return renamed


def replace_truncated_file(firmware, firmware_type, file_name):
    """If the file on disk is truncated and this source serves a different copy, download it and keep it if it's
    complete. The truncated copy is moved to TRUNCATED_DIR, never deleted."""
    name = file_name[len('firmware/'):]
    integrity = get_cameras_json().get(name, {}).get('integrity') or {}
    if integrity.get('status') != 'truncated':
        return False

    expected_size = get_expected_size(firmware, firmware_type)
    if expected_size == os.path.getsize(file_name):
        # The vendor serves the same cut-off file, so downloading it again won't help
        with camera_json_lock:
            cameras_json = get_cameras_json()
            cameras_json[name].setdefault('integrity', {})['vendor_copy_truncated'] = True
            save_cameras_json(cameras_json)
        return False

    candidate = f'{file_name}.redownload'
    if download_firmware(firmware[firmware_type], candidate, firmware.get('downloader')) is None:
        return False
    if check_integrity(candidate)['status'] != 'ok':
        print(f'\tRe-downloaded {name}, but it is still incomplete; keeping the existing copy')
        os.remove(candidate)
        return False

    os.makedirs(TRUNCATED_DIR, exist_ok=True)
    os.replace(file_name, os.path.join(TRUNCATED_DIR, name))
    os.replace(candidate, file_name)
    print(f'\tReplaced truncated {name} with a complete copy (the old one is in {TRUNCATED_DIR})')
    return True


def is_valid_date(value):
    try:
        datetime.strptime(value, '%Y-%m-%d')
        return True
    except (TypeError, ValueError):
        return False


def tidy_entry(firmware_file, entry, inspect_content=False):
    """Normalise a cameras.json entry in place: model names vs series vs descriptions, listing kinds, the earliest
    release date, uninformative versions, and what kind of file it is."""
    def split(names_holder):
        models, series, descriptions = split_model_names(
            (names_holder.get('camera_name') or []) + (names_holder.get('series') or []), firmware_file)
        names_holder['camera_name'] = models
        if series:
            names_holder['series'] = series
        else:
            names_holder.pop('series', None)
        if descriptions:
            names_holder['notes'] = merge_unique_text((names_holder.get('notes') or []) + descriptions)

    split(entry)
    for listing in entry.get('listings') or []:
        split(listing)
        # Listings stored before kinds existed are all from vendors' own pages
        listing.setdefault('kind', 'vendor')

    # Drop malformed values older versions stored (placeholder checksums like "0", 7-digit "dates")
    for field, pattern in (('md5', r'[0-9a-fA-F]{32}'), ('sha256', r'[0-9a-fA-F]{64}')):
        if entry.get(field) and not re.fullmatch(pattern, entry[field]):
            entry.pop(field)
    for holder in [entry] + list(entry.get('listings') or []):
        if holder.get('release_date') and not is_valid_date(holder['release_date']):
            holder.pop('release_date')

    # The top-level date is the earliest any vendor gives, i.e. when the firmware first appeared
    dates = [listing['release_date'] for listing in entry.get('listings') or [] if listing.get('release_date')]
    if dates:
        entry['release_date'] = min(dates)

    # A "version" that only repeats the file name (GSS, Montavue) says nothing extra
    stem = os.path.splitext(firmware_file)[0].casefold()
    if (entry.get('firmware_version') or '').casefold() == stem:
        entry.pop('firmware_version')
    for listing in entry.get('listings') or []:
        if (listing.get('firmware_version') or '').casefold() == stem:
            listing.pop('firmware_version')

    # PC software, OS images and manuals some sources file next to firmware, so the site can leave them out.
    # enrich looks inside the file (its header and zip contents); otherwise the name decides
    path = f'firmware/{firmware_file}'
    if inspect_content and os.path.exists(path):
        entry['file_type'] = classify_file(path, firmware_file, entry.get('url'))
    elif not entry.get('file_type'):
        entry['file_type'] = classify_file_type(firmware_file, entry.get('url'))


def download_firmware_thread(firmware, firmware_type):
    listing_only = bool(firmware.get('listing_only'))
    if listing_only:
        # Known but not downloadable (e.g. behind a login): record the listing, don't try to fetch it
        file_name = f'firmware/{get_firmware_file_name(firmware, firmware_type)}'
        replaced = False
    else:
        file_name = f'firmware/{resolve_file_name(firmware, firmware_type)}'
        replaced = os.path.exists(file_name) and replace_truncated_file(firmware, firmware_type, file_name)

    downloaded = False
    if not listing_only and not os.path.exists(file_name):
        # file_name is None
        new_file_name = download_firmware(firmware[firmware_type], file_name, firmware.get('downloader'))

        if new_file_name is not None:
            downloaded = True
            file_name = new_file_name
            # Some files only show they aren't firmware once downloaded (e.g. a zip of installers or release notes)
            if classify_file_content(file_name) in ('software', 'document'):
                print(f'\t{file_name[9:]} isn\'t firmware; moving it to {NON_FIRMWARE_DIR}')
                move_out_of_firmware(file_name[9:])
                return

    # TODO: Add a lock here since we're reading, modifying, then writing back a file

    with camera_json_lock:
        cameras_json = get_cameras_json()
        cameras_json_original = copy.deepcopy(cameras_json)
        firmware_json_name = file_name[9:]

        existing = cameras_json.get(firmware_json_name, {})

        # Merge what's already stored with what this listing provides
        new_camera_names = firmware['camera_name'] if isinstance(firmware['camera_name'], list) else [firmware['camera_name']]
        camera_names, series, descriptions = split_model_names(
            merge_unique_text((existing.get('camera_name') or []) + (existing.get('series') or []) + new_camera_names),
            firmware_json_name)
        firmware_notes = merge_unique_text((existing.get('notes') or []) + [firmware['firmware_notes']] + descriptions)

        vendors = list(existing.get('vendors') or [])
        if firmware['vendor'] not in vendors:
            vendors.append(firmware['vendor'])

        if firmware_type == 'firmware_latest':
            split_version_build_date(firmware)

        firmware_data = {
            'camera_name': camera_names,
            'url': firmware[firmware_type],
            'notes': firmware_notes,
            'vendors': vendors,
            'listings': merge_listing(existing.get('listings') or [], firmware, firmware_type, new_camera_names,
                                      firmware_json_name),
        }
        if series:
            firmware_data['series'] = series

        if os.path.exists(file_name):
            firmware_data['firmware_size'] = os.stat(file_name).st_size
            # Hash a fresh download now, while it's still in memory, so enrich doesn't re-read it from disk
            if downloaded or replaced:
                firmware_data['file_hashes'] = get_file_hashes(file_name)
            # dahua / hikvision / unknown, from the file itself (some OEMs sell both, e.g. GSS Red|LINE is Hikvision)
            if not existing.get('platform') or downloaded or replaced:
                firmware_data['platform'] = detect_platform(file_name)

        if firmware.get('firmware_changelog'):
            firmware_data['changelog'] = firmware['firmware_changelog']

        if firmware_type == 'firmware_latest':
            for field in LATEST_METADATA_FIELDS:
                if firmware.get(field):
                    firmware_data[field] = firmware[field].strip() if isinstance(firmware[field], str) else firmware[field]

        if not existing.get('firmware_version') and not firmware_data.get('firmware_version'):
            version, release_date = parse_dahua_version(firmware_json_name)
            if version:
                firmware_data['firmware_version'] = version
                if not existing.get('release_date'):
                    firmware_data['release_date'] = release_date

        if listing_only and not os.path.exists(file_name):
            firmware_data['downloadable'] = False
            firmware_data['listing_only_reason'] = firmware.get('listing_only_reason')

        if firmware_json_name in cameras_json:
            cameras_json[firmware_json_name].update(firmware_data)
        else:
            cameras_json[firmware_json_name] = firmware_data

        tidy_entry(firmware_json_name, cameras_json[firmware_json_name])

        # Another source had the file after all
        if os.path.exists(file_name):
            cameras_json[firmware_json_name].pop('downloadable', None)
            cameras_json[firmware_json_name].pop('listing_only_reason', None)

        if cameras_json_original != cameras_json:
            save_cameras_json(cameras_json)


# Download all firmwares
# Vendor sites are listed this many at a time
LISTING_WORKERS = 6
# Downloads run this many at a time in total, spread across servers (see HostScheduler)
DOWNLOAD_WORKERS = 16
# Firmwares are unpacked this many at a time (binwalk is CPU-bound); limited further by free space in the temp dir
PROCESS_WORKERS = max(1, min(12, (os.cpu_count() or 2) // 2))
# Unpacking a firmware takes up to roughly this many times its size in temp space, plus a reserve that's always kept free
UNPACK_SPACE_FACTOR = 8
TEMP_SPACE_RESERVE = 10 * 1024 ** 3
# Servers that throttle heavy users get at most this many downloads at once
HOST_CAPS = {
    'web.archive.org': 2,
    'mega.nz': 2,
    'drive.google.com': 2,
    'drive.usercontent.google.com': 2,
    'www.dropbox.com': 2,
}


# Set from --only / --skip: names of modules (e.g. "EmpireTech", "Wayback Machine") or vendors to include or leave out
ONLY_MODULES = set()
SKIP_MODULES = set()


def selected_modules():
    def matches(module, names):
        return bool({module.name.casefold(), module.vendor.casefold(), module.__name__.split('.')[-1].casefold()} & names)
    modules = [m for m in oem_modules if not ONLY_MODULES or matches(m, ONLY_MODULES)]
    return [m for m in modules if not matches(m, SKIP_MODULES)]


def list_vendor(oem):
    """One vendor module's firmwares, prepared for downloading."""
    # Don't let one broken vendor site stop the rest
    try:
        oem_firmwares = oem.get_firmwares()
    except Exception as err:
        print(f'Failed to get {oem.name} firmwares: {err!r}')
        return []
    print(f'Got a list of {len(oem_firmwares)} {oem.name} firmwares!')

    for oem_firmware in oem_firmwares:
        # Modules that collect other vendors' files (the Wayback Machine) set these per firmware
        oem_firmware['vendor'] = oem_firmware.get('vendor') or oem.vendor
        oem_firmware['source'] = oem_firmware.get('source') or oem.name
        oem_firmware['source_kind'] = getattr(oem, 'kind', 'vendor')
        oem_firmware['module'] = oem.name
        # Modules (or single firmwares) whose files can't be downloaded, e.g. links that need a login
        oem_firmware['listing_only'] = oem_firmware.get('listing_only') or getattr(oem, 'listing_only', False)
        oem_firmware['listing_only_reason'] = (oem_firmware.get('listing_only_reason')
                                               or getattr(oem, 'listing_only_reason', None))
        # Modules for hosts that need special handling (Google Drive, MEGA, ...) provide their own downloader
        oem_firmware['downloader'] = getattr(oem, 'download_file', None)
        for firmware_type in ["firmware_previous", "firmware_latest"]:
            oem_firmware[firmware_type] = normalize_firmware_url(oem_firmware[firmware_type])
    return oem_firmwares


def print_download_summary(tasks):
    on_disk = set(list_firmware_files())
    names = {}
    for firmware, firmware_type in tasks:
        names.setdefault(get_firmware_file_name(firmware, firmware_type), []).append((firmware, firmware_type))
    listing_only = {name for name, listings in names.items()
                    if all(firmware.get('listing_only') for firmware, _ in listings)}
    missing = {name: listings for name, listings in names.items() if name not in on_disk and name not in listing_only}

    per_module = {}
    hosts = set()
    for name, listings in missing.items():
        for module in {firmware['module'] for firmware, _ in listings}:
            per_module[module] = per_module.get(module, 0) + 1
        hosts.update(urlparse(firmware[firmware_type]).hostname for firmware, firmware_type in listings)

    print(f'Found {len(names)} firmwares')
    print(f'{len(names) - len(missing) - len(listing_only)}/{len(names)} firmwares already downloaded'
          + (f' ({len(listing_only)} more are listed but not downloadable)' if listing_only else ''))
    print()
    print(f'Downloads per server ({len(missing)} firmwares from {len(hosts)} servers; '
          f'a firmware several sources list is counted for each):')
    for module, count in sorted(per_module.items(), key=lambda item: -item[1]):
        print(f'    {module}: {count}')
    print()


def get_all_firmwares():
    with ThreadPoolExecutor(max_workers=LISTING_WORKERS) as pool:
        firmwares = [firmware for listed in pool.map(list_vendor, selected_modules()) for firmware in listed]

    if len(firmwares) == 0:
        return

    # Archived copies (Wayback Machine) are only for files no live source has anymore
    live_names = {get_firmware_file_name(f, t) for f in firmwares if f.get('source_kind') != 'archive'
                  for t in ('firmware_previous', 'firmware_latest') if f.get(t)}
    archived = [f for f in firmwares if f.get('source_kind') == 'archive']
    firmwares = [f for f in firmwares if f.get('source_kind') != 'archive'
                 or get_firmware_file_name(f, 'firmware_latest') not in live_names]
    recovering = sum(1 for f in firmwares if f.get('source_kind') == 'archive')
    print(f'Recovering {recovering} of {len(archived)} archived firmwares (the rest are still available live)')

    tasks = [(firmware, firmware_type) for firmware in firmwares
             for firmware_type in ["firmware_previous", "firmware_latest"] if firmware[firmware_type]]

    # PC software, OS images and documents some sources list next to firmware aren't downloaded at all
    skipped = {}
    firmware_tasks = []
    for firmware, firmware_type in tasks:
        file_type = classify_file_type(get_firmware_file_name(firmware, firmware_type), firmware[firmware_type])
        if file_type == 'firmware':
            firmware_tasks.append((firmware, firmware_type))
        else:
            skipped[file_type] = skipped.get(file_type, 0) + 1
    if skipped:
        print(f'Skipping {sum(skipped.values())} listings that aren\'t firmware: '
              + ', '.join(f'{count} {file_type}' for file_type, count in sorted(skipped.items())))
    tasks = firmware_tasks
    print_download_summary(tasks)

    # Spread downloads across servers rather than working through one vendor's list at a time
    scheduler = HostScheduler(DOWNLOAD_WORKERS, HOST_CAPS)
    for firmware, firmware_type in tasks:
        scheduler.add(urlparse(firmware[firmware_type]).hostname or '', download_firmware_thread, firmware, firmware_type)
    scheduler.run()


def list_firmware_files():
    return sorted(f for f in os.listdir('firmware')
                  if not f.endswith(('.part', '.redownload')) and os.path.isfile(f'firmware/{f}'))


def move_out_of_firmware(firmware_file):
    """Move a file that turned out not to be firmware to NON_FIRMWARE_DIR, without overwriting anything there."""
    os.makedirs(NON_FIRMWARE_DIR, exist_ok=True)
    destination = os.path.join(NON_FIRMWARE_DIR, firmware_file)
    if os.path.exists(destination):
        stem, extension = os.path.splitext(firmware_file)
        destination = os.path.join(NON_FIRMWARE_DIR, f'{stem}-{int(time.time())}{extension}')
    os.replace(f'firmware/{firmware_file}', destination)


def remove_non_firmware():
    """Keep the JSONs to firmware only: move software and documents out of firmware/ and drop their entries."""
    removed = {}
    with camera_json_lock:
        cameras_json = get_cameras_json()
        on_disk = set(list_firmware_files())
        for firmware_file in sorted(on_disk | set(cameras_json)):
            url = cameras_json.get(firmware_file, {}).get('url')
            if firmware_file in on_disk:
                file_type = classify_file(f'firmware/{firmware_file}', firmware_file, url)
            else:
                file_type = classify_file_type(firmware_file, url)
            if file_type == 'firmware':
                continue
            if firmware_file in on_disk:
                move_out_of_firmware(firmware_file)
            cameras_json.pop(firmware_file, None)
            removed[file_type] = removed.get(file_type, 0) + 1
        if removed:
            save_cameras_json(cameras_json)

    if removed:
        with firmware_processing_lock:
            firmware_json = get_firmware_json()
            for firmware_file in list(firmware_json):
                if firmware_file not in cameras_json:
                    firmware_json.pop(firmware_file)
            save_firmware_json(firmware_json)
    print('Removed non-firmware: ' + (', '.join(f'{count} {file_type}' for file_type, count in sorted(removed.items()))
                                     or 'none') + f' (moved to {NON_FIRMWARE_DIR})')


def enrich_firmwares():
    """Hash every firmware, link files with identical content, and tidy model names in existing entries."""
    # First, so non-firmware isn't hashed, deduplicated or written back
    remove_non_firmware()

    # Before picking main entries for duplicates, which prefers names a vendor lists
    infer_vendors_from_urls()

    firmware_files = list_firmware_files()
    cameras_json = get_cameras_json()
    to_hash = [f for f in firmware_files if needs_hashing(cameras_json.get(f, {}), f'firmware/{f}')]
    print(f'Hashing {len(to_hash)} of {len(firmware_files)} firmwares')

    def hash_one(firmware_file):
        return firmware_file, get_file_hashes(f'firmware/{firmware_file}')

    def save_hashes(hashes_by_file):
        with camera_json_lock:
            cameras_json = get_cameras_json()
            for firmware_file, hashes in hashes_by_file.items():
                cameras_json.setdefault(firmware_file, {})['file_hashes'] = hashes
            save_cameras_json(cameras_json)

    # Two at a time: it's one hard drive, so more parallel reads just make it seek
    pending = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        for done, (firmware_file, hashes) in enumerate(pool.map(hash_one, to_hash), 1):
            pending[firmware_file] = hashes
            # Save every so often so an interrupted run keeps its progress
            if len(pending) >= 25:
                save_hashes(pending)
                pending = {}
                print(f'\tHashed {done}/{len(to_hash)}')
    if pending:
        save_hashes(pending)

    with camera_json_lock:
        cameras_json = get_cameras_json()
        duplicates = assign_duplicates(cameras_json, firmware_files)

        truncated = 0
        for firmware_file in firmware_files:
            entry = cameras_json.setdefault(firmware_file, {})
            path = f'firmware/{firmware_file}'
            # Entries from older data may lack these
            entry['firmware_size'] = os.path.getsize(path)
            if not entry.get('platform'):
                entry['platform'] = detect_platform(path)
            # Catch downloads that were cut off (or vendors serving cut-off files) before they're analysed or archived
            integrity = check_integrity(path)
            if integrity['status'] == 'truncated' and (entry.get('integrity') or {}).get('vendor_copy_truncated'):
                integrity['vendor_copy_truncated'] = True
            entry['integrity'] = integrity
            # The file on disk doesn't match the checksum the vendor published
            vendor_md5 = (entry.get('md5') or '').lower()
            file_md5 = (entry.get('file_hashes') or {}).get('md5')
            if vendor_md5 and file_md5:
                if vendor_md5 != file_md5:
                    entry['vendor_md5_mismatch'] = True
                else:
                    entry.pop('vendor_md5_mismatch', None)
            truncated += integrity['status'] == 'truncated'

        # Apply the current clean-up rules to every entry, including ones stored by older versions
        for firmware_file, entry in cameras_json.items():
            tidy_entry(firmware_file, entry, inspect_content=True)

        save_cameras_json(cameras_json)
    print(f'Found {duplicates} firmwares that are identical to another file')
    print(f'Found {truncated} truncated firmwares')


def mirror_duplicate_results():
    """Duplicates aren't analysed themselves; they share their main entry's result."""
    cameras_json = get_cameras_json()
    with firmware_processing_lock:
        firmware_json = get_firmware_json()
        for firmware_file, entry in cameras_json.items():
            main_file = entry.get('duplicate_of')
            if main_file and main_file in firmware_json:
                firmware_json[firmware_file] = {**firmware_json[main_file], 'status': 'duplicate',
                                                'duplicate_of': main_file}
            elif not main_file and (firmware_json.get(firmware_file) or {}).get('status') == 'duplicate':
                # No longer a duplicate (e.g. one of the files changed), so analyse it on its own
                del firmware_json[firmware_file]
        save_firmware_json(firmware_json)


def process_firmware(firmware_file):
    file_path = f"firmware/{firmware_file}"

    entry = get_cameras_json().get(firmware_file, {})
    if entry.get('duplicate_of'):
        return
    # Software and documents have no hardware IDs to find
    if (entry.get('file_type') or classify_file_type(firmware_file, entry.get('url'))) != 'firmware':
        return

    previous = get_firmware_json().get(firmware_file)
    if previous and previous.get('status') == 'duplicate':
        previous = None
    platform = entry.get('platform') or detect_platform(file_path)
    integrity = entry.get('integrity') or check_integrity(file_path)
    truncated = integrity.get('status') == 'truncated'

    # A result from before the file was known to be truncated (or from before it was replaced by a complete copy)
    # doesn't describe it correctly
    known_truncated = bool(previous and previous.get('truncated'))
    if previous and platform == 'dahua' and truncated != known_truncated:
        previous = None

    if not needs_processing(previous, platform):
        return

    # Our extractor only understands Dahua firmware, so don't spend time unpacking Hikvision and others
    if platform != 'dahua':
        mark_not_dahua(firmware_file, platform)
        return

    with temp_space_for(os.path.getsize(file_path)):
        process_firmware_threaded(firmware_file, file_path, previous, integrity)


_space_condition = threading.Condition()
_space_reserved = [0]


@contextmanager
def temp_space_for(file_size):
    """Wait until the temp directory has room to unpack a file of this size alongside the others being unpacked."""
    needed = file_size * UNPACK_SPACE_FACTOR
    with _space_condition:
        while True:
            free = shutil.disk_usage(tempfile.gettempdir()).free - _space_reserved[0]
            # Always let one job run, even if it alone needs more than is free
            if free - needed >= TEMP_SPACE_RESERVE or _space_reserved[0] == 0:
                break
            _space_condition.wait(timeout=30)
        _space_reserved[0] += needed
    try:
        yield
    finally:
        with _space_condition:
            _space_reserved[0] -= needed
            _space_condition.notify_all()


def process_all_firmwares():
    # Scratch space for unpacking firmwares
    os.makedirs('tmp', exist_ok=True)

    def process_one(firmware_file):
        # Don't let one unusual firmware stop the rest from being processed
        try:
            process_firmware(firmware_file)
        except Exception as err:
            print(f'Failed to process {firmware_file}: {err!r}')

    # Largest first, so the slow ones don't all end up at the end
    firmware_files = sorted(list_firmware_files(), key=lambda f: -os.path.getsize(f'firmware/{f}'))
    print(f'Processing with {PROCESS_WORKERS} workers')
    with ThreadPoolExecutor(max_workers=PROCESS_WORKERS) as pool:
        list(pool.map(process_one, firmware_files))

    mirror_duplicate_results()


def with_platform(entry, path):
    if entry.get('platform'):
        return entry
    return {**entry, 'platform': detect_platform(path)}


def get_alias_entries(cameras_json, entry):
    return {alias: cameras_json[alias] for alias in entry.get('aliases') or [] if alias in cameras_json}


def archive_firmware_thread(firmware_file):
    path = f'firmware/{firmware_file}'
    cameras_json = get_cameras_json()
    entry = with_platform(cameras_json.get(firmware_file, {}), path)
    alias_entries = get_alias_entries(cameras_json, entry)
    analysis = get_firmware_json().get(firmware_file)

    try:
        if entry.get('archive_url') and entry.get('archive_md5'):
            # Already archived: only refresh the item's metadata and provenance record if they changed
            print(f'Updating archive metadata: {firmware_file}')
            archive_fields = {'archive_record_hash': refresh_archive(
                firmware_file, entry, analysis, alias_entries, entry['archive_md5'], os.path.getsize(path))}
        else:
            print(f'Archiving: {firmware_file}')
            archive_fields = archive_firmware(path, entry, analysis, alias_entries)
    except Exception as err:
        print(f'\tFailed to archive {firmware_file}: {err!r}')
        return

    with camera_json_lock:
        cameras_json = get_cameras_json()
        cameras_json.setdefault(firmware_file, {}).update({**archive_fields, 'platform': entry['platform']})
        save_cameras_json(cameras_json)
    print(f'\tArchived {firmware_file}: {cameras_json[firmware_file]["archive_item"]}')


def link_duplicate_archives():
    """Identical copies aren't uploaded separately; point them at their main entry's item."""
    with camera_json_lock:
        cameras_json = get_cameras_json()
        for entry in cameras_json.values():
            main_entry = cameras_json.get(entry.get('duplicate_of') or '')
            if main_entry and main_entry.get('archive_url'):
                entry['archive_item'] = main_entry['archive_item']
                entry['archive_url'] = main_entry['archive_url']
                # Not this file's own upload, so there's nothing of its own to refresh
                for field in ('archive_md5', 'archive_record_hash'):
                    entry.pop(field, None)
        save_cameras_json(cameras_json)


def archive_all_firmwares():
    # Runs after listing in a full run, so this only covers firmwares no vendor lists anymore
    infer_vendors_from_urls()

    cameras_json = get_cameras_json()
    firmware_json = get_firmware_json()

    firmware_files = []
    skipped_truncated = []
    for firmware_file in list_firmware_files():
        entry = with_platform(cameras_json.get(firmware_file, {}), f'firmware/{firmware_file}')
        if entry.get('duplicate_of'):
            continue
        # This is a firmware archive; PC software and manuals aren't uploaded
        if (entry.get('file_type') or classify_file_type(firmware_file, entry.get('url'))) != 'firmware':
            continue
        # Don't put an incomplete file on the archive. One that's already there keeps its item, which gets updated
        # to say the file is incomplete
        if (entry.get('integrity') or {}).get('status') == 'truncated' and not entry.get('archive_url'):
            skipped_truncated.append(firmware_file)
            continue
        if not entry.get('archive_url') or not entry.get('archive_md5') or needs_refresh(
                firmware_file, entry, firmware_json.get(firmware_file), get_alias_entries(cameras_json, entry),
                os.path.getsize(f'firmware/{firmware_file}')):
            firmware_files.append(firmware_file)

    # Firmwares no vendor currently lists go first, since their source is the most likely to be gone
    firmware_files.sort(key=lambda f: (bool(cameras_json.get(f, {}).get('vendors')), f))
    print(f'Archiving or updating {len(firmware_files)} firmwares on the Internet Archive '
          f'(skipping {len(skipped_truncated)} truncated ones)')

    # Keep this low; archive.org throttles bulk uploads
    with ThreadPoolExecutor(max_workers=3) as pool:
        for future in as_completed([pool.submit(archive_firmware_thread, f) for f in firmware_files]):
            future.result()

    link_duplicate_archives()


# Links on these hosts need their own clients to check (and download), so they aren't checked
UNCHECKED_LINK_HOSTS = ('mega.nz', 'drive.google.com', 'drive.usercontent.google.com', 'sharepoint.com')


def check_link(url):
    """Returns ok / dead / error / unchecked for a listing's download link, without downloading the file."""
    host = urlparse(url).hostname or ''
    if not url.startswith(('http://', 'https://')) or host.endswith(UNCHECKED_LINK_HOSTS):
        return 'unchecked'
    try:
        response = http.get(url, stream=True, headers={'Accept-Encoding': 'identity'})
        response.close()
    except Exception:
        return 'error'
    if response.status_code in (200, 206):
        return 'ok'
    # S3 answers 403 for files that no longer exist when the bucket can't be listed
    if response.status_code in (404, 410) or (response.status_code == 403 and host.endswith('amazonaws.com')):
        return 'dead'
    return 'error'


def check_all_links():
    """Record whether each vendor link still works, so the site can fall back to the archive copy."""
    cameras_json = get_cameras_json()
    urls = sorted({listing['url'] for entry in cameras_json.values() for listing in entry.get('listings') or []
                   if listing.get('url')})
    print(f'Checking {len(urls)} vendor links')
    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = dict(zip(urls, pool.map(check_link, urls)))

    checked_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
    with camera_json_lock:
        cameras_json = get_cameras_json()
        for entry in cameras_json.values():
            for listing in entry.get('listings') or []:
                if listing.get('url') in statuses:
                    listing['url_status'] = statuses[listing['url']]
                    listing['url_checked_at'] = checked_at
        save_cameras_json(cameras_json)

    counts = {}
    for status in statuses.values():
        counts[status] = counts.get(status, 0) + 1
    print(f'Link check: {counts}')


# Download hosts that only one vendor uses, for firmwares scraped before vendor tracking that no vendor lists anymore
VENDOR_HOSTS = {
    'dahuawiki.com': 'Dahua',
    'www.lorextechnology.com': 'Lorex',
    '52.45.202.118': 'Lorex',
    'gogss.com': 'GSS',
    'amcrest.com': 'Amcrest',
    'support.amcrest.com': 'Amcrest',
    'amcrest-firmwares.s3.amazonaws.com': 'Amcrest',
    'amcrest-firmwares.s3.us-east-1.amazonaws.com': 'Amcrest',
    'sup-files.s3.us-east-2.amazonaws.com': 'Amcrest',
    'materialfile.dahuasecurity.com': 'Dahua',
    'material.dahuasecurity.com': 'Dahua',
    'dahuasg.s3.ap-southeast-1.amazonaws.com': 'Dahua',
    'files.dahuatech.support': 'Dahua',
    'ftp.asm.cz': 'ASM',
    'downloads.rhinoco.com.au': 'Rhino',
    'www.rhinoco.com.au': 'Rhino',
    'rvigroup.ru': 'RVI',
    'icr-eb-bucket.s3.amazonaws.com': 'IC Realtime',
    'support.securitytronix.co': 'SecurityTronix',
    'downloadstore.boschsecurity.com': 'Bosch',
    'cpplusworld.com': 'CP Plus',
    'winictech.com': 'Winic',
    'backend.intelbras.com': 'Intelbras',
    'specotech.com': 'Speco',
    'support.optiviewusa.com': 'Optiview',
    'dh-vision.com': 'DH Vision',
}
VENDOR_S3_BUCKETS = {
    'amcrest-files': 'Amcrest',
    'amcrest-firmwares': 'Amcrest',
}


def infer_vendors_from_urls():
    """Give entries that no current listing covers a vendor and listing from their download host, so they still
    say where they came from (e.g. firmwares dahuawiki has since removed)."""
    with camera_json_lock:
        cameras_json = get_cameras_json()
        inferred = 0
        for entry in cameras_json.values():
            if entry.get('listings') or not entry.get('url'):
                continue
            url = urlparse(entry['url'])
            vendor = VENDOR_HOSTS.get(url.hostname)
            if vendor is None and url.hostname == 's3.amazonaws.com':
                vendor = VENDOR_S3_BUCKETS.get(url.path.split('/')[1])
            # Older entries may already name their vendor without having a listing
            if vendor is None and len(entry.get('vendors') or []) == 1:
                vendor = entry['vendors'][0]
            if vendor is None:
                continue

            entry['vendors'] = list(dict.fromkeys((entry.get('vendors') or []) + [vendor]))
            entry['listings'] = [{
                'vendor': vendor,
                'source': f'{url.hostname} (found in an earlier scrape)',
                'url': entry['url'],
                'camera_name': entry.get('camera_name') or [],
                'notes': entry.get('notes') or [],
            }]
            inferred += 1

        if inferred:
            save_cameras_json(cameras_json)
    print(f'Inferred vendors for {inferred} firmwares from their download URLs')


def validate_outputs():
    """Check both JSONs against docs/schema/ and the cross-file rules, so a change in shape is caught before the site
    reads them."""
    from util.validate_output import validate
    problems = validate()
    counts = {}
    for problem in problems:
        counts[problem.category] = counts.get(problem.category, 0) + 1
    print(f'Validation: {len(problems)} problems' + (': ' + ', '.join(f'{count} {category}' for category, count in
                                                         sorted(counts.items(), key=lambda item: -item[1]))
                                                     if problems else ''))
    return problems


def start_full_processing():
    get_all_firmwares()
    enrich_firmwares()
    process_all_firmwares()
    archive_all_firmwares()
    validate_outputs()


if __name__ == '__main__':
    # `python main.py` runs everything; `python main.py download|enrich|process|archive|check-links` runs one step
    steps = {'download': get_all_firmwares, 'enrich': enrich_firmwares, 'process': process_all_firmwares,
             'archive': archive_all_firmwares, 'check-links': check_all_links, 'validate': validate_outputs}
    # --only A,B / --skip A,B choose which sources are listed, e.g. MEGA downloads through a VPN on their own:
    #   python main.py download --only EmpireTech
    args = sys.argv[1:]
    for flag, target in (('--only', ONLY_MODULES), ('--skip', SKIP_MODULES)):
        while flag in args:
            i = args.index(flag)
            target.update(name.strip().casefold() for name in args[i + 1].split(',') if name.strip())
            del args[i:i + 2]
    if args:
        steps[args[0]]()
    else:
        start_full_processing()
