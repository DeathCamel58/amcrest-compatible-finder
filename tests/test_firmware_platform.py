from conftest import make_zip_bytes
from util.firmware_platform import HIKVISION_KEY, detect_header, detect_platform


def hikvision_header(magic=b"SWKH"):
    encoded = bytes(b ^ HIKVISION_KEY[(i + (i >> 4)) & 0xF] for i, b in enumerate(magic))
    return encoded + b"\x00" * 60


def test_dh_header(write_file):
    assert detect_platform(write_file("firmware.bin", b"DH\x03\x04" + b"\x00" * 60)) == "dahua"


def test_hikvision_header(write_file):
    assert detect_platform(write_file("digicap.dav", hikvision_header())) == "hikvision"
    assert detect_header(hikvision_header(b"02KH")) == "hikvision"


def test_raw_magic_not_hikvision():
    # The magic only counts once decoded with the key
    assert detect_header(b"SWKH" + b"\x00" * 12) == "unknown"


def test_hikvision_inside_zip(write_file):
    path = write_file("hik.zip", make_zip_bytes({"readme.txt": b"hello", "digicap.dav": hikvision_header()}))
    assert detect_platform(path) == "hikvision"


def test_dahua_inside_zip(write_file):
    path = write_file("dh.zip", make_zip_bytes({"update.img": b"DH" + b"\x00" * 30}))
    assert detect_platform(path) == "dahua"


def test_dahua_file_name_fallback(write_file):
    content = b"\x12\x34\x56\x78" + b"\x00" * 60
    assert detect_platform(write_file("DH_IPC-HX5X3X-Rhea_MultiLang_PN_V2.800.0000000.0.R.220101.bin",
                                      content)) == "dahua"
    assert detect_platform(write_file("General_ASI7213X_Eng_P.bin", content)) == "dahua"
    assert detect_platform(write_file("ACME_camera_fw.bin", content)) == "unknown"


def test_missing_file(tmp_path):
    assert detect_platform(str(tmp_path / "nothing.bin")) == "unknown"
