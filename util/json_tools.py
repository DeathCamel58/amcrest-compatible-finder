import json
import os
import threading

from util.hardware import classify_hardware_ids

cameras_lock = threading.Lock()
firmware_lock = threading.Lock()


def get_cameras_json():
    json_file = f"cameras.json"
    data = {}
    if os.path.exists(json_file):
        with cameras_lock:
            with open(f"cameras.json", "r") as f:
                data = json.load(f)
    return data


def save_json_atomic(json_file, data):
    # Write to a temp file and rename it over the original, so a crash mid-save can't leave a truncated JSON
    # (the data for firmwares that vendors have since removed can't be re-scraped)
    temp_file = f"{json_file}.tmp"
    with open(temp_file, 'w') as f:
        json.dump(data, f, indent=4, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_file, json_file)


def save_cameras_json(data):
    json_file = f"cameras.json"
    with cameras_lock:
        save_json_atomic(json_file, data)


def normalize_firmware_result(value):
    # firmware_compatible.json used to map file name -> list of hardware IDs. Results from that version have no
    # status, so treat an empty list as "no_ids" from extractor version 1 (they get one retry with the new extractor)
    if isinstance(value, list):
        value = {"hardware_ids": value, "status": "ok" if value else "no_ids", "extractor_version": 1}
    # The same IDs sorted into models / boards / raw hwids / ignored tokens, so consumers don't each clean them up
    if "hardware" not in value:
        value = {**value, "hardware": classify_hardware_ids(value.get("hardware_ids") or [])}
    return value


def get_firmware_json():
    json_file = f"firmware_compatible.json"
    data = {}
    if os.path.exists(json_file):
        with firmware_lock:
            with open(f"firmware_compatible.json", "r") as f:
                data = json.load(f)
    return {name: normalize_firmware_result(value) for name, value in data.items()}


def get_hardware_ids(firmware_json, firmware_file):
    return (firmware_json.get(firmware_file) or {}).get("hardware_ids") or []


def save_firmware_json(data):
    json_file = f"firmware_compatible.json"
    with firmware_lock:
        save_json_atomic(json_file, data)
