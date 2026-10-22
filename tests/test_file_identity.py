import os

import pytest

from util.file_identity import COPY_MARKERS, assign_duplicates, get_file_hashes, needs_hashing


@pytest.mark.parametrize("name", ["General_NVR_V4.001.230408+(6).bin", "General_NVR_V4.001.210816+.bin",
                                  "General_NVR_V4.001 (2).bin", "317700_General_NVR_V4.001.bin"])
def test_copy_markers_match(name):
    assert COPY_MARKERS.search(name)


@pytest.mark.parametrize("name", ["General_NVR_V4.001.230408.bin", "IPC+HDW_V2.bin", "IPC-144M.bin",
                                  "DH_IPC_(2)_V2.bin", "123_short.bin"])
def test_copy_markers_no_match(name):
    assert not COPY_MARKERS.search(name)


def entry(sha="h1", listings=None):
    value = {"file_hashes": {"sha256": sha}}
    if listings is not None:
        value["listings"] = listings
    return value


def test_main_entry_without_copy_markers():
    names = ["General_X_V2.230408+(6).bin", "General_X_V2.230408+.bin", "317700_General_X_V2.230408.bin",
             "General_X_V2.230408_a_much_longer_name.bin"]
    cameras = {name: entry() for name in names}
    # Even with listings, a copy-marked name loses to an unmarked one
    cameras["317700_General_X_V2.230408.bin"]["listings"] = [{"vendor": "A"}]
    assert assign_duplicates(cameras, names) == 3
    main = "General_X_V2.230408_a_much_longer_name.bin"
    assert sorted(cameras[main]["aliases"]) == sorted(n for n in names if n != main)
    assert "duplicate_of" not in cameras[main]
    for name in names:
        if name != main:
            assert cameras[name]["duplicate_of"] == main
            assert "aliases" not in cameras[name]


def test_main_entry_prefers_listed_name():
    cameras = {"short.bin": entry(), "a_longer_listed_name.bin": entry(listings=[{"vendor": "V"}])}
    assign_duplicates(cameras, list(cameras))
    assert cameras["a_longer_listed_name.bin"]["aliases"] == ["short.bin"]
    assert cameras["short.bin"]["duplicate_of"] == "a_longer_listed_name.bin"


def test_then_shortest_name():
    cameras = {"bbbb.bin": entry(), "aa.bin": entry()}
    assign_duplicates(cameras, list(cameras))
    assert cameras["aa.bin"]["aliases"] == ["bbbb.bin"]


def test_unique_files_lose_stale_links():
    cameras = {"a.bin": {**entry("h1"), "aliases": ["old.bin"], "duplicate_of": "x.bin"},
               "b.bin": entry("h2"), "nohash.bin": {}}
    assert assign_duplicates(cameras, list(cameras)) == 0
    assert "aliases" not in cameras["a.bin"] and "duplicate_of" not in cameras["a.bin"]
    assert cameras["nohash.bin"] == {}


def test_get_file_hashes_and_needs_hashing(tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(b"abc")
    hashes = get_file_hashes(str(path))
    assert hashes["md5"] == "900150983cd24fb0d6963f7d28e17f72"
    assert hashes["sha256"] == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert hashes["size"] == 3
    assert not needs_hashing({"file_hashes": hashes}, str(path))
    assert needs_hashing({}, str(path))


def test_truncated_copy_becomes_duplicate_of_complete_file(tmp_path):
    from util.file_identity import HEAD_BYTES, assign_truncated_copies
    complete = os.urandom(HEAD_BYTES * 3)
    files = {"full.bin": complete, "cut.bin": complete[:HEAD_BYTES * 2], "other_cut.bin": os.urandom(HEAD_BYTES * 2)}
    cameras = {}
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
        cameras[name] = {"file_hashes": {"size": len(data)}}
    cameras["cut.bin"]["integrity"] = {"status": "truncated"}
    cameras["other_cut.bin"]["integrity"] = {"status": "truncated"}

    marked = assign_truncated_copies(cameras, list(files), lambda name: str(tmp_path / name))

    assert marked == 1
    assert cameras["cut.bin"]["duplicate_of"] == "full.bin" and cameras["cut.bin"]["truncated_copy"]
    assert cameras["full.bin"]["aliases"] == ["cut.bin"]
    assert "duplicate_of" not in cameras["other_cut.bin"]
    assert "head_md5" in cameras["full.bin"]["file_hashes"]
