import json
import os
import shutil
import subprocess

from util.json_tools import save_firmware_json


def clean_tmp(path):
    try:
        shutil.rmtree(path)
    except Exception as e:
        print('Failed to delete %s. Reason: %s' % (path, e))

def get_extracted_firmware_compatibility(path):
    # TODO: Determine how NVR compatibility works
    compatible_ids = []

    files = os.listdir(path)
    if 'check.img' in files:
        print("Found check.img!")

        full_data = bytes

        with (open(f'{path}/check.img', 'rb') as f):
            content = f.read()

            data = content.split(b'{')
            if len(data) > 1:
                full_data = data[-1]

            data = full_data.split(b'}')
            if len(data) > 1:
                full_data = data[0]

        full_data = full_data.decode('utf-8')
        full_data = "{" + full_data + "}"

        full_data = json.loads(full_data)
        # print(full_data)

        if 'hwid' in full_data:
            print("Found hwid!")

            for hwid in full_data['hwid']:
                compatible_id = hwid.split(":")

                if len(compatible_id) > 1:
                    compatible_id = compatible_id[0]

                compatible_ids.append(compatible_id)

    return compatible_ids


def process_firmware_threaded(firmware_file, file_path, tmp_file_path, firmware_json):
    compatible_list = ['Unknown']

    shutil.copy(file_path, tmp_file_path)

    extracted_name = f"tmp/_{firmware_file}.extracted"
    try:
        subprocess.run(['/usr/bin/binwalk', '-e', tmp_file_path])

        # TODO: Process zip files somehow

        compatible_list = get_extracted_firmware_compatibility(extracted_name)
    except Exception as err:
        print(f"\t{err}")

    # Clean up the temporary files
    clean_tmp(extracted_name)
    os.remove(tmp_file_path)

    firmware_json[firmware_file] = compatible_list

    save_firmware_json(firmware_json)

    return compatible_list