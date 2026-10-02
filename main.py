import copy
import hashlib
import os.path
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import re
import sys
from datetime import date, datetime, timezone
from urllib.parse import urlparse

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
    name = name.replace("/", "_")
    # File names are limited to 255 bytes; some (RVI's model lists in Cyrillic) are longer. Shorten them, keeping the
    # end (version and extension) and a hash of the full name so they stay unique
    if len(name.encode()) > MAX_FILE_NAME_BYTES:
        stem, extension = os.path.splitext(name)
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
    models, series = split_model_names(
        merge_unique_text((listing.get('camera_name') or []) + (listing.get('series') or []) + new_camera_names),
        firmware_file)
    listing['camera_name'] = models
    if series:
        listing['series'] = series
    listing['notes'] = merge_unique_text((listing.get('notes') or []) + [firmware['firmware_notes']])
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

    # TODO: Add a lock here since we're reading, modifying, then writing back a file

    with camera_json_lock:
        cameras_json = get_cameras_json()
        cameras_json_original = copy.deepcopy(cameras_json)
        firmware_json_name = file_name[9:]

        existing = cameras_json.get(firmware_json_name, {})

        # Merge what's already stored with what this listing provides
        new_camera_names = firmware['camera_name'] if isinstance(firmware['camera_name'], list) else [firmware['camera_name']]
        camera_names, series = split_model_names(
            merge_unique_text((existing.get('camera_name') or []) + (existing.get('series') or []) + new_camera_names),
            firmware_json_name)
        firmware_notes = merge_unique_text((existing.get('notes') or []) + [firmware['firmware_notes']])

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
DOWNLOAD_WORKERS = 10
# Servers that throttle heavy users get at most this many downloads at once
HOST_CAPS = {
    'web.archive.org': 2,
    'mega.nz': 2,
    'drive.google.com': 2,
    'drive.usercontent.google.com': 2,
    'www.dropbox.com': 2,
}


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
        firmwares = [firmware for listed in pool.map(list_vendor, oem_modules) for firmware in listed]

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
    print_download_summary(tasks)

    # Spread downloads across servers rather than working through one vendor's list at a time
    scheduler = HostScheduler(DOWNLOAD_WORKERS, HOST_CAPS)
    for firmware, firmware_type in tasks:
        scheduler.add(urlparse(firmware[firmware_type]).hostname or '', download_firmware_thread, firmware, firmware_type)
    scheduler.run()


def list_firmware_files():
    return sorted(f for f in os.listdir('firmware')
                  if not f.endswith(('.part', '.redownload')) and os.path.isfile(f'firmware/{f}'))


def enrich_firmwares():
    """Hash every firmware, link files with identical content, and tidy model names in existing entries."""
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

        # Older entries were stored before junk model names were filtered out
        for firmware_file, entry in cameras_json.items():
            models, series = split_model_names((entry.get('camera_name') or []) + (entry.get('series') or []), firmware_file)
            entry['camera_name'] = models
            if series:
                entry['series'] = series
            for listing in entry.get('listings') or []:
                models, series = split_model_names((listing.get('camera_name') or []) + (listing.get('series') or []), firmware_file)
                listing['camera_name'] = models
                if series:
                    listing['series'] = series

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

    process_firmware_threaded(firmware_file, file_path, previous, integrity)


def process_all_firmwares():
    # Scratch space for unpacking firmwares
    os.makedirs('tmp', exist_ok=True)

    for firmware_file in list_firmware_files():
        # Don't let one unusual firmware stop the rest from being processed
        try:
            process_firmware(firmware_file)
        except Exception as err:
            print(f'Failed to process {firmware_file}: {err!r}')

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


def start_full_processing():
    get_all_firmwares()
    enrich_firmwares()
    process_all_firmwares()
    archive_all_firmwares()


if __name__ == '__main__':
    # `python main.py` runs everything; `python main.py download|enrich|process|archive|check-links` runs one step
    steps = {'download': get_all_firmwares, 'enrich': enrich_firmwares, 'process': process_all_firmwares,
             'archive': archive_all_firmwares, 'check-links': check_all_links}
    if len(sys.argv) > 1:
        steps[sys.argv[1]]()
    else:
        start_full_processing()
