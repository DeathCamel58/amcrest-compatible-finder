import copy
import json
import os
import re
import shutil
import subprocess
import threading

from util.json_tools import save_firmware_json, get_firmware_json


firmware_processing_lock = threading.Lock()


def clean_tmp(path):
    try:
        shutil.rmtree(path)
    except Exception as e:
        print('Failed to delete %s. Reason: %s' % (path, e))


def extract_firmware(path):
    directory, file_name = os.path.split(path)
    extracted_name = f"_{file_name}.extracted"
    extracted_name = os.path.join(directory, extracted_name)
    compatible_list = []
    try:
        subprocess.run(['/usr/bin/binwalk', '-e', path], stdout=subprocess.DEVNULL)

        if os.path.isdir(extracted_name):
            compatible_list = get_extracted_firmware_compatibility(extracted_name)
    except Exception as err:
        print(f"\t{err}")
    return extracted_name, compatible_list


def get_extracted_firmware_compatibility(path):
    # TODO: Determine how NVR compatibility works
    compatible_ids = []

    files = os.listdir(path)

    # Some firmwares store the IDs in check.img
    if 'check.img' in files:
        print("Found check.img!")

        data = bytes

        with (open(f'{path}/check.img', 'rb') as f):
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

    # Fallback to getting the ID from u-boot.bin.img
    # NOTE: This sometimes is the ID that can be obtained from HTTP at:
    #        - /cgi-bin/magicBox.cgi?action=getSystemInfoNew
    #        - /cgi-bin/magicBox.cgi?action=getSystemInfo
    #       Although usually it's something different
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


def extract_if_zip(path):
    """Determine if this is a zip file, and if so, extract it, returning the array of paths"""
    zip_process = subprocess.run(['file', path], capture_output=True)
    zip_process.stdout = zip_process.stdout.decode('utf-8')
    files = []
    if "Zip archive" in zip_process.stdout:
        firmware_file_name = path[9:]
        extraction_path = f"tmp/{firmware_file_name}_extracted"

        zip_process = subprocess.run(['unzip', path, '-d', extraction_path], capture_output=True, cwd=f"{os.path.dirname(os.path.realpath(__file__))}/../")

        if zip_process.returncode != 0:
            clean_tmp(extraction_path)
            return files

        lines = zip_process.stdout.decode('utf-8')
        lines = lines.split("\n")

        files = []
        for line in lines:
            file_name = line[13:]
            file_name = re.sub(r' *\n*$', '', file_name)
            if line.startswith("  inflating: "):
                files.append(f"{file_name}")
            elif line.startswith(" extracting: ") and not line.endswith("/\n"):
                files.append(f"{file_name}")

        # TODO: Check that the files array does not contain folders (ending in `/`)
        print(files)

    return files


def process_firmware_threaded(firmware_file, file_path, tmp_file_path):
    compatible_list = []

    firmware_json = get_firmware_json()

    # Only process firmwares without data
    if firmware_file not in firmware_json or (firmware_json[firmware_file] == []):
        print(f"Processing: {firmware_file}")

        zip_files = extract_if_zip(file_path)
        if len(zip_files) > 0:
            for file in zip_files:
                firmware_file_name = file[4:]
                tmp_file_path_extracted = f"tmp/{firmware_file_name}_extracted"
                tmp_compatible_list = process_firmware_threaded(firmware_file, file, tmp_file_path_extracted)
                if len(tmp_compatible_list) > 0:
                    compatible_list = tmp_compatible_list

            # Clean up the temporary files
            clean_tmp(f"tmp/{file_path[9:]}_extracted")

            with firmware_processing_lock:
                firmware_json = get_firmware_json()
                firmware_json_original = copy.deepcopy(firmware_json)
                firmware_json[firmware_file] = compatible_list
                if firmware_json_original != firmware_json:
                    save_firmware_json(firmware_json)

        else:
            shutil.copy(file_path, tmp_file_path)

            extracted_name, new_compatible_list = extract_firmware(tmp_file_path)
            if new_compatible_list:
                compatible_list = new_compatible_list

            # Clean up the temporary files
            if os.path.exists(extracted_name):
                clean_tmp(extracted_name)
            os.remove(tmp_file_path)

            with firmware_processing_lock:
                firmware_json = get_firmware_json()
                firmware_json_original = copy.deepcopy(firmware_json)
                firmware_json[firmware_file] = compatible_list
                if firmware_json_original != firmware_json:
                    save_firmware_json(firmware_json)
    else:
        compatible_list = firmware_json[firmware_file]

    return compatible_list
