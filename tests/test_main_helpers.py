import os
import re
import subprocess
import sys

import pytest

import main

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --- get_firmware_file_name --------------------------------------------------------------------------------------

def test_file_name_from_url_decodes_escapes():
    firmware = {"firmware_latest": "https://example.com/dl/IPC%28A%29_V2.800.bin?token=abc"}
    assert main.get_firmware_file_name(firmware, "firmware_latest") == "IPC(A)_V2.800.bin"


def test_explicit_file_name_and_slash():
    firmware = {"firmware_previous": "https://drive.google.com/uc?id=1", "firmware_previous_file_name": "a%2Fb.bin"}
    assert main.get_firmware_file_name(firmware, "firmware_previous") == "a_b.bin"


def test_short_name_unchanged():
    assert main.get_firmware_file_name({"firmware_latest": "https://x/y/IPC-144M.bin"}, "firmware_latest") == "IPC-144M.bin"


def test_long_cyrillic_name_shortened():
    long_name = "Прошивка_для_моделей_" + "_".join(f"Камера{i}" for i in range(30)) + "_V2.800.0000000.0.R.220101.bin"
    assert len(long_name.encode()) > main.MAX_FILE_NAME_BYTES
    name = main.get_firmware_file_name({"firmware_latest": f"https://x/{long_name}"}, "firmware_latest")
    assert len(name.encode()) <= main.MAX_FILE_NAME_BYTES
    assert name.endswith(".bin")
    assert re.search(r"-[0-9a-f]{8}\.bin$", name)
    assert name.startswith("Прошивка")
    # The end of the stem (version) is kept
    assert "220101-" in name
    # Valid UTF-8 (slicing is by character, not byte)
    name.encode().decode("utf-8")
    # Deterministic, and unique for a different name with the same start and end
    assert main.get_firmware_file_name({"firmware_latest": f"https://x/{long_name}"}, "firmware_latest") == name
    other = long_name.replace("Камера5_", "Камера55_")
    other_name = main.get_firmware_file_name({"firmware_latest": f"https://x/{other}"}, "firmware_latest")
    assert other_name != name


def test_long_ascii_name_shortened():
    long_name = "A" * 300 + ".zip"
    name = main.get_firmware_file_name({"firmware_latest": f"https://x/{long_name}"}, "firmware_latest")
    assert len(name.encode()) <= main.MAX_FILE_NAME_BYTES
    assert name.endswith(".zip")


def test_long_extension_does_not_hang():
    # Run in a subprocess so the infinite loop can be killed
    code = ("import main; print(main.get_firmware_file_name("
            "{'firmware_latest': 'https://x/fw.' + 'A' * 250}, 'firmware_latest'))")
    try:
        result = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, timeout=2)
    except subprocess.TimeoutExpired:
        pytest.fail("get_firmware_file_name did not return")
    assert result.returncode == 0
    assert len(result.stdout.strip().encode()) <= main.MAX_FILE_NAME_BYTES


# --- split_version_build_date ------------------------------------------------------------------------------------

def test_split_version_six_digit_date():
    firmware = {"firmware_version": "V2.800.0000032.0.R.250224"}
    main.split_version_build_date(firmware)
    assert firmware == {"firmware_version": "V2.800.0000032.0.R", "release_date": "2025-02-24"}


def test_split_version_eight_digit_date_keeps_existing_release_date():
    firmware = {"firmware_version": " 4.001.0000000.1.R.20201126 ", "release_date": "2020-12-01"}
    main.split_version_build_date(firmware)
    assert firmware == {"firmware_version": "4.001.0000000.1.R", "release_date": "2020-12-01"}


@pytest.mark.parametrize("version", ["V2.800.0000032.0.R", "1.0.3", None, 5, ""])
def test_split_version_no_match(version):
    firmware = {"firmware_version": version}
    main.split_version_build_date(firmware)
    assert firmware == {"firmware_version": version}


# --- merge_listing -----------------------------------------------------------------------------------------------

FILE = "DH_IPC-HX5X3X-Rhea_MultiLang_PN_Stream3_V2.800.0000000.0.R.220101.bin"


def firmware(**extra):
    value = {"vendor": "Acme", "source": "https://acme.example/firmware", "firmware_notes": "Some notes",
             "firmware_latest": f"https://acme.example/dl/{FILE}", "firmware_previous": f"https://acme.example/old/{FILE}"}
    value.update(extra)
    return value


def test_merge_listing_freshness_across_runs(monkeypatch):
    monkeypatch.setattr(main, "RUN_DATE", "2026-01-01")
    listings = main.merge_listing([], firmware(firmware_version="V3.0", release_date="2026-01-01"), "firmware_latest",
                                  ["IPC-HDW2431T-AS-S2"], FILE)
    assert len(listings) == 1
    listing = listings[0]
    assert listing["first_seen"] == listing["last_seen"] == listing["last_seen_latest"] == "2026-01-01"
    assert listing["latest"] is True
    assert listing["kind"] == "vendor"
    assert listing["firmware_version"] == "V3.0"
    assert listing["camera_name"] == ["IPC-HDW2431T-AS-S2"]
    assert listing["notes"] == ["Some notes"]

    # Next run the page lists it only as the previous firmware
    monkeypatch.setattr(main, "RUN_DATE", "2026-02-01")
    before = [dict(listing)]
    listings2 = main.merge_listing(before, firmware(), "firmware_previous", [], FILE)
    assert before[0]["last_seen"] == "2026-01-01"  # input not mutated
    assert len(listings2) == 1
    listing = listings2[0]
    assert listing["first_seen"] == "2026-01-01"
    assert listing["last_seen"] == "2026-02-01"
    assert listing["last_seen_latest"] == "2026-01-01"
    assert listing["latest"] is False
    assert listing["url"] == f"https://acme.example/old/{FILE}"


def test_merge_listing_other_source_is_separate(monkeypatch):
    monkeypatch.setattr(main, "RUN_DATE", "2026-01-01")
    listings = main.merge_listing([], firmware(), "firmware_latest", [], FILE)
    listings = main.merge_listing(listings, firmware(source="https://acme.example/other"), "firmware_latest", [], FILE)
    assert len(listings) == 2


def test_merge_listing_mirror_has_no_latest(monkeypatch):
    monkeypatch.setattr(main, "RUN_DATE", "2026-01-01")
    existing = [{"vendor": "Acme", "source": "https://acme.example/firmware", "latest": True,
                 "last_seen_latest": "2025-01-01"}]
    listings = main.merge_listing(existing, firmware(source_kind="mirror", original_url="https://orig/x",
                                                     archived_at="2020-01-01"), "firmware_latest", [], FILE)
    listing = listings[0]
    assert listing["kind"] == "mirror"
    assert "latest" not in listing and "last_seen_latest" not in listing
    assert listing["original_url"] == "https://orig/x"
    assert listing["archived_at"] == "2020-01-01"


def test_merge_listing_version_from_file_name(monkeypatch):
    monkeypatch.setattr(main, "RUN_DATE", "2026-01-01")
    listing = main.merge_listing([], firmware(), "firmware_previous", [], FILE)[0]
    assert listing["firmware_version"] == "V2.800.0000000.0.R"
    assert listing["release_date"] == "2022-01-01"


def test_merge_listing_splits_names(monkeypatch):
    monkeypatch.setattr(main, "RUN_DATE", "2026-01-01")
    listing = main.merge_listing([], firmware(firmware_notes=""), "firmware_latest",
                                 ["IPC-HDW2431T-AS-S2", "IPC-HX3XXX", "Full Color firmware", FILE], FILE)[0]
    assert listing["camera_name"] == ["IPC-HDW2431T-AS-S2"]
    assert listing["series"] == ["IPC-HX3XXX"]
    assert listing["notes"] == ["Full Color firmware"]


# --- tidy_entry --------------------------------------------------------------------------------------------------

def test_tidy_entry():
    entry = {
        "camera_name": ["IPC-144M", "IPC-HX3XXX", "Full Color firmware"],
        "firmware_version": "ipc-144m",
        "listings": [
            {"release_date": "2023-05-01", "firmware_version": "IPC-144M", "camera_name": ["IPC-144M"]},
            {"release_date": "2022-01-01", "kind": "mirror"},
            {"series": ["IPC-HX3XXX"]},
        ],
    }
    main.tidy_entry("IPC-144M.bin", entry)
    assert entry["release_date"] == "2022-01-01"
    assert "firmware_version" not in entry
    assert "firmware_version" not in entry["listings"][0]
    assert [listing["kind"] for listing in entry["listings"]] == ["vendor", "mirror", "vendor"]
    assert entry["camera_name"] == ["IPC-144M"]
    assert entry["series"] == ["IPC-HX3XXX"]
    assert entry["notes"] == ["Full Color firmware"]
    assert entry["listings"][2]["series"] == ["IPC-HX3XXX"]
    assert entry["file_type"] == "firmware"


def test_tidy_entry_keeps_real_version_and_file_type():
    entry = {"firmware_version": "V2.800.0000000.0.R", "file_type": "software", "series": ["old series"]}
    main.tidy_entry("General_SmartPSS_V2.zip", entry)
    assert entry["firmware_version"] == "V2.800.0000000.0.R"
    assert entry["file_type"] == "software"


def test_tidy_entry_backfills_file_type_from_name():
    entry = {}
    main.tidy_entry("Dahua_EN_win11Pro_22H2_20240508.zip", entry)
    assert entry["file_type"] == "software"
    assert "series" not in entry


def test_tidy_entry_inspects_content(tmp_path):
    # cwd is tmp_path (conftest), so this is a temporary firmware/ folder
    (tmp_path / "firmware").mkdir()
    (tmp_path / "firmware" / "IPC_V2.bin").write_bytes(b"MZ\x00\x00")
    entry = {"file_type": "firmware"}
    main.tidy_entry("IPC_V2.bin", entry, inspect_content=True)
    assert entry["file_type"] == "software"


# --- selected_modules --------------------------------------------------------------------------------------------

@pytest.fixture
def module_filters(monkeypatch):
    monkeypatch.setattr(main, "ONLY_MODULES", set())
    monkeypatch.setattr(main, "SKIP_MODULES", set())
    yield


def short(module):
    return module.__name__.split(".")[-1]


def test_selected_all(module_filters):
    assert main.selected_modules() == main.oem_modules


def test_only_by_name(module_filters, monkeypatch):
    monkeypatch.setattr(main, "ONLY_MODULES", {"empiretech"})
    assert [short(m) for m in main.selected_modules()] == ["EmpireTech"]


def test_only_by_display_name(module_filters, monkeypatch):
    monkeypatch.setattr(main, "ONLY_MODULES", {"wayback machine"})
    assert [short(m) for m in main.selected_modules()] == ["Wayback"]


def test_only_by_vendor(module_filters, monkeypatch):
    monkeypatch.setattr(main, "ONLY_MODULES", {"gss"})
    selected = main.selected_modules()
    assert len(selected) == 6
    assert all(m.vendor == "GSS" for m in selected)


def test_skip(module_filters, monkeypatch):
    monkeypatch.setattr(main, "SKIP_MODULES", {"wayback machine"})
    selected = main.selected_modules()
    assert len(selected) == len(main.oem_modules) - 1
    assert "Wayback" not in [short(m) for m in selected]


def test_only_and_skip(module_filters, monkeypatch):
    monkeypatch.setattr(main, "ONLY_MODULES", {"dahua"})
    monkeypatch.setattr(main, "SKIP_MODULES", {"dahuaofficial"})
    assert [short(m) for m in main.selected_modules()] == ["Dahua", "Dahua_FileDirectory", "DahuaTechSupport", "DahuaPoland"]
