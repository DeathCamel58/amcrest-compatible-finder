import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import zlib
from datetime import datetime, timezone

from util.hardware import classify_hardware_ids
from util.json_tools import save_firmware_json, get_firmware_json


firmware_processing_lock = threading.Lock()

# Increase when the extraction logic improves, so firmwares without hardware IDs get analyzed again
# 3: read standalone "hwid" files, and the intact part of truncated zip-style files
EXTRACTOR_VERSION = 3
# Failed extractions are retried on later runs up to this many times per extractor version
MAX_EXTRACT_ATTEMPTS = 3


def clean_tmp(path):
    try:
        shutil.rmtree(path)
    except Exception as e:
        print('Failed to delete %s. Reason: %s' % (path, e))


class ExtractionError(Exception):
    pass


def extract_firmware(path):
    """Unpack a firmware with binwalk and return the hardware IDs found. Raises ExtractionError if it can't be unpacked."""
    workdir = tempfile.mkdtemp(prefix="bw_")

    try:
        file_name = os.path.basename(path)
        local_fw = os.path.join(workdir, file_name)

        # Copy the actual firmware file into the binwalk CWD
        shutil.copy(path, local_fw)

        # Run binwalk inside the temp directory
        try:
            subprocess.check_output(
                ['/usr/bin/binwalk', '-e', file_name],  # use local file
                cwd=workdir,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError as err:
            raise ExtractionError(f"binwalk exited with {err.returncode}") from err

        extracted_src = os.path.join(workdir, 'extractions', file_name + ".extracted")

        if not os.path.isdir(extracted_src):
            raise ExtractionError("binwalk found nothing to extract")

        try:
            return get_extracted_firmware_compatibility(extracted_src)
        except Exception as err:
            raise ExtractionError(f"reading extracted files failed: {err!r}") from err

    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def check_firmware_compatibility(path):
    # TODO: Determine how NVR compatibility works
    compatible_ids = []

    files = os.listdir(path)

    # Some firmwares store the IDs in check.img, newer ones in a separate "hwid" file with the same JSON
    hwid_file = 'check.img' if 'check.img' in files else 'hwid' if 'hwid' in files else None
    if hwid_file:
        print(f"Found {hwid_file}!")

        data = bytes

        with (open(f'{path}/{hwid_file}', 'rb') as f):
            content = f.read()

            data = content.split(b'{')
            if len(data) > 1:
                data = data[-1]

            data = data.split(b'}')
            if len(data) > 1:
                data = data[0]

        data = data.decode('utf-8')
        data = "{" + data + "}"

        data = json.loads(data)

        if 'hwid' in data:
            print("\tFound hwid!")

            for hwid in data['hwid']:
                compatible_id = hwid.split(":")

                if len(compatible_id) > 0:
                    compatible_id = compatible_id[0]

                    compatible_ids.append(compatible_id)
        else:
            print("\tNo hwid")

    # Some NVR/XVRs store the IDs in Install
    if len(compatible_ids) == 0 and 'Install' in files:
        data = None

        with (open(f'{path}/Install', 'r', encoding='gb2312') as f):
            data = f.read()

        try:
            # Cleanup the data string to remove any trailing comments
            data = data[: data.rfind('}') + 1]
            data = json.loads(data)

            for i in range(len(data["Devices"])):
                compatible_ids.append(data["Devices"][i][0])
        except Exception as err:
            print(f"\t{err}")

    # Some NVR/XVRs store the IDs in Install.lua
    if len(compatible_ids) == 0 and 'Install.lua' in files:
        data = None

        with (open(f'{path}/Install.lua', 'r', encoding='gb2312') as f):
            data = f.read()

        regex_parse = re.split(r'(?:board.name|vendor.Name) +[~|=]= +["|\']', data)
        if len(regex_parse) > 1:
            regex_parse.pop(0)

            for i in range(len(regex_parse)):
                compatible_id = re.split(r'["|\']', regex_parse[i])
                if len(compatible_id) > 0:
                    compatible_id = compatible_id[0]
                    compatible_ids.append(compatible_id)

    if len(compatible_ids) == 0 and 'u-boot.bin.img' in files:
        print("Found u-boot.bin.img!")
        # Run binwalk on the u-boot image
        # Check for `uImage header` in the output
        # Examples
        # 0             0x0             uImage header, header size: 64 bytes, header CRC: 0xA471488C, created: 2020-06-01 07:36:47, image size: 2768896 bytes, Data Address: 0xA0140000, Entry Point: 0xA0540000, data CRC: 0x7E72D059, OS: Linux, CPU: ARM, image type: Firmware Image, compression type: gzip, image name: "NVR4XXX-4KS2/L"
        # 0             0x0             uImage header, header size: 64 bytes, header CRC: 0xD668B917, created: 2023-09-09 10:00:32, image size: 1030408 bytes, Data Address: 0xA0100000, Entry Point: 0xA02C0000, data CRC: 0x35724575, OS: Linux, CPU: ARM, image type: Standalone Program, compression type: gzip, image name: "5x32FW98336Tboot"
        # 0             0x0             uImage header, header size: 64 bytes, header CRC: 0x460E4C2C, created: 2017-03-25 02:49:06, image size: 259252 bytes, Data Address: 0xA0000000, Entry Point: 0xA0040000, data CRC: 0x83DBDA06, OS: Linux, CPU: ARM, image type: Firmware Image, compression type: gzip, image name: "3535boot"
        # 0             0x0             uImage header, header size: 64 bytes, header CRC: 0xF61065CA, created: 2022-07-19 06:19:49, image size: 360960 bytes, Data Address: 0xA0000000, Entry Point: 0xA0300000, data CRC: 0x574143FA, OS: Linux, CPU: ARM, image type: Firmware Image, compression type: gzip, image name: "NVR4X-S2"
        # Use `image name` string as the compatible list
        binwalk = subprocess.run(['/usr/bin/binwalk', f"{path}/u-boot.bin.img"], capture_output=True)
        if 'image name: "' in binwalk.stdout.decode('utf-8'):
            uimage_header = binwalk.stdout.decode('utf-8').split('image name: "')
            if len(uimage_header) > 1:
                uimage_header = uimage_header[1]
                uimage_header = uimage_header.split('"')
                if len(uimage_header) > 1:
                    uimage_header = uimage_header[0]
            compatible_ids.append(uimage_header)

    return compatible_ids


def get_extracted_firmware_compatibility(path):
    # Check if path/0/dahua.zip exists and if so, extract to path
    zip_parent_path = os.path.join(path, '0')
    zip_path = os.path.join(zip_parent_path, 'dahua.zip')
    if os.path.exists(zip_path):
        subprocess.run(['unzip', zip_path, '-d', path], capture_output=True)
        shutil.rmtree(zip_parent_path)

    compatible_ids = []

    compatible = check_firmware_compatibility(path)
    if len(compatible) > 0:
        for x in range(len(compatible)):
            compatible_ids.append(compatible[x])

    files = os.listdir(path)
    for i in range(len(files)):
        file_path = os.path.join(path, files[i])
        if os.path.isdir(file_path):
            compatible = check_firmware_compatibility(file_path)
            if len(compatible) > 0:
                for x in range(len(compatible)):
                    compatible_ids.append(compatible[x])

    return sorted(list(set(compatible_ids)))


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
    headers, since the directory at the end is missing. Returns the number of entries extracted."""
    extracted = 0
    with open(path, 'rb') as f:
        data = f.read()
    position = 0
    while position + 30 <= len(data) and data[position:position + 4] in (b'PK\x03\x04', b'DH\x03\x04'):
        method = struct.unpack('<H', data[position + 8:position + 10])[0]
        compressed_size = struct.unpack('<I', data[position + 18:position + 22])[0]
        name_length, extra_length = struct.unpack('<HH', data[position + 26:position + 30])
        name = data[position + 30:position + 30 + name_length].decode('utf-8', 'replace')
        start = position + 30 + name_length + extra_length
        end = start + compressed_size
        if end > len(data):
            break  # this is where the file was cut off

        safe_name = os.path.basename(name)
        if safe_name:
            raw = data[start:end]
            try:
                content = zlib.decompress(raw, -15) if method == 8 else raw
            except zlib.error:
                break
            with open(os.path.join(destination, safe_name), 'wb') as out:
                out.write(content)
            extracted += 1
        position = end

    return extracted


def analyze_truncated_firmware(file_path):
    """The installer metadata (hwid, Install, check.img) usually comes first, so it survives a cut-off download."""
    workdir = tempfile.mkdtemp(prefix="partial_")
    try:
        if not extract_complete_entries(file_path, workdir):
            return [], "extract_failed", "truncated: no complete entries"
        hardware_ids = sorted(set(check_firmware_compatibility(workdir)))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    if hardware_ids:
        return hardware_ids, "ok", None
    return [], "extract_failed", "truncated: the intact part has no hardware IDs"


def analyze_firmware(file_path, integrity=None):
    """Returns (hardware_ids, status, error) where status is "ok", "no_ids" or "extract_failed".
    Never modifies file_path; everything is unpacked in temporary folders."""
    if integrity and integrity.get("status") == "truncated":
        return analyze_truncated_firmware(file_path)

    try:
        zip_files, extraction_path = extract_if_zip(file_path)
    except ExtractionError as err:
        return [], "extract_failed", str(err)

    if extraction_path is not None:
        # A zip can hold several firmwares; combine what each one supports
        hardware_ids, statuses, errors = set(), [], []
        try:
            for file in zip_files:
                member_ids, member_status, member_error = analyze_firmware(file)
                hardware_ids.update(member_ids)
                statuses.append(member_status)
                if member_error:
                    errors.append(f"{os.path.basename(file)}: {member_error}")
        finally:
            clean_tmp(extraction_path)

        if hardware_ids:
            return sorted(hardware_ids), "ok", None
        if statuses and all(status == "extract_failed" for status in statuses):
            return [], "extract_failed", "; ".join(errors)
        return [], "no_ids", None

    try:
        hardware_ids = extract_firmware(file_path)
    except ExtractionError as err:
        return [], "extract_failed", str(err)

    return (hardware_ids, "ok", None) if hardware_ids else ([], "no_ids", None)


def save_result(firmware_file, result):
    with firmware_processing_lock:
        firmware_json = get_firmware_json()
        firmware_json[firmware_file] = result
        save_firmware_json(firmware_json)


def process_firmware_threaded(firmware_file, file_path, previous=None, integrity=None):
    print(f"Processing: {firmware_file}")
    hardware_ids, status, error = analyze_firmware(file_path, integrity)

    result = {
        "hardware_ids": hardware_ids,
        "hardware": classify_hardware_ids(hardware_ids),
        "status": status,
        "extractor_version": EXTRACTOR_VERSION,
        "processed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if integrity and integrity.get("status") == "truncated":
        # Partial result: the file is incomplete, so this is only what its intact part shows
        result["truncated"] = True
    if status == "extract_failed":
        result["error"] = error
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


def needs_processing(result, platform):
    """Whether a firmware should be (re)analyzed, given its stored result and detected platform."""
    if not result:
        return True
    status = result.get("status")
    if status == "ok":
        return False
    if status == "not_dahua":
        # Only if platform detection has since changed its mind
        return platform == "dahua"
    if result.get("extractor_version", 1) < EXTRACTOR_VERSION:
        return True
    if status == "extract_failed":
        return result.get("attempts", 1) < MAX_EXTRACT_ATTEMPTS
    return False
