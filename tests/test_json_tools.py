from util.json_tools import normalize_firmware_result


def test_old_list_format():
    result = normalize_firmware_result(["IPC-HDW2431T-AS-S2", "HCVR5x04-S2", "0800425346118427", "DAHUA"])
    assert result["hardware_ids"] == ["IPC-HDW2431T-AS-S2", "HCVR5x04-S2", "0800425346118427", "DAHUA"]
    assert result["status"] == "ok"
    assert result["extractor_version"] == 1
    assert result["hardware"] == {"models": ["IPC-HDW2431T-AS-S2"], "boards": ["HCVR5x04-S2"],
                                  "hwids": ["0800425346118427"], "ignored": ["DAHUA"]}


def test_old_empty_list():
    result = normalize_firmware_result([])
    assert result["status"] == "no_ids"
    assert result["hardware"] == {"models": [], "boards": [], "hwids": [], "ignored": []}


def test_new_format_without_hardware_block():
    value = {"hardware_ids": ["ASI7213X"], "status": "ok", "extractor_version": 3}
    result = normalize_firmware_result(value)
    assert result["hardware"]["models"] == ["ASI7213X"]
    assert result["extractor_version"] == 3
    assert "hardware" not in value  # input not mutated


def test_existing_hardware_block_kept():
    value = {"hardware_ids": ["ASI7213X"], "status": "ok", "hardware": {"models": ["custom"]}}
    assert normalize_firmware_result(value)["hardware"] == {"models": ["custom"]}


def test_missing_hardware_ids():
    assert normalize_firmware_result({"status": "error"})["hardware"]["models"] == []


def test_json_file_lock_is_reentrant_and_cross_process(tmp_path):
    import fcntl
    from util.json_tools import JsonFileLock, json_lock
    lock = JsonFileLock("cameras.json")
    with lock:
        with json_lock("cameras.json"):  # a save inside the read-modify-write doesn't deadlock
            pass
        # Another process (simulated with a fresh file handle) can't take the lock meanwhile
        with open("cameras.json.lock", "a") as other:
            try:
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked_out = False
            except BlockingIOError:
                locked_out = True
    assert locked_out
