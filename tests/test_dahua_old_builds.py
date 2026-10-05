import json
from datetime import date

from brands import DahuaOldBuilds as old
from util.release_notes import fallback_pdf_text, versions_in_text

FOLDER = "https://materialfile.dahuasecurity.com/uploads/cpq/50597/firmware/"
FAMILY = "DH_IPC-HX5(4)XXX-EMMC-Taurus_MultiLang_PN_Stream3"
NOTES = "https://materialfile.dahuasecurity.com/uploads/cpq/88316/firmware/DH_Taurus_Platform_Products_Release_Notes.pdf"
NOTES_TEXT = """1.1 V3.140.0000000.81.R.260508 ..... 1
1.2 V3.140.0000000.76.R.260312 ..... 1
1.3 V3.140.0000000.71.R.260210 ..... 2
2.1 V2.840.0000000.12.R.230101 ..... 9
Security Center Version 5.7SR1(5.7.529.12)"""


def cameras():
    current = f"{FAMILY}_V3.140.0000000.81.R.260508.zip"
    return {
        current: {"camera_name": ["IPC-HDW5442H"], "changelog": NOTES,
                  "listings": [{"vendor": "Dahua", "url": FOLDER + current}]},
        # Another vendor's copy of an older build of the same family
        f"{FAMILY}_V3.140.0000000.43.R.251212.zip": {"listings": [{"vendor": "EmpireTech", "url": "https://mega/x"}]},
    }


def test_parse_name_and_dates():
    parsed = old.parse_name(f"{FAMILY}_V3.140.0000000.81.R.260508.zip")
    assert parsed == {"family": FAMILY, "version": "V3.140.0000000.81.R", "date": "260508", "ext": ".zip"}
    assert old.parse_name("DH_NVR_V4.001.0000000.0.R.20190809.bin")["date"] == "20190809"
    assert old.parse_name("readme.txt") is None
    assert old.series("V3.140.0000000.81.R") == "V3.140"
    assert old.release_date("260508") == "2026-05-08" and old.release_date("20190809") == "2019-08-09"


def test_versions_in_release_notes():
    assert versions_in_text(NOTES_TEXT) == [("V3.140.0000000.81.R", "260508"), ("V3.140.0000000.76.R", "260312"),
                                            ("V3.140.0000000.71.R", "260210"), ("V2.840.0000000.12.R", "230101")]
    pdf = b"stream\n" + __import__("zlib").compress(b"BT (1.2 V3.140.0000000.76.R.260312) Tj ET") + b"\nendstream"
    assert ("V3.140.0000000.76.R", "260312") in versions_in_text(fallback_pdf_text(pdf))


def test_candidates():
    folders, known, family_versions, swr = old.known_firmwares(cameras())
    wanted = {"name:x": {"name": f"{FAMILY}_V3.140.0000000.62.R.260114", "found": None}}
    note_versions = {NOTES: versions_in_text(NOTES_TEXT)}
    todo = old.candidates(folders, known, family_versions, old.wanted_versions(wanted), note_versions, swr)
    by_url = {c[0]: c[5] for c in todo}
    assert by_url == {
        FOLDER + f"{FAMILY}_V3.140.0000000.62.R.260114.zip": "wanted",
        FOLDER + f"{FAMILY}_V3.140.0000000.43.R.251212.zip": "other vendors",
        FOLDER + f"{FAMILY}_V3.140.0000000.76.R.260312.zip": "release notes",
        FOLDER + f"{FAMILY}_V3.140.0000000.71.R.260210.zip": "release notes",
    }  # V2.840 is another branch, and V3.140...81 is the build already there
    assert [c[5] for c in todo] == ["wanted", "other vendors", "release notes", "release notes"]
    assert todo[2][3] == "260312"  # newest first


def test_swr_release_notes_and_variants():
    notes = "https://materialfile.dahuasecurity.com/uploads/cpq/SWR/3376862/DHI_IVSS71XXAI-I_MultiLang_V3.005.0000000.1.R.250908_Release_Notes.pdf"
    vth = "https://materialfile.dahuasecurity.com/uploads/cpq/62177/firmware/"
    data = {
        "a": {"changelog": notes, "listings": []},
        "DH_VTHX421H_MultiLang_SIP_EMEA_V4.810.0000000.3.R.260410.zip": {
            "listings": [{"url": vth + "DH_VTHX421H_MultiLang_SIP_EMEA_V4.810.0000000.3.R.260410.zip"}]},
        "DH_VTHX421H_MultiLang_SIP_Asia_V4.600.0000000.0.R.220819.zip": {
            "listings": [{"url": "https://materialfile.dahuasecurity.com/uploads/cpq/1/firmware/"
                                 "DH_VTHX421H_MultiLang_SIP_Asia_V4.600.0000000.0.R.220819.zip"}]},
    }
    folders, known, family_versions, swr = old.known_firmwares(data)
    urls = {c[0]: c[5] for c in old.candidates(folders, known, family_versions, {}, {}, swr)}
    assert urls["https://materialfile.dahuasecurity.com/uploads/cpq/SWR/3376861/M/"
                "DHI_IVSS71XXAI-I_MultiLang_V3.005.0000000.1.R.250908.zip"] == "swr"
    assert urls[vth + "DH_VTHX421H_MultiLang_SIP_Asia_V4.810.0000000.3.R.260410.zip"] == "variant"


class FakeResponse:
    def __init__(self, status, content=b"", headers=None):
        self.status_code, self.content, self.headers = status, content, headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_get_firmwares_probes_once_and_caches(monkeypatch, tmp_path):
    with open("cameras.json", "w") as f:
        json.dump(cameras(), f)
    present = FOLDER + f"{FAMILY}_V3.140.0000000.76.R.260312.zip"
    calls = []

    def fake_get(url, stream=False, headers=None, timeout=None):
        calls.append(url)
        if url == NOTES:
            return FakeResponse(200, NOTES_TEXT.encode())
        if url == present:
            return FakeResponse(206, headers={"Content-Range": "bytes 0-0/123456"})
        return FakeResponse(403)

    monkeypatch.setattr(old.http, "get", fake_get)
    monkeypatch.setattr(old, "pdf_text", lambda data: data.decode())
    monkeypatch.setattr(old, "PROBE_DELAY", 0)
    monkeypatch.setattr(old.time, "sleep", lambda s: None)

    found = old.get_firmwares(max_probes=1, today=date(2026, 10, 5))
    assert len(calls) == 2 and found == []  # the cap: one candidate checked (the notes are read once)
    found = old.get_firmwares(max_probes=10, today=date(2026, 10, 5))
    assert [f["firmware_latest"] for f in found] == [present]
    assert found[0]["firmware_size"] == 123456 and found[0]["release_date"] == "2026-03-12"
    assert found[0]["camera_name"] == ["IPC-HDW5442H"]
    calls.clear()
    assert len(old.get_firmwares(today=date(2026, 10, 6))) == 1
    assert calls == []  # everything was cached: no requests at all
    cache = old.load_cache()
    assert cache["probes"][present]["status"] == "hit"
