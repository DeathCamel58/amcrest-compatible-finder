import copy
import os.path
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import random
import re
import sys
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
from util.archive import archive_firmware, needs_refresh, refresh_archive
from util.download_firmware import download_firmware
from util.firmware_platform import detect_platform
from util.general import merge_unique_text, normalize_firmware_url
from util.firmware_processing import process_firmware_threaded
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
]

# TODO: Support additional Dahua OEMs
#       https://securitycamcenter.com/dahua-oem-list/



camera_json_lock = threading.Lock()


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
    return name.replace("/", "_")


def merge_listing(listings, firmware, firmware_type, new_camera_names):
    """Keep what each vendor page says about this file, since the top-level fields combine every vendor.

    One listing per vendor page (source); a page that lists the same file in several rows gets merged."""
    listings = [dict(listing) for listing in listings]
    listing = next((l for l in listings if l.get('vendor') == firmware['vendor'] and l.get('source') == firmware['source']), None)
    if listing is None:
        listing = {'vendor': firmware['vendor'], 'source': firmware['source']}
        listings.append(listing)

    listing['url'] = firmware[firmware_type]
    listing['camera_name'] = merge_unique_text((listing.get('camera_name') or []) + new_camera_names)
    listing['notes'] = merge_unique_text((listing.get('notes') or []) + [firmware['firmware_notes']])
    if firmware.get('firmware_changelog'):
        listing['changelog'] = firmware['firmware_changelog']
    if firmware_type == 'firmware_latest':
        for field in ['firmware_version', 'release_date']:
            if firmware.get(field):
                listing[field] = firmware[field].strip() if isinstance(firmware[field], str) else firmware[field]

    return listings


def download_firmware_thread(firmware, firmware_type):
    file_name = f'firmware/{get_firmware_file_name(firmware, firmware_type)}'

    downloaded = False
    if not os.path.exists(file_name):
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
        camera_names = merge_unique_text((existing.get('camera_name') or []) + new_camera_names)
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
            'listings': merge_listing(existing.get('listings') or [], firmware, firmware_type, new_camera_names),
        }

        if os.path.exists(file_name):
            firmware_data['firmware_size'] = os.stat(file_name).st_size
            # dahua / hikvision / unknown, from the file itself (some OEMs sell both, e.g. GSS Red|LINE is Hikvision)
            if not existing.get('platform') or downloaded:
                firmware_data['platform'] = detect_platform(file_name)

        if firmware.get('firmware_changelog'):
            firmware_data['changelog'] = firmware['firmware_changelog']

        if firmware_type == 'firmware_latest':
            for field in LATEST_METADATA_FIELDS:
                if firmware.get(field):
                    firmware_data[field] = firmware[field].strip() if isinstance(firmware[field], str) else firmware[field]

        if firmware_json_name in cameras_json:
            cameras_json[firmware_json_name].update(firmware_data)
        else:
            cameras_json[firmware_json_name] = firmware_data

        if cameras_json_original != cameras_json:
            save_cameras_json(cameras_json)


# Download all firmwares
def get_all_firmwares():
    firmwares = []

    for oem in oem_modules:
        # Don't let one broken vendor site stop the rest
        try:
            oem_firmwares = oem.get_firmwares()
        except Exception as err:
            print(f'Failed to get {oem.name} firmwares: {err!r}')
            continue
        print(f'Got a list of {len(oem_firmwares)} {oem.name} firmwares!')

        for oem_firmware in oem_firmwares:
            oem_firmware['vendor'] = oem.vendor
            oem_firmware['source'] = oem.name
            # Modules for hosts that need special handling (Google Drive, MEGA, ...) provide their own downloader
            oem_firmware['downloader'] = getattr(oem, 'download_file', None)
            for firmware_type in ["firmware_previous", "firmware_latest"]:
                oem_firmware[firmware_type] = normalize_firmware_url(oem_firmware[firmware_type])
            firmwares.append(oem_firmware)

    if len(firmwares) == 0:
        return

    # Shuffle the order of firmwares to hit different vendors at once (to prevent a slow vendor from stopping downloads)
    random.shuffle(firmwares)

    # Download all firmwares
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = []
        for firmware in firmwares:
            for firmware_type in ["firmware_previous", "firmware_latest"]:
                if firmware[firmware_type] is not None and firmware[firmware_type] != '':
                    futures.append(pool.submit(download_firmware_thread, firmware, firmware_type))
        for future in as_completed(futures):
            future.result()


def process_firmware(firmware_file):
    file_path = f"firmware/{firmware_file}"
    tmp_file_path = f"tmp/{firmware_file}"

    firmware_json = get_firmware_json()

    # Only get compatibility for firmwares we don't have yet
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = []

        if (firmware_file in firmware_json and firmware_json[firmware_file] == []) or (firmware_file not in firmware_json):
            futures.append(pool.submit(process_firmware_threaded, firmware_file, file_path, tmp_file_path))

        for future in as_completed(futures):
            future.result()


def process_all_firmwares():
    # Scratch space for unpacking firmwares
    os.makedirs('tmp', exist_ok=True)

    firmware_files = os.listdir('firmware')

    for firmware_file in firmware_files:
        # Skip incomplete downloads
        if firmware_file.endswith('.part'):
            continue
        # Don't let one unusual firmware stop the rest from being processed
        try:
            process_firmware(firmware_file)
        except Exception as err:
            print(f'Failed to process {firmware_file}: {err!r}')


def with_platform(entry, path):
    if entry.get('platform'):
        return entry
    return {**entry, 'platform': detect_platform(path)}


def archive_firmware_thread(firmware_file):
    path = f'firmware/{firmware_file}'
    entry = with_platform(get_cameras_json().get(firmware_file, {}), path)
    hardware_ids = get_firmware_json().get(firmware_file) or []

    try:
        if entry.get('archive_url') and entry.get('archive_md5'):
            # Already archived: only refresh the item's metadata and provenance record if they changed
            print(f'Updating archive metadata: {firmware_file}')
            archive_fields = {'archive_record_hash': refresh_archive(
                firmware_file, entry, hardware_ids, entry['archive_md5'], os.path.getsize(path))}
        else:
            print(f'Archiving: {firmware_file}')
            archive_fields = archive_firmware(path, entry, hardware_ids)
    except Exception as err:
        print(f'\tFailed to archive {firmware_file}: {err!r}')
        return

    with camera_json_lock:
        cameras_json = get_cameras_json()
        cameras_json.setdefault(firmware_file, {}).update({**archive_fields, 'platform': entry['platform']})
        save_cameras_json(cameras_json)
    print(f'\tArchived {firmware_file}: {cameras_json[firmware_file]["archive_item"]}')


def archive_all_firmwares():
    # Runs after listing in a full run, so this only covers firmwares no vendor lists anymore
    infer_vendors_from_urls()

    cameras_json = get_cameras_json()
    firmware_json = get_firmware_json()

    firmware_files = []
    for firmware_file in os.listdir('firmware'):
        if firmware_file.endswith('.part'):
            continue
        entry = with_platform(cameras_json.get(firmware_file, {}), f'firmware/{firmware_file}')
        hardware_ids = firmware_json.get(firmware_file) or []
        if not entry.get('archive_url') or not entry.get('archive_md5') or needs_refresh(
                firmware_file, entry, hardware_ids, os.path.getsize(f'firmware/{firmware_file}')):
            firmware_files.append(firmware_file)

    # Firmwares no vendor currently lists go first, since their source is the most likely to be gone
    firmware_files.sort(key=lambda f: (bool(cameras_json.get(f, {}).get('vendors')), f))
    print(f'Archiving or updating {len(firmware_files)} firmwares on the Internet Archive')

    # Keep this low; archive.org throttles bulk uploads
    with ThreadPoolExecutor(max_workers=3) as pool:
        for future in as_completed([pool.submit(archive_firmware_thread, f) for f in firmware_files]):
            future.result()


# Download hosts that only one vendor uses, for firmwares scraped before vendor tracking that no vendor lists anymore
VENDOR_HOSTS = {
    'dahuawiki.com': 'Dahua',
    'www.lorextechnology.com': 'Lorex',
    '52.45.202.118': 'Lorex',
    'gogss.com': 'GSS',
    'amcrest-firmwares.s3.amazonaws.com': 'Amcrest',
    'amcrest-firmwares.s3.us-east-1.amazonaws.com': 'Amcrest',
}
VENDOR_S3_BUCKETS = {
    'amcrest-files': 'Amcrest',
    'amcrest-firmwares': 'Amcrest',
}


def infer_vendors_from_urls():
    """Give entries that no current listing covers a vendor from their download host, so they still say where
    they came from (e.g. firmwares dahuawiki has since removed)."""
    with camera_json_lock:
        cameras_json = get_cameras_json()
        inferred = 0
        for entry in cameras_json.values():
            if entry.get('vendors') or not entry.get('url'):
                continue
            url = urlparse(entry['url'])
            vendor = VENDOR_HOSTS.get(url.hostname)
            if vendor is None and url.hostname == 's3.amazonaws.com':
                vendor = VENDOR_S3_BUCKETS.get(url.path.split('/')[1])
            if vendor is None:
                continue

            entry['vendors'] = [vendor]
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
    process_all_firmwares()
    archive_all_firmwares()


if __name__ == '__main__':
    # `python main.py` runs everything; `python main.py download|process|archive` runs a single step
    steps = {'download': get_all_firmwares, 'process': process_all_firmwares, 'archive': archive_all_firmwares}
    if len(sys.argv) > 1:
        steps[sys.argv[1]]()
    else:
        start_full_processing()
