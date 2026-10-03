import atexit
import os
import shutil
import signal
import struct
import subprocess
import tempfile
import threading
import time
import zipfile
import zlib
from datetime import datetime, timedelta, timezone

# CHIP_IMAGE_NAME and uimage_name are re-exported for older callers and tests
from util.firmware_metadata import (CHIP_IMAGE_NAME, UIMAGE_MAGIC, FileMember, Member, PackageReader, combine,  # noqa: F401
                                    members_in_directory, uimage_name)
from util.hardware import classify_hardware_ids
from util.json_tools import save_firmware_json, get_firmware_json


firmware_processing_lock = threading.Lock()

# Increase when the extraction logic improves, so firmwares without hardware IDs get analyzed again
# 3: read standalone "hwid" files, and the intact part of truncated zip-style files
# 4: read the hardware lists straight out of zip-style firmwares (including nested ones and Dahua's "DHSP" PTZ
#    variant), which finds IDs in some files binwalk didn't
# 5: hardware IDs by source (with the full hwid entries), SoC, partition layout, security, locale and versions
EXTRACTOR_VERSION = 5
# Results from before this version lack the per-source / package fields, so even "ok" ones are redone
METADATA_VERSION = 5
# Failed extractions are retried on later runs up to this many times per extractor version
MAX_EXTRACT_ATTEMPTS = 3


def clean_tmp(path):
    try:
        shutil.rmtree(path)
    except Exception as e:
        print('Failed to delete %s. Reason: %s' % (path, e))


class ExtractionError(Exception):
    pass


# One pathological file shouldn't hold a processing worker forever
BINWALK_TIMEOUT = 30 * 60
# Members of a truncated file bigger than this (uncompressed) aren't extracted; the hardware IDs are in small files
MAX_PARTIAL_MEMBER_SIZE = 2 * 1024 ** 3

# Zip-style containers: ordinary zips, and Dahua's with "DH" or (PTZ firmwares) "DHSP" in place of the "PK" signature
LOCAL_HEADER_SIGNATURES = (b'PK\x03\x04', b'DH\x03\x04', b'DHSP')
PACKAGE_FORMATS = {b'PK\x03\x04': 'zip', b'DH\x03\x04': 'dh', b'DHSP': 'dhsp'}
# Members that can be firmwares themselves (a zip around a Dahua .bin, or a .bin bundling several)
NESTED_EXTENSIONS = ('.bin', '.zip', '.img', '.dav')
MAX_NESTED_DEPTH = 3


def extract_firmware(path):
    """Unpack a firmware with binwalk and read what it holds (see firmware_metadata.combine). Raises ExtractionError
    if it can't be unpacked."""
    workdir = tempfile.mkdtemp(prefix="bw_")

    try:
        file_name = os.path.basename(path)
        local_fw = os.path.join(workdir, file_name)

        # Link the firmware into the binwalk CWD rather than copying it (several GB, read from a spinning disk)
        os.symlink(os.path.realpath(path), local_fw)

        # Run binwalk inside the temp directory, in its own process group so a timeout can stop the extractors it
        # starts (7zz, unsquashfs, ...) too
        process = subprocess.Popen(['/usr/bin/binwalk', '-e', file_name], cwd=workdir,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            returncode = process.wait(timeout=BINWALK_TIMEOUT)
        except subprocess.TimeoutExpired as err:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise ExtractionError("binwalk timed out") from err
        if returncode != 0:
            raise ExtractionError(f"binwalk exited with {returncode}")

        extracted_src = os.path.join(workdir, 'extractions', file_name + ".extracted")

        if not os.path.isdir(extracted_src):
            raise ExtractionError("binwalk found nothing to extract")

        try:
            return read_extracted_firmware(extracted_src)
        except Exception as err:
            raise ExtractionError(f"reading extracted files failed: {err!r}") from err

    finally:
        shutil.rmtree(workdir, ignore_errors=True)



def read_extracted_firmware(path):
    """What binwalk's extraction folder holds: the folder and its immediate subfolders, read as one package."""
    # A Dahua .bin unpacks to 0/dahua.zip, which holds the actual members
    zip_parent_path = os.path.join(path, '0')
    zip_path = os.path.join(zip_parent_path, 'dahua.zip')
    if os.path.exists(zip_path):
        subprocess.run(['unzip', '-o', '-qq', zip_path, '-d', path], capture_output=True)
        shutil.rmtree(zip_parent_path)
    return combine([PackageReader(members_in_directory(path))], 'other')

def extract_if_zip(path):
    """If path is a zip that unzip can open, extract it into its own folder under tmp/ and return
    (member paths, folder). Returns ([], None) for anything else, so it gets handed to binwalk."""
    file_type = subprocess.run(['file', '-b', path], capture_output=True).stdout.decode('utf-8', 'replace')
    if "Zip archive" not in file_type:
        return [], None

    os.makedirs('tmp', exist_ok=True)
    extraction_path = tempfile.mkdtemp(prefix='zip_', dir='tmp')

    # Exit code 1 means warnings only; the files were still extracted
    zip_process = subprocess.run(['unzip', '-o', '-qq', path, '-d', extraction_path], capture_output=True)
    if zip_process.returncode not in (0, 1):
        # Dahua .bin files are zips with "DH" in place of "PK", which unzip can't open but binwalk can
        clean_tmp(extraction_path)
        return [], None

    files = [os.path.join(root, name) for root, _, names in os.walk(extraction_path) for name in sorted(names)]
    return files, extraction_path


def extract_complete_entries(path, destination):
    """Copy out the complete entries of a cut-off zip-style file (PK or Dahua's DH variant) by walking its local
    headers, since the directory at the end is missing. Returns the number of entries extracted.

    Streams the file (header by header, member by member) rather than reading it into memory: several multi-GB
    truncated images unpacked by parallel workers would otherwise exhaust RAM."""
    extracted = 0
    with open(path, 'rb') as f:
        size = os.fstat(f.fileno()).st_size
        position = 0
        while position + 30 <= size:
            f.seek(position)
            header = f.read(30)
            if header[:4] not in (b'PK\x03\x04', b'DH\x03\x04'):
                break
            method = struct.unpack('<H', header[8:10])[0]
            compressed_size, uncompressed_size = struct.unpack('<II', header[18:26])
            name_length, extra_length = struct.unpack('<HH', header[26:30])
            name = f.read(name_length).decode('utf-8', 'replace')
            start = position + 30 + name_length + extra_length
            end = start + compressed_size
            if end > size:
                break  # this is where the file was cut off

            safe_name = os.path.basename(name)
            if safe_name and uncompressed_size <= MAX_PARTIAL_MEMBER_SIZE:
                f.seek(start)
                if not copy_member(f, compressed_size, method, os.path.join(destination, safe_name)):
                    break
                extracted += 1
            position = end

    return extracted


def copy_member(f, compressed_size, method, destination, chunk_size=4 * 1024 * 1024):
    """Write one member (stored or deflated) from f's current position to destination. False if it's corrupt."""
    decompressor = zlib.decompressobj(-15) if method == 8 else None
    remaining = compressed_size
    try:
        with open(destination, 'wb') as out:
            while remaining > 0:
                chunk = f.read(min(chunk_size, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                out.write(decompressor.decompress(chunk) if decompressor else chunk)
            if decompressor:
                out.write(decompressor.flush())
    except zlib.error:
        os.remove(destination)
        return False
    return True


# Zip-style containers: ordinary zips, and Dahua's with "DH" or (PTZ firmwares) "DHSP" in place of the "PK" signature

def iter_local_entries(f, start, end):
    """(name, method, data start, compressed size, uncompressed size) for each complete member of the zip-style
    container in f between start and end, by walking the local headers. Stops at the central directory, at anything
    that isn't a local header, at a cut-off member, and at members whose sizes aren't in their header."""
    position = start
    while position + 30 <= end:
        f.seek(position)
        header = f.read(30)
        if len(header) < 30 or header[:4] not in LOCAL_HEADER_SIGNATURES:
            return
        flags, method = struct.unpack('<HH', header[6:10])
        compressed_size, uncompressed_size = struct.unpack('<II', header[18:26])
        name_length, extra_length = struct.unpack('<HH', header[26:30])
        name = f.read(name_length).decode('utf-8', 'replace')
        data_start = position + 30 + name_length + extra_length
        if (flags & 0x08 and compressed_size == 0) or compressed_size == 0xFFFFFFFF:
            return  # sizes in a data descriptor after the data, or in a ZIP64 extra field
        if data_start + compressed_size > end:
            return
        yield name, method, data_start, compressed_size, uncompressed_size
        position = data_start + compressed_size


def read_member(f, data_start, compressed_size, method):
    f.seek(data_start)
    data = f.read(compressed_size)
    if method == 8:
        return zlib.decompressobj(-15).decompress(data)
    if method == 0:
        return data
    raise ExtractionError(f"unsupported compression method {method}")



def starts_with_container(f, data_start, compressed_size, method):
    """Whether a member's (decompressed) content starts with a local header, reading only its first bytes."""
    f.seek(data_start)
    head = f.read(min(compressed_size, 64 * 1024))
    if method == 8:
        try:
            head = zlib.decompressobj(-15).decompress(head, 4)
        except zlib.error:
            return False
    return head[:4] in LOCAL_HEADER_SIGNATURES



class ZipMember(Member):
    """A member of a zip-style container, read in place."""

    def __init__(self, f, path, method, data_start, compressed_size, uncompressed_size):
        self.f, self.path, self.method = f, path, method
        self.data_start, self.compressed_size, self.size = data_start, compressed_size, uncompressed_size

    def head(self, length=64 + 4096):
        self.f.seek(self.data_start)
        if self.method == 0:
            return self.f.read(min(length, self.compressed_size))
        raw = self.f.read(min(self.compressed_size, max(64 * 1024, length * 4)))
        if self.method != 8:
            return b''
        try:
            return zlib.decompressobj(-15).decompress(raw, length)
        except zlib.error:
            return b''

    def read(self, limit=4 * 1024 ** 2):
        if self.size > limit or self.method not in (0, 8):
            return None
        return read_member(self.f, self.data_start, self.compressed_size, self.method)


class FileWindow:
    """A read-only, seekable view of f[start:end], so zipfile can read a container stored inside another."""

    def __init__(self, f, start, end):
        self.f, self.start, self.end, self.position = f, start, end, 0

    def seekable(self):
        return True

    def seek(self, offset, whence=0):
        base = {0: 0, 1: self.position, 2: self.end - self.start}[whence]
        self.position = max(0, base + offset)
        return self.position

    def tell(self):
        return self.position

    def read(self, size=-1):
        remaining = self.end - self.start - self.position
        size = remaining if size is None or size < 0 else min(size, remaining)
        if size <= 0:
            return b''
        self.f.seek(self.start + self.position)
        data = self.f.read(size)
        self.position += len(data)
        return data


def central_directory_entries(f, start, end):
    """Like iter_local_entries, but from a standard zip's central directory, which also covers members whose sizes
    are in data descriptors (common in zips made by archivers) and ZIP64. Raises zipfile.BadZipFile without one."""
    window = FileWindow(f, start, end)
    entries = []
    with zipfile.ZipFile(window) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
    for info in infos:
        window.seek(info.header_offset)
        header = window.read(30)
        if len(header) < 30 or header[:4] != b'PK\x03\x04':
            raise zipfile.BadZipFile(f"no local header for {info.filename}")
        name_length, extra_length = struct.unpack('<HH', header[26:30])
        data_start = start + info.header_offset + 30 + name_length + extra_length
        if data_start + info.compress_size > end:
            raise zipfile.BadZipFile(f"{info.filename} runs past the end")
        entries.append((info.filename, info.compress_type, data_start, info.compress_size, info.file_size))
    return entries


def container_entries(f, start, end, package_format):
    """A container's members: from the central directory for standard zips, else (Dahua's variants, cut-off files)
    by walking the local headers."""
    if package_format == 'zip':
        try:
            return central_directory_entries(f, start, end)
        except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError, struct.error, EOFError):
            pass
    return list(iter_local_entries(f, start, end))


def scan_container(f, start, end, readers, temp_dir, firmware=None, depth=0):
    """Read the zip-style container in f[start:end] into a PackageReader (appended to readers), then descend into
    the firmwares nested in it, each read as its own package. Returns the container's format, or None if there's no
    container at start."""
    f.seek(start)
    package_format = PACKAGE_FORMATS.get(f.read(4))
    if package_format is None:
        return None

    members, nested = [], []
    for name, method, data_start, compressed_size, uncompressed_size in container_entries(f, start, end,
                                                                                          package_format):
        base = os.path.basename(name)
        if base.startswith('._') or '__MACOSX/' in name:
            continue  # macOS resource forks
        if (depth < MAX_NESTED_DEPTH and base.lower().endswith(NESTED_EXTENSIONS) and method in (0, 8)
                and starts_with_container(f, data_start, compressed_size, method)):
            nested.append((base, method, data_start, compressed_size))
        else:
            members.append(ZipMember(f, name, method, data_start, compressed_size, uncompressed_size))
    readers.append(PackageReader(members, firmware, package_format))

    for base, method, data_start, compressed_size in nested:
        if method == 0:
            # Stored: read it in place
            scan_container(f, data_start, data_start + compressed_size, readers, temp_dir, base, depth + 1)
        else:
            # Deflated: inflate it to a temporary file first
            nested_path = os.path.join(temp_dir, f"nested_{len(readers)}")
            f.seek(data_start)
            if copy_member(f, compressed_size, method, nested_path):
                with open(nested_path, 'rb') as nested_file:
                    scan_container(nested_file, 0, os.fstat(nested_file.fileno()).st_size, readers, temp_dir, base,
                                   depth + 1)
            if os.path.exists(nested_path):
                os.remove(nested_path)
    return package_format


def read_firmware(path):
    """What a zip-style firmware holds, read in place: only the headers, the small metadata members, and the first
    bytes of each image are read, instead of unpacking the whole thing with binwalk. Returns firmware_metadata.combine
    output, or None when the file isn't a zip-style container. A cut-off file gives what its intact part holds."""
    with tempfile.TemporaryDirectory(prefix='meta_') as temp_dir:
        readers = []
        with open(path, 'rb') as f:
            package_format = scan_container(f, 0, os.fstat(f.fileno()).st_size, readers, temp_dir)
            if package_format is None:
                return None
            if sum(1 for reader in readers if reader.firmware) >= 2:
                package_format = 'bundle'
            return combine(readers, package_format)


def read_hardware_ids(path):
    """Just the hardware IDs of a zip-style firmware (see read_firmware), or None if it isn't one."""
    extraction = read_firmware(path)
    return None if extraction is None else extraction['hardware_ids']


def analysis(hardware_ids, status, error=None, extraction=None):
    extraction = extraction or {}
    return {
        'hardware_ids': sorted(hardware_ids),
        'status': status,
        'error': error,
        'hardware_sources': extraction.get('hardware_sources') or [],
        'packages': extraction.get('packages') or [],
        'package_format': extraction.get('package_format'),
    }


def label_inner(result, firmware):
    """Tag a nested file's sources and packages with its name, where they don't name an inner firmware already."""
    for item in result['hardware_sources'] + result['packages']:
        if not item.get('firmware'):
            item['firmware'] = firmware
    return result


def analyze_truncated_firmware(file_path):
    """The installer metadata (hwid, Install, check.img) usually comes first, so it survives a cut-off download."""
    try:
        extraction = read_firmware(file_path)
    except (OSError, zlib.error, struct.error, ExtractionError) as err:
        print(f"\tReading truncated {os.path.basename(file_path)} failed: {err!r}")
        extraction = None
    if extraction is None:
        workdir = tempfile.mkdtemp(prefix="partial_")
        try:
            if not extract_complete_entries(file_path, workdir):
                return analysis([], "extract_failed", "truncated: no complete entries")
            extraction = combine([PackageReader(members_in_directory(workdir))], 'other')
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    if extraction['hardware_ids']:
        return analysis(extraction['hardware_ids'], "ok", None, extraction)
    return analysis([], "extract_failed", "truncated: the intact part has no hardware IDs", extraction)


def analyze_slowly(file_path):
    """Unzip (standard zips) or binwalk (everything else) the firmware and read what it holds."""
    try:
        zip_files, extraction_path = extract_if_zip(file_path)
    except ExtractionError as err:
        return analysis([], "extract_failed", str(err))

    if extraction_path is not None:
        # A zip can hold several firmwares; combine what each one supports
        hardware_ids, statuses, errors, sources, packages = set(), [], [], [], []
        try:
            for file in zip_files:
                member = label_inner(analyze_firmware(file), os.path.relpath(file, extraction_path))
                hardware_ids.update(member['hardware_ids'])
                statuses.append(member['status'])
                sources += member['hardware_sources']
                packages += member['packages']
                if member['error']:
                    errors.append(f"{os.path.basename(file)}: {member['error']}")
        finally:
            clean_tmp(extraction_path)

        extraction = {'hardware_sources': sources, 'packages': packages,
                      'package_format': 'bundle' if len(packages) >= 2 else 'zip'}
        if hardware_ids:
            return analysis(hardware_ids, "ok", None, extraction)
        if statuses and all(status == "extract_failed" for status in statuses):
            return analysis([], "extract_failed", "; ".join(errors), extraction)
        return analysis([], "no_ids", None, extraction)

    try:
        extraction = extract_firmware(file_path)
    except ExtractionError as err:
        return analysis([], "extract_failed", str(err))
    return analysis(extraction['hardware_ids'], "ok" if extraction['hardware_ids'] else "no_ids", None, extraction)


def analyze_firmware(file_path, integrity=None):
    """Returns a dict of hardware_ids, status ("ok", "no_ids" or "extract_failed"), error, hardware_sources,
    packages and package_format. Never modifies file_path; anything unpacked goes in temporary folders."""
    if integrity and integrity.get("status") == "truncated":
        return analyze_truncated_firmware(file_path)

    # Most firmwares are zip-style, with the hardware list in small members that can be read directly. Anything
    # else, or a container where that finds nothing, is unpacked the slow way
    try:
        extraction = read_firmware(file_path)
    except (OSError, zlib.error, struct.error, ExtractionError) as err:
        print(f"\tReading {os.path.basename(file_path)} directly failed, unpacking it instead: {err!r}")
        extraction = None
    if extraction and extraction['hardware_ids']:
        return analysis(extraction['hardware_ids'], "ok", None, extraction)

    result = analyze_slowly(file_path)
    if extraction and not result['packages']:
        # The direct read found the layout even though the hardware list needed (or failed) the slow way
        result['packages'] = extraction['packages']
        result['package_format'] = extraction['package_format']
    if not result['packages']:
        result['packages'] = single_image_packages(file_path)
    return result


def single_image_packages(file_path):
    """A file that is one image (a u-boot or kernel uImage on its own) as a one-partition package."""
    try:
        with open(file_path, 'rb') as f:
            if f.read(4) != UIMAGE_MAGIC:
                return []
        reader = PackageReader([FileMember(file_path, os.path.basename(file_path))])
    except OSError:
        return []
    return [reader.package()] if reader.images else []


# Results are saved in batches: firmware_compatible.json is tens of MB, and rewriting it after every firmware
# serialises the workers on the save
SAVE_EVERY = 50
SAVE_SECONDS = 60
_pending_results = {}
_last_save = [time.monotonic()]


def save_result(firmware_file, result):
    with firmware_processing_lock:
        _pending_results[firmware_file] = result
        if len(_pending_results) >= SAVE_EVERY or time.monotonic() - _last_save[0] >= SAVE_SECONDS:
            _save_pending()


def flush_results():
    """Save the results not saved yet. Call when processing is done (and it runs at exit)."""
    with firmware_processing_lock:
        _save_pending()


def _save_pending():
    if _pending_results:
        firmware_json = get_firmware_json()
        firmware_json.update(_pending_results)
        save_firmware_json(firmware_json)
        _pending_results.clear()
    _last_save[0] = time.monotonic()


atexit.register(flush_results)



def process_firmware_threaded(firmware_file, file_path, previous=None, integrity=None):
    print(f"Processing: {firmware_file}")
    found = analyze_firmware(file_path, integrity)
    status = found['status']

    result = {
        "hardware_ids": found['hardware_ids'],
        "hardware": classify_hardware_ids(found['hardware_ids']),
        "status": status,
        "extractor_version": EXTRACTOR_VERSION,
        "processed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "hardware_sources": found['hardware_sources'],
        "packages": found['packages'],
        "package_format": found['package_format'],
    }
    if integrity and integrity.get("status") == "truncated":
        # Partial result: the file is incomplete, so this is only what its intact part shows
        result["truncated"] = True
    if status == "extract_failed":
        result["error"] = found['error']
        # Count failures under the same extractor version, so persistent failures stop being retried
        same_version = previous and previous.get("extractor_version") == EXTRACTOR_VERSION
        result["attempts"] = (previous.get("attempts", 0) if same_version else 0) + 1

    save_result(firmware_file, result)
    return result

def mark_not_dahua(firmware_file, platform):
    result = {
        "hardware_ids": [],
        "hardware": classify_hardware_ids([]),
        "status": "not_dahua",
        "platform": platform,
        "extractor_version": EXTRACTOR_VERSION,
        "processed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    save_result(firmware_file, result)
    return result



# A failed extraction isn't retried until this long after it failed, so restarting a run doesn't redo the failures
# (often multi-GB images) it just went through
RETRY_FAILED_AFTER = timedelta(hours=20)


def failed_recently(result):
    try:
        processed_at = datetime.fromisoformat(result["processed_at"])
    except (KeyError, TypeError, ValueError):
        return False
    return datetime.now(timezone.utc) - processed_at < RETRY_FAILED_AFTER


def needs_processing(result, platform):
    """Whether a firmware should be (re)analyzed, given its stored result and detected platform."""
    if not result:
        return True
    status = result.get("status")
    if status == "ok":
        # Results from before the per-source and package fields were added are redone to fill them in
        return result.get("extractor_version", 1) < METADATA_VERSION
    if status == "not_dahua":
        # Only if platform detection has since changed its mind
        return platform == "dahua"
    if result.get("extractor_version", 1) < EXTRACTOR_VERSION:
        return True
    if status == "extract_failed":
        return result.get("attempts", 1) < MAX_EXTRACT_ATTEMPTS and not failed_recently(result)
    return False
