from conftest import make_zip_bytes, to_dh_variant
from util.file_integrity import check_integrity

MEMBERS = {"Install": b"install script", "kernel.img": bytes(range(256)) * 40, "romfs.img": b"r" * 5000}


def test_complete_zip(write_file):
    assert check_integrity(write_file("a.zip", make_zip_bytes(MEMBERS))) == {"status": "ok"}


def test_tail_cut_off(write_file):
    data = make_zip_bytes(MEMBERS)
    result = check_integrity(write_file("a.zip", data[:len(data) // 2]))
    assert result["status"] == "truncated"
    assert "missing" in result["reason"]


def test_end_record_cut_off(write_file):
    data = make_zip_bytes(MEMBERS)
    result = check_integrity(write_file("a.zip", data[:-5]))
    assert result["status"] == "truncated"


def test_dh_variant_zip(write_file):
    data = to_dh_variant(make_zip_bytes(MEMBERS))
    assert data[:4] == b"DH\x03\x04"
    assert b"PK\x05\x06" in data
    assert check_integrity(write_file("a.bin", data)) == {"status": "ok"}


def test_dh_variant_truncated(write_file):
    data = to_dh_variant(make_zip_bytes(MEMBERS))
    assert check_integrity(write_file("a.bin", data[:len(data) // 2]))["status"] == "truncated"


def test_empty_file(write_file):
    assert check_integrity(write_file("a.bin", b"")) == {"status": "truncated", "reason": "empty file"}


def test_non_zip_file(write_file):
    result = check_integrity(write_file("a.dav", b"\x01\x02\x03\x04" + b"\x00" * 100))
    assert result["status"] == "unverified"
