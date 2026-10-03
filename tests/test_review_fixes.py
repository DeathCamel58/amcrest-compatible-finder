import json
import os
import threading

import pytest

from conftest import make_zip_bytes, to_dh_variant


# util/json_tools.py: per-process temp files, locking, atomic saves

def test_save_json_atomic_writes_and_leaves_no_temp_files(tmp_path):
    from util.json_tools import save_json_atomic, load_json
    target = tmp_path / "cameras.json"
    save_json_atomic(str(target), {"b": 1, "a": 2})
    assert load_json(str(target)) == {"a": 2, "b": 1}
    leftovers = [p for p in os.listdir(tmp_path) if p.endswith(".tmp")]
    assert leftovers == []


def test_save_json_atomic_keeps_the_original_when_serialising_fails(tmp_path):
    from util.json_tools import save_json_atomic, load_json
    target = tmp_path / "data.json"
    save_json_atomic(str(target), {"ok": True})
    with pytest.raises(TypeError):
        save_json_atomic(str(target), {"bad": object()})
    assert load_json(str(target)) == {"ok": True}
    assert [p for p in os.listdir(tmp_path) if p.endswith(".tmp")] == []


def test_json_lock_is_reentrant_and_prevents_lost_updates(tmp_path):
    from util.json_tools import json_lock, save_json_atomic, load_json
    target = str(tmp_path / "counter.json")
    save_json_atomic(target, {"n": 0})

    def increment():
        for _ in range(20):
            # Read-modify-write; save_json_atomic takes the same lock again inside
            with json_lock(target):
                data = load_json(target)
                data["n"] += 1
                save_json_atomic(target, data)

    threads = [threading.Thread(target=increment) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert load_json(target) == {"n": 100}


def test_get_and_save_cameras_json_round_trip():
    from util.json_tools import get_cameras_json, save_cameras_json
    assert get_cameras_json() == {}
    save_cameras_json({"x.bin": {"url": "u"}})
    assert get_cameras_json() == {"x.bin": {"url": "u"}}
    assert os.path.exists("cameras.json.lock")


# brands/RVI_Files.py and brands/RhinoFiles.py: misses above the highest found ID aren't cached

def test_rvi_prune_tail_drops_misses_above_highest_found():
    from brands.RVI_Files import prune_tail
    cache = {10: "/a.bin", 11: None, 20: "/b.bin", 21: None, 22: None}
    assert prune_tail(cache) == {10: "/a.bin", 11: None, 20: "/b.bin"}


def test_rvi_prune_tail_with_no_files_keeps_everything():
    from brands.RVI_Files import prune_tail
    assert prune_tail({1: None, 2: None}) == {1: None, 2: None}


def test_rvi_ids_to_check_include_the_tail_again(monkeypatch):
    from brands import RVI_Files
    monkeypatch.setattr(RVI_Files, "ID_RANGES", [(1, 5)])
    monkeypatch.setattr(RVI_Files, "NEW_ID_HEADROOM", 3)
    cache = RVI_Files.prune_tail({1: None, 2: None, 3: None, 4: None, 5: "/x.bin", 6: None, 7: None})
    assert RVI_Files.get_ids_to_check(cache) == [6, 7, 8]


def test_rhino_cache_round_trip_prunes_tail(monkeypatch, tmp_path):
    from brands import RhinoFiles
    monkeypatch.setattr(RhinoFiles, "cache_path", str(tmp_path / "tmp" / "rhino.json"))
    RhinoFiles.save_cache({1: "a.bin", 2: None, 3: "b.bin", 4: None, 5: None}, {9})
    files, failed = RhinoFiles.load_cache()
    assert files == {1: "a.bin", 2: None, 3: "b.bin"}
    assert failed == {9}


# util/firmware_processing.py: streaming extraction of truncated files

def test_extract_complete_entries_streams_a_truncated_dh_file(tmp_path):
    from util.firmware_processing import extract_complete_entries
    data = to_dh_variant(make_zip_bytes({
        "hwid": b'{"hwid": ["IPC-HDW1230S:01:02"]}',
        "Install": b"x" * 1000,
        "romfs-x.squashfs.img": os.urandom(200_000),
    }))
    truncated = tmp_path / "cut.bin"
    truncated.write_bytes(data[: len(data) // 2])
    out = tmp_path / "out"
    out.mkdir()
    assert extract_complete_entries(str(truncated), str(out)) == 2
    assert (out / "hwid").read_bytes() == b'{"hwid": ["IPC-HDW1230S:01:02"]}'
    assert (out / "Install").read_bytes() == b"x" * 1000
    assert not (out / "romfs-x.squashfs.img").exists()


def test_extract_complete_entries_skips_oversized_members(tmp_path, monkeypatch):
    from util import firmware_processing
    monkeypatch.setattr(firmware_processing, "MAX_PARTIAL_MEMBER_SIZE", 100)
    data = make_zip_bytes({"hwid": b"small", "big.img": b"y" * 5000, "Install": b"z"})
    path = tmp_path / "f.bin"
    path.write_bytes(data)
    out = tmp_path / "out"
    out.mkdir()
    assert firmware_processing.extract_complete_entries(str(path), str(out)) == 2
    assert sorted(os.listdir(out)) == ["Install", "hwid"]


# brands/Amcrest.py: links that can't be file downloads are dropped

@pytest.mark.parametrize("href, expected", [
    ("https://s3.amazonaws.com/amcrest-firmwares/NV4432E-HS_regular_20180105_10002_*.bin", None),
    ("https://amcrest.com/downloads/ATC-1202W.zip", None),
    ("https://s3.console.aws.amazon.com/s3/buckets/amcrest", None),
    ("s3://amcrest-firmwares/a+(1).bin", "s3://amcrest-firmwares/a+(1).bin"),
    ("/files/x.bin", "https://amcrest.com/files/x.bin"),
    ("https://amcrest-firmwares.s3.amazonaws.com/x.bin", "https://amcrest-firmwares.s3.amazonaws.com/x.bin"),
    (None, None),
    ("", None),
])
def test_amcrest_clean_download_link(href, expected):
    from brands.Amcrest import clean_download_link
    assert clean_download_link(href) == expected


# util/http.py: minimum-rate guard for streamed downloads

class FakeResponse:
    def __init__(self, chunks):
        self.chunks = chunks

    def iter_content(self, chunk_size=None):
        yield from self.chunks


class FakeClock:
    def __init__(self, times):
        self.times = iter(times)

    def __call__(self):
        return next(self.times)


def test_min_rate_passes_a_fast_download():
    from util.http import iter_content_with_min_rate
    chunks = [b"x" * 1000] * 5
    # start, then one tick per chunk, 1 s apart: 1000 B/s against a 500 B/s minimum
    clock = FakeClock([0, 1, 2, 3, 4, 5])
    received = list(iter_content_with_min_rate(FakeResponse(chunks), min_bytes_per_sec=500, window=2, clock=clock))
    assert received == chunks


def test_min_rate_raises_on_a_trickle():
    from util.http import SlowDownloadError, iter_content_with_min_rate
    chunks = [b"x" * 10] * 10
    clock = FakeClock([0] + list(range(1, 11)))
    with pytest.raises(SlowDownloadError):
        list(iter_content_with_min_rate(FakeResponse(chunks), min_bytes_per_sec=500, window=3, clock=clock))


def test_min_rate_allows_a_slow_start_within_the_first_window():
    from util.http import iter_content_with_min_rate
    chunks = [b"x" * 10, b"x" * 10]
    clock = FakeClock([0, 1, 2])
    assert len(list(iter_content_with_min_rate(FakeResponse(chunks), min_bytes_per_sec=500, window=60,
                                                clock=clock))) == 2


# util/http.py: a host whose clearance refresh failed isn't refreshed again

def test_failed_refresh_is_remembered(monkeypatch):
    from util import http
    calls = []
    monkeypatch.setattr(http, "get_protected_html", lambda url: calls.append(url) or None)
    monkeypatch.setattr(http, "_failed_refreshes", set())
    monkeypatch.setattr(http, "_clearances", {})
    assert http._refresh_clearance("blocked.example") is False
    assert http._refresh_clearance("blocked.example") is False
    assert calls == ["https://blocked.example/"]


# brands/CPPlus.py: only real binaries are cached as "other"

@pytest.mark.parametrize("header, expected", [
    ((b"DH\x03\x04abc", "application/octet-stream"), "DH"),
    ((b"PK\x03\x04abc", "application/zip"), "other"),
    ((b"<!DOCTYPE html>", "text/html"), None),
    ((b"  <html>", "application/octet-stream"), None),
    ((b"\x00\x01binary", "text/html; charset=utf-8"), None),
    ((b"", "application/octet-stream"), None),
    (None, None),
])
def test_cpplus_classify_header(header, expected):
    from brands.CPPlus import classify_header
    assert classify_header(header) == expected


def test_cpplus_header_cache_save_is_atomic(monkeypatch, tmp_path):
    from brands import CPPlus
    path = tmp_path / "tmp" / "cpplus.json"
    monkeypatch.setattr(CPPlus, "HEADER_CACHE", str(path))
    CPPlus.save_header_cache({"/prodassets/firmware/a.bin": "DH"})
    assert json.loads(path.read_text()) == {"/prodassets/firmware/a.bin": "DH"}
    assert not os.path.exists(f"{path}.tmp")


# brands/VIPVision.py and brands/RVI.py

def test_vipvision_guessed_file_name():
    from brands.VIPVision import guessed_file_name
    name = guessed_file_name("https://www.rhinoco.com.au/file/display/8042",
                             "NVR64ULT-I, NVR128ULT-I Firmware - 2024-08-12")
    assert name.startswith("VIPVision_8042_")
    assert name.endswith(".bin")
    assert "2024-08-12" not in name


def test_rvi_quote_url_encodes_raw_paths_once():
    from brands.RVI import quote_url
    raw = "https://rvigroup.ru/exupload/Прошивки/Tiandy/RVi 1NR/fw.box"
    quoted = quote_url(raw)
    assert " " not in quoted and "Прошивки" not in quoted
    assert quote_url(quoted) == quoted
    assert quote_url("https://rvigroup.ru/download/api/?download-id=11046") == \
        "https://rvigroup.ru/download/api/?download-id=11046"


# util/download_firmware.py: retries resume the .part with a Range request

class RangeResponse:
    def __init__(self, status, body, content_range=None):
        self.status_code = status
        self.body = body
        self.headers = {"Content-Range": content_range} if content_range else {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=1):
        yield self.body


def test_retry_resumes_partial_download(tmp_path, monkeypatch):
    from util import download_firmware as dl
    part = tmp_path / "f.bin.part"
    part.write_bytes(b"hello ")
    seen = []

    def fake_get(url, stream=True, headers=None):
        seen.append(headers)
        return RangeResponse(206, b"world", "bytes 6-10/11")

    monkeypatch.setattr(dl.http, "get", fake_get)
    dl._http_download("https://x/f.bin", str(part))
    assert part.read_bytes() == b"hello world"
    assert seen[0]["Range"] == "bytes=6-"


def test_retry_starts_over_when_server_ignores_range(tmp_path, monkeypatch):
    from util import download_firmware as dl
    part = tmp_path / "f.bin.part"
    part.write_bytes(b"hello ")
    monkeypatch.setattr(dl.http, "get", lambda url, stream=True, headers=None: RangeResponse(200, b"hello world"))
    dl._http_download("https://x/f.bin", str(part))
    assert part.read_bytes() == b"hello world"


def test_stale_part_from_an_earlier_run_is_not_resumed(tmp_path, monkeypatch):
    from util import download_firmware as dl
    (tmp_path / "f.bin.part").write_bytes(b"old data")
    calls = []

    def downloader(url, part_name):
        calls.append(os.path.exists(part_name))
        with open(part_name, "wb") as f:
            f.write(b"new")

    result = dl._download_to("https://x/f.bin", str(tmp_path / "f.bin"), str(tmp_path / "f.bin.part"), downloader)
    assert calls == [False]
    assert (tmp_path / "f.bin").read_bytes() == b"new"
    assert result == str(tmp_path / "f.bin")


# util/download_firmware.py: falling back to the Wayback Machine's copy

def test_slow_download_falls_back_to_wayback(tmp_path, monkeypatch):
    from util import download_firmware as dl
    from util.http import SlowDownloadError
    monkeypatch.setattr(dl.time, "sleep", lambda s: None)
    live_calls = []

    def slow_downloader(url, part_name):
        live_calls.append(url)
        raise SlowDownloadError("10 KB/s")

    monkeypatch.setattr(dl, "wayback_capture", lambda url: "https://web.archive.org/web/1id_/" + url)

    def fake_http_download(url, part_name):
        assert url.startswith("https://web.archive.org/")
        with open(part_name, "wb") as f:
            f.write(b"PK\x03\x04firmware")

    monkeypatch.setattr(dl, "_http_download", fake_http_download)
    target = str(tmp_path / "f.bin")
    assert dl._download_to("https://slow.example/f.bin", target, target + ".part", slow_downloader) == target
    assert len(live_calls) == dl.SLOW_ATTEMPTS_BEFORE_WAYBACK
    assert dl.downloaded_via_wayback["https://slow.example/f.bin"].startswith("https://web.archive.org/")


def test_wayback_html_capture_is_rejected(tmp_path, monkeypatch):
    from util import download_firmware as dl
    monkeypatch.setattr(dl, "wayback_capture", lambda url: "https://web.archive.org/web/1id_/" + url)

    def html(url, part_name):
        with open(part_name, "wb") as f:
            f.write(b"<!DOCTYPE html><html>not found</html>")

    monkeypatch.setattr(dl, "_http_download", html)
    target = str(tmp_path / "f.bin")
    assert dl._download_from_wayback("https://x/f.bin", target, target + ".part") is None
    assert not os.path.exists(target) and not os.path.exists(target + ".part")


def test_dead_url_wayback_check_is_remembered(tmp_path, monkeypatch):
    from util import download_firmware as dl
    monkeypatch.setattr(dl, "FAILED_DOWNLOADS_FILE", str(tmp_path / "failed.json"))
    dl._registries.pop(str(tmp_path / "failed.json"), None)
    dl.record_failed_download("https://dead/f.bin", "HTTP 404")
    lookups = []
    monkeypatch.setattr(dl, "wayback_capture", lambda url: lookups.append(url))
    target = str(tmp_path / "f.bin")
    assert dl.download_dead_url_from_wayback("https://dead/f.bin", target) is None
    assert dl.download_dead_url_from_wayback("https://dead/f.bin", target) is None
    assert lookups == ["https://dead/f.bin"]  # the second call didn't look it up again


def test_throttling_host_tries_wayback_first(tmp_path, monkeypatch):
    from util import download_firmware as dl
    monkeypatch.setattr(dl, "WAYBACK_FIRST_HOSTS", {"slow.example"})
    monkeypatch.setattr(dl, "wayback_capture", lambda url: "https://web.archive.org/web/1id_/" + url)
    monkeypatch.setattr(dl, "_http_download", lambda url, part: open(part, "wb").write(b"PK\x03\x04data"))
    live = []
    target = str(tmp_path / "f.bin")
    assert dl.download_firmware("https://slow.example/f.bin", target, lambda u, p: live.append(u)) == target
    assert live == []
