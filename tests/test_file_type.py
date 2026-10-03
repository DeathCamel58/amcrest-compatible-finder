import pytest

from conftest import make_zip_bytes, to_dh_variant
from util.file_type import classify_file, classify_file_content, classify_file_type


@pytest.mark.parametrize("name, expected", [
    ("Dahua_EN_win11Pro_22H2_20240508.zip", "software"),
    ("General_SmartPSS_ChnEng_IS_V2.003.0000004.0.R.20210427.zip", "software"),
    ("General_SmartPSS-Lite_V1.001.0000004.0.R.20230113.exe", "software"),
    ("webplugin.exe", "software"),
    ("DH_WebPlugin_Win_V3.2.1.zip", "software"),
    ("Dahua_NVR_Release_Notes.zip", "document"),
    ("NVR_User's_Manual.zip", "document"),
    ("Quick_Start_Guide.pdf", "document"),
    ("Dahua_PSS_Client.zip", "software"),
    ("FLIR_IPC-HX1X2X-Themis-VMS_Eng_NP_Stream3_V2.622.0000000.31.R.180813.zip", "firmware"),
    ("DH_IPC-HX5X3X-Rhea_MultiLang_PN_Stream3_V2.800.0000000.0.R.220101.bin", "firmware"),
    ("General_NVR4XXX-4KS2_MultiLang_V4.001.0000000.1.R.201126.bin", "firmware"),
    ("IPC-144M.bin", "firmware"),
    ("digicap.dav", "firmware"),
])
def test_classify_file_type(name, expected):
    assert classify_file_type(name) == expected


def test_software_url_path():
    assert classify_file_type("tool_v1.zip", "https://example.com/software/tool_v1.zip") == "software"


def test_release_notes_bin_is_not_document():
    assert classify_file_type("Release_Notes_V2.800.bin") == "firmware"


# --- classify_file_content ---------------------------------------------------------------------------------------

def test_mz_header(write_file):
    assert classify_file_content(write_file("a.bin", b"MZ\x90\x00" + b"\x00" * 60)) == "software"


def test_pdf_header(write_file):
    assert classify_file_content(write_file("a.bin", b"%PDF-1.7\n...")) == "document"


def test_unknown_header(write_file):
    assert classify_file_content(write_file("a.bin", b"\x00\x01\x02\x03rest")) is None


def test_missing_file(tmp_path):
    assert classify_file_content(str(tmp_path / "missing.bin")) is None


def test_zip_of_only_exe(write_file):
    path = write_file("a.zip", make_zip_bytes({"Setup.exe": b"MZ...", "tools/helper.exe": b"MZ..."}))
    assert classify_file_content(path) == "software"


def test_zip_of_only_pdfs(write_file):
    path = write_file("a.zip", make_zip_bytes({"manual.pdf": b"%PDF", "docs/notes.pdf": b"%PDF"}))
    assert classify_file_content(path) == "document"


def test_zip_with_firmware_and_webplugin(write_file):
    path = write_file("a.zip", make_zip_bytes({"DH_NVR_V4.001.bin": b"DH" + b"\x00" * 100,
                                               "webplugin.exe": b"MZ..."}))
    assert classify_file_content(path) == "firmware"


def test_pxe_zip(write_file):
    path = write_file("a.zip", make_zip_bytes({"pxe/ldlinux.c32": b"\x00" * 10, "pxe/pxelinux.cfg/default": b"x",
                                               "pxe/vmlinuz": b"\x00" * 10}))
    assert classify_file_content(path) == "software"


def test_office_document(write_file):
    path = write_file("a.zip", make_zip_bytes({"[Content_Types].xml": b"<x/>", "word/document.xml": b"<x/>"}))
    assert classify_file_content(path) == "document"


def test_dh_header_zip(write_file):
    data = to_dh_variant(make_zip_bytes({"Install": b"x", "kernel.img": b"\x00" * 100, "romfs-x.squashfs.img": b"y"}))
    assert data[:2] == b"DH"
    assert classify_file_content(write_file("a.bin", data)) == "firmware"


def test_zip_inconclusive(write_file):
    path = write_file("a.zip", make_zip_bytes({"something.dat": b"x", "manual.pdf": b"%PDF"}))
    assert classify_file_content(path) is None


def test_classify_file_falls_back_to_name(write_file):
    path = write_file("a.zip", b"\x00\x00garbage")
    assert classify_file(path, "General_SmartPSS_V2.zip") == "software"
    mz = write_file("b.bin", b"MZ\x00\x00")
    assert classify_file(mz, "DH_IPC-HX5X3X_V2.800.bin") == "software"
