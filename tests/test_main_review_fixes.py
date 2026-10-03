import json
import os

import pytest
from requests import HTTPError

import main
from util import download_firmware as dl
from util.file_identity import assign_duplicates


@pytest.fixture(autouse=True)
def fresh_registries(monkeypatch, tmp_path):
    # Never the real folders next to the firmware library
    monkeypatch.setattr(main, "NON_FIRMWARE_DIR", str(tmp_path / "non-firmware"))
    monkeypatch.setattr(main, "TRUNCATED_DIR", str(tmp_path / "truncated"))
    # The registries are cached per path; each test runs in its own temp directory
    monkeypatch.setattr(dl, "_registries", {})
    monkeypatch.setattr(dl, "_mega_quota_exceeded", dl.threading.Event())
    monkeypatch.setattr(main, "_replaced_this_run", set())
    yield


def write(path, data=b"x"):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


# --- #6 resolve_file_name ----------------------------------------------------------------------------------------

def listing(url, size=None):
    return {"firmware_latest": url, "firmware_size": size}


def test_resolve_new_file_keeps_name():
    assert main.resolve_file_name(listing("https://a.example/fw.bin", 5), "firmware_latest") == "fw.bin"


def test_resolve_different_size_gets_hashed_name():
    write("firmware/fw.bin", b"12345")
    url = "https://b.example/fw.bin"
    name = main.resolve_file_name(listing(url, 999), "firmware_latest")
    assert name == main.hashed_file_name("fw.bin", url)
    assert name.startswith("fw-") and name.endswith(".bin")


def test_resolve_same_size_keeps_name():
    write("firmware/fw.bin", b"12345")
    assert main.resolve_file_name(listing("https://b.example/fw.bin", 5), "firmware_latest") == "fw.bin"


def test_resolve_reuses_existing_hashed_name_even_without_size():
    url = "https://b.example/fw.bin"
    write("firmware/fw.bin", b"12345")
    write(f"firmware/{main.hashed_file_name('fw.bin', url)}", b"other")
    assert main.resolve_file_name(listing(url), "firmware_latest") == main.hashed_file_name("fw.bin", url)


# --- #10 rejected / failed download registries -------------------------------------------------------------------

def test_rejected_url_and_name():
    assert not dl.is_rejected("https://x/a.zip", "a.zip")
    dl.record_rejected_download("https://x/a.zip", "a.zip", "software")
    assert dl.is_rejected("https://x/a.zip")
    assert dl.is_rejected(file_name="a.zip")
    assert dl.skip_reason("https://x/a.zip") == "not firmware"
    # Saved to disk, so a later run sees it
    with open(dl.REJECTED_DOWNLOADS_FILE) as f:
        assert json.load(f)["https://x/a.zip"]["reason"] == "software"


def test_failed_url_skipped_after_two_runs(monkeypatch):
    url = "https://x/gone.bin"
    dl.record_failed_download(url, "HTTP 404")
    dl.record_failed_download(url, "HTTP 404")  # same run counts once
    assert dl.skip_reason(url) is None
    monkeypatch.setattr(dl, "RUN_ID", "later run")
    dl.record_failed_download(url, "HTTP 404")
    assert dl.skip_reason(url).startswith("failed in 2 runs")
    dl.clear_failed_download(url)
    assert dl.skip_reason(url) is None


def test_permanent_404_recorded_and_not_retried():
    calls = []

    class Response:
        status_code = 404

    def downloader(url, part_name):
        calls.append(url)
        raise HTTPError("404 Not Found", response=Response())

    assert dl.download_firmware("https://x/gone.bin", "firmware/gone.bin", downloader) is None
    assert len(calls) == 1
    assert dl._load_registry(dl.FAILED_DOWNLOADS_FILE)["https://x/gone.bin"]["reason"] == "HTTP 404"


def test_empty_download_gives_up_after_two():
    calls = []

    def downloader(url, part_name):
        calls.append(url)
        write(part_name, b"")

    assert dl.download_firmware("https://x/empty.bin", "firmware/empty.bin", downloader) is None
    assert len(calls) == dl.EMPTY_DOWNLOAD_ATTEMPTS
    assert not os.path.exists("firmware/empty.bin.part")


def test_mega_quota_skips_rest_of_run():
    assert dl.skip_reason("https://mega.nz/file/abc") is None
    dl._note_mega_quota(Exception("509 Bandwidth Limit Exceeded"), "https://mega.nz/file/abc")
    assert dl.skip_reason("https://mega.nz/file/abc") == "MEGA quota exceeded this run"
    assert dl.skip_reason("https://example.com/fw.bin") is None
    assert dl.download_firmware("https://mega.nz/file/abc", "firmware/a.bin", lambda u, p: None) is None


def test_thread_skips_failed_url_but_records_listing(monkeypatch):
    url = "https://x/gone.bin"
    for run in ("run 1", "run 2"):
        monkeypatch.setattr(dl, "RUN_ID", run)
        dl.record_failed_download(url, "HTTP 404")

    def no_download(*args, **kwargs):
        raise AssertionError("should not download")

    monkeypatch.setattr(main, "download_firmware", no_download)
    main.download_firmware_thread({"vendor": "Acme", "source": "https://x/", "firmware_latest": url,
                                   "camera_name": ["IPC-1"], "firmware_notes": None}, "firmware_latest")
    assert "gone.bin" in main.get_cameras_json()


def test_thread_skips_rejected_file_without_entry(monkeypatch):
    dl.record_rejected_download("https://x/tool.zip", "tool.zip", "software")
    monkeypatch.setattr(main, "download_firmware", lambda *a, **k: None)
    # Another listing of the same file name from a different URL
    main.download_firmware_thread({"vendor": "Acme", "source": "https://y/", "firmware_latest": "https://y/tool.zip",
                                   "camera_name": ["IPC-1"], "firmware_notes": None}, "firmware_latest")
    assert "tool.zip" not in main.get_cameras_json()


def test_thread_records_rejected_download(monkeypatch):
    def fake_download(url, file_name, downloader=None):
        write(file_name, b"MZ\x00\x00")
        return file_name

    monkeypatch.setattr(main, "download_firmware", fake_download)
    main.download_firmware_thread({"vendor": "Acme", "source": "https://x/", "firmware_latest": "https://x/fw2.bin",
                                   "camera_name": ["IPC-1"], "firmware_notes": None}, "firmware_latest")
    assert not os.path.exists("firmware/fw2.bin")
    assert os.path.exists(os.path.join(main.NON_FIRMWARE_DIR, "fw2.bin"))
    assert dl.is_rejected("https://x/fw2.bin")
    assert "fw2.bin" not in main.get_cameras_json()


# --- #11 main entry preference -----------------------------------------------------------------------------------

def hashed(entry=None):
    return {"file_hashes": {"sha256": "aa"}, **(entry or {})}


def test_duplicates_prefer_archived_then_analysed():
    cameras = {"a.bin": hashed(), "b_long_name.bin": hashed({"archive_url": "https://archive.org/x"}),
               "c_longer_name.bin": hashed()}
    assign_duplicates(cameras, list(cameras), {"c_longer_name.bin"})
    assert cameras["b_long_name.bin"]["aliases"] == ["c_longer_name.bin", "a.bin"]
    assert cameras["a.bin"]["duplicate_of"] == "b_long_name.bin"


def test_duplicates_pending_archive_not_preferred():
    cameras = {"a.bin": hashed(), "b_long.bin": hashed({"archive_url": "https://x", "archive_pending": True}),
               "c_longest.bin": hashed()}
    assign_duplicates(cameras, list(cameras), {"c_longest.bin"})
    assert cameras["c_longest.bin"]["aliases"] == ["a.bin", "b_long.bin"]


def test_duplicates_default_order_unchanged():
    cameras = {"a.bin": hashed(), "a (1).bin": hashed()}
    assign_duplicates(cameras, list(cameras))
    assert cameras["a.bin"]["aliases"] == ["a (1).bin"]


# --- #21 parse_args ----------------------------------------------------------------------------------------------

ALL = ["download", "enrich", "process", "archive", "validate"]


def test_parse_args():
    assert main.parse_args([]) == (ALL, set(), set())
    assert main.parse_args(["download", "--only", "EmpireTech, Dahua"]) == (["download"], {"empiretech", "dahua"}, set())
    assert main.parse_args(["--skip", "Wayback", "enrich"]) == (["enrich"], set(), {"wayback"})


def test_parse_args_stages_run_in_pipeline_order():
    assert main.parse_args(["validate", "download", "process"])[0] == ["download", "process", "validate"]
    assert main.parse_args(["archive", "archive"])[0] == ["archive"]
    assert main.parse_args(["check-links"])[0] == ["check-links"]


def test_parse_args_groups_and_ranges():
    assert main.parse_args(["analyse"])[0] == ["enrich", "process", "validate"]
    assert main.parse_args(["update"])[0] == ["download", "enrich", "process", "validate"]
    assert main.parse_args(["--from", "process"])[0] == ["process", "archive", "validate"]
    assert main.parse_args(["--to", "enrich"])[0] == ["download", "enrich"]
    assert main.parse_args(["all", "check-links", "--from", "archive"])[0] == ["archive", "check-links", "validate"]


@pytest.mark.parametrize("argv", [["download", "--only"], ["--skip"], ["--only", "--skip", "x"], ["bogus"],
                                  ["--from", "nope"], ["--from", "validate", "--to", "download"]])
def test_parse_args_errors(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        main.parse_args(argv)
    assert exc.value.code == 2
    assert "usage" in capsys.readouterr().out


def test_parse_args_help(capsys):
    with pytest.raises(SystemExit) as exc:
        main.parse_args(["--help"])
    assert exc.value.code == 0
    assert "check-links" in capsys.readouterr().out


# --- #22 version split -------------------------------------------------------------------------------------------

def test_split_three_part_version():
    firmware = {"firmware_version": "V2.06.39.R.160822"}
    main.split_version_build_date(firmware)
    assert firmware == {"firmware_version": "V2.06.39.R", "release_date": "2016-08-22"}


def test_tidy_entry_splits_stored_versions():
    entry = {"firmware_version": "V2.06.39.R.160822",
             "listings": [{"firmware_version": "V2.800.0000032.0.R.250224"}]}
    main.tidy_entry("fw.bin", entry)
    assert entry["firmware_version"] == "V2.06.39.R"
    assert entry["listings"][0]["firmware_version"] == "V2.800.0000032.0.R"
    assert entry["listings"][0]["release_date"] == "2025-02-24"


# --- #20 temp space ----------------------------------------------------------------------------------------------

def test_temp_free_space_is_minimum(monkeypatch):
    os.makedirs("tmp")
    frees = {main.tempfile.gettempdir(): 100, os.path.realpath("tmp"): 40}
    monkeypatch.setattr(main.shutil, "disk_usage", lambda path: type("U", (), {"free": frees[path]}))
    assert main.temp_free_space() == 40


# --- #5 removed entries are kept -----------------------------------------------------------------------------------

def test_remove_non_firmware_keeps_entries_and_other_results():
    write("firmware/Manual.pdf", b"%PDF-1.4")
    main.save_cameras_json({"Manual.pdf": {"url": "https://x/Manual.pdf"}})
    main.save_firmware_json({"Manual.pdf": {"status": "error"}, "orphan.bin": {"status": "ok"}})
    main.remove_non_firmware()
    assert "Manual.pdf" not in main.get_cameras_json()
    assert set(main.get_firmware_json()) == {"orphan.bin"}
    with open(os.path.join(main.NON_FIRMWARE_DIR, main.REMOVED_ENTRIES_FILE)) as f:
        saved = json.load(f)["Manual.pdf"]
    assert saved["cameras"] == {"url": "https://x/Manual.pdf"}
    assert saved["firmware_compatible"]
    assert dl.is_rejected("https://x/Manual.pdf")


# --- #1 / #14 truncated replacement --------------------------------------------------------------------------------

def test_replace_truncated_same_size_flags_vendor_copy(monkeypatch):
    write("firmware/t.bin", b"12345")
    main.save_cameras_json({"t.bin": {"integrity": {"status": "truncated"}}})
    monkeypatch.setattr(main, "download_firmware", lambda *a, **k: pytest.fail("should not download"))
    assert main.replace_truncated_file(listing("https://x/t.bin", 5), "firmware_latest", "firmware/t.bin") is False
    assert main.get_cameras_json()["t.bin"]["integrity"]["vendor_copy_truncated"] is True


def test_replace_truncated_unknown_size_does_not_flag(monkeypatch):
    write("firmware/t.bin", b"12345")
    write("firmware/t.bin.redownload", b"stale")
    main.save_cameras_json({"t.bin": {"integrity": {"status": "truncated"}}})
    seen = []

    def fake_download(url, file_name, downloader=None):
        seen.append(os.path.exists(file_name))
        return None

    monkeypatch.setattr(main, "download_firmware", fake_download)
    assert main.replace_truncated_file(listing("https://x/t.bin"), "firmware_latest", "firmware/t.bin") is False
    assert seen == [False]  # the stale .redownload was removed first
    assert "vendor_copy_truncated" not in main.get_cameras_json()["t.bin"]["integrity"]
    assert os.path.exists("firmware/t.bin")


def test_unique_destination_never_overwrites():
    first = main.unique_destination("out", "a.bin")
    write(first)
    second = main.unique_destination("out", "a.bin")
    assert second != first and not os.path.exists(second)


def test_archive_stops_after_pushback(monkeypatch, tmp_path):
    (tmp_path / "firmware").mkdir()
    for name in ("a.bin", "b.bin"):
        (tmp_path / "firmware" / name).write_bytes(b"x")
    calls = []

    def fake_archive(path, entry, analysis, aliases):
        calls.append(path)
        raise Exception("error uploading: Please reduce your request rate. - appears to be spam")

    monkeypatch.setattr(main, "archive_firmware", fake_archive)
    main.archive_stop.clear()
    main.archive_firmware_thread("a.bin")
    main.archive_firmware_thread("b.bin")
    assert calls == ["firmware/a.bin"]
    assert main.archive_stop.is_set()
    main.archive_stop.clear()
