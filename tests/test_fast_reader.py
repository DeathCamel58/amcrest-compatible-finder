import io
import os
import struct
import subprocess
import zipfile

from conftest import make_zip_bytes, to_dh_variant
from util import firmware_processing
from util.firmware_processing import analyze_firmware, read_hardware_ids, uimage_name

HWID = b'{"hwid": ["IPC-HDW1230S:01:02", "IPC-HFW1230S:01:02"]}'


def stored_zip_bytes(members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def uimage(name):
    header = struct.pack(">IIIIIIIBBBB", 0x27051956, 0, 0, 1000, 0, 0, 0, 5, 2, 5, 1) + name.encode().ljust(32, b"\0")
    return header + b"\0" * 100


def test_reads_dh_firmware_without_binwalk(write_file, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("binwalk ran")))
    path = write_file("fw.bin", to_dh_variant(make_zip_bytes({"hwid": HWID, "romfs.img": os.urandom(5000)})))
    assert read_hardware_ids(path) == ["IPC-HDW1230S", "IPC-HFW1230S"]
    result = analyze_firmware(path)
    assert (result["hardware_ids"], result["status"], result["error"]) == (["IPC-HDW1230S", "IPC-HFW1230S"], "ok", None)


def test_reads_dhsp_ptz_variant(write_file):
    data = make_zip_bytes({"check.img": b'xx{"hwid": ["SD49825GBS2-A:1"]}'}).replace(b"PK\x03\x04", b"DHSP")
    assert read_hardware_ids(write_file("sd.bin", data)) == ["SD49825GBS2-A"]


def test_nested_deflated_and_stored(write_file):
    inner = to_dh_variant(make_zip_bytes({"hwid": HWID}))
    other = to_dh_variant(make_zip_bytes({"Install": b'{"Devices": [["NVR4X-4KS2", 1]]}'}))
    deflated = make_zip_bytes({"fw.bin": inner, "README.txt": b"hi"})
    assert read_hardware_ids(write_file("a.zip", deflated)) == ["IPC-HDW1230S", "IPC-HFW1230S"]
    stored = stored_zip_bytes({"a.bin": inner, "b.bin": other})
    assert read_hardware_ids(write_file("b.zip", stored)) == ["IPC-HDW1230S", "IPC-HFW1230S", "NVR4X-4KS2"]


def test_uboot_name_only_when_nothing_else(write_file):
    with_ids = to_dh_variant(make_zip_bytes({"hwid": HWID, "u-boot.bin.img": uimage("3521boot")}))
    assert read_hardware_ids(write_file("a.bin", with_ids)) == ["IPC-HDW1230S", "IPC-HFW1230S"]
    only_uboot = to_dh_variant(make_zip_bytes({"u-boot.bin.img": uimage("NVR4X-4KS2/L")}))
    assert read_hardware_ids(write_file("b.bin", only_uboot)) == ["NVR4X-4KS2/L"]


def test_uimage_name():
    assert uimage_name(b"junk" + uimage("NVR4X-S2")) == "NVR4X-S2"
    assert uimage_name(b"no header here") is None


def test_not_a_container(write_file):
    assert read_hardware_ids(write_file("x.bin", b"\x00" * 1000)) is None


def test_container_without_ids_falls_back_to_binwalk(write_file, monkeypatch):
    calls = []
    monkeypatch.setattr(firmware_processing, "extract_firmware", lambda path: calls.append(path) or {
        "hardware_ids": ["FROM-BINWALK"], "hardware_sources": [], "packages": [], "package_format": "other"})
    monkeypatch.setattr(firmware_processing, "extract_if_zip", lambda path: ([], None))
    path = write_file("x.bin", to_dh_variant(make_zip_bytes({"romfs.img": b"data"})))
    result = analyze_firmware(path)
    assert (result["hardware_ids"], result["status"]) == (["FROM-BINWALK"], "ok")
    assert calls == [path]


def test_cut_off_member_is_ignored(write_file):
    data = to_dh_variant(make_zip_bytes({"hwid": HWID, "big.img": os.urandom(50_000)}))
    assert read_hardware_ids(write_file("cut.bin", data[: len(data) // 2])) == ["IPC-HDW1230S", "IPC-HFW1230S"]


def test_chip_named_uboot_is_not_a_hardware_id(write_file):
    data = to_dh_variant(make_zip_bytes({"u-boot.bin.img": uimage("ss528V100")}))
    assert read_hardware_ids(write_file("a.bin", data)) == []


def test_results_are_saved_in_batches(monkeypatch):
    from util.json_tools import get_firmware_json
    monkeypatch.setattr(firmware_processing, "SAVE_EVERY", 3)
    monkeypatch.setattr(firmware_processing, "SAVE_SECONDS", 10 ** 6)
    firmware_processing.flush_results()
    firmware_processing.save_result("a.bin", {"status": "ok"})
    firmware_processing.save_result("b.bin", {"status": "ok"})
    assert get_firmware_json() == {}
    firmware_processing.save_result("c.bin", {"status": "no_ids"})
    assert sorted(get_firmware_json()) == ["a.bin", "b.bin", "c.bin"]
    firmware_processing.save_result("d.bin", {"status": "ok"})
    firmware_processing.flush_results()
    assert "d.bin" in get_firmware_json()


def test_recent_failure_is_not_retried():
    from datetime import datetime, timedelta, timezone
    from util.firmware_processing import EXTRACTOR_VERSION, needs_processing
    now = datetime.now(timezone.utc)
    failed = {"status": "extract_failed", "extractor_version": EXTRACTOR_VERSION, "attempts": 1}
    assert not needs_processing({**failed, "processed_at": now.isoformat()}, "dahua")
    assert needs_processing({**failed, "processed_at": (now - timedelta(days=2)).isoformat()}, "dahua")
    assert needs_processing(failed, "dahua")
