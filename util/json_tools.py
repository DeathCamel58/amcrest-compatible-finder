import fcntl
import json
import os
import tempfile
import threading
from contextlib import contextmanager

from util.hardware import classify_hardware_ids

CAMERAS_JSON = "cameras.json"
FIRMWARE_JSON = "firmware_compatible.json"

# One lock per JSON file. Within the process an RLock (so a save inside json_lock() doesn't deadlock); across processes
# (e.g. a separate `download --only EmpireTech` run) an fcntl lock on "<file>.lock", taken by the outermost holder only
_locks = {}
_locks_guard = threading.Lock()


class _FileLock:
    def __init__(self, json_file):
        self.json_file = json_file
        self.thread_lock = threading.RLock()
        self.depth = 0
        self.handle = None

    def acquire(self):
        self.thread_lock.acquire()
        if self.depth == 0:
            directory = os.path.dirname(os.path.abspath(self.json_file))
            os.makedirs(directory, exist_ok=True)
            self.handle = open(f"{self.json_file}.lock", "a")
            fcntl.flock(self.handle, fcntl.LOCK_EX)
        self.depth += 1

    def release(self):
        self.depth -= 1
        if self.depth == 0:
            fcntl.flock(self.handle, fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None
        self.thread_lock.release()


def _get_lock(json_file):
    key = os.path.abspath(json_file)
    with _locks_guard:
        if key not in _locks:
            _locks[key] = _FileLock(json_file)
        return _locks[key]


@contextmanager
def json_lock(json_file):
    """Hold a JSON file's lock (threads and other processes) across a read-modify-write:

        with json_lock(CAMERAS_JSON):
            data = get_cameras_json()
            ...
            save_cameras_json(data)
    """
    lock = _get_lock(json_file)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def load_json(json_file):
    if not os.path.exists(json_file):
        return {}
    with json_lock(json_file):
        with open(json_file, "r") as f:
            return json.load(f)


def save_json_atomic(json_file, data):
    """Write to a temp file in the same directory and rename it over the original, so a crash mid-save can't leave a
    truncated JSON (the data for firmwares that vendors have since removed can't be re-scraped). The temp name is
    unique per call, so concurrent writers never share one."""
    with json_lock(json_file):
        directory = os.path.dirname(os.path.abspath(json_file))
        fd, temp_file = tempfile.mkstemp(prefix=f".{os.path.basename(json_file)}.", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w") as f:
                # One-space indent: still one field per line for readable diffs, but firmware_compatible.json stays well
                # under GitHub's 100 MiB file limit (4 spaces made it 104 MB once partition tables were added)
                json.dump(data, f, indent=1, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_file, json_file)
        except BaseException:
            if os.path.exists(temp_file):
                os.remove(temp_file)
            raise


def get_cameras_json():
    return load_json(CAMERAS_JSON)


def save_cameras_json(data):
    save_json_atomic(CAMERAS_JSON, data)


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
    return {name: normalize_firmware_result(value) for name, value in load_json(FIRMWARE_JSON).items()}


def get_hardware_ids(firmware_json, firmware_file):
    return (firmware_json.get(firmware_file) or {}).get("hardware_ids") or []


def save_firmware_json(data):
    save_json_atomic(FIRMWARE_JSON, data)
