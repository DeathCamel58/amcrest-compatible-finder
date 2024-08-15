import json
import os
import threading

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


def save_cameras_json(data):
    json_file = f"cameras.json"
    with cameras_lock:
        with open(json_file, 'w') as f:
            json.dump(data, f, indent=4, sort_keys=True)


def get_firmware_json():
    json_file = f"firmware_compatible.json"
    data = {}
    if os.path.exists(json_file):
        with firmware_lock:
            with open(f"firmware_compatible.json", "r") as f:
                data = json.load(f)
    return data


def save_firmware_json(data):
    json_file = f"firmware_compatible.json"
    with firmware_lock:
        with open(json_file, 'w') as f:
            json.dump(data, f, indent=4, sort_keys=True)
