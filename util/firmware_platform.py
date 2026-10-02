import os
import re
import zipfile

# Hikvision firmware (digicap.dav) headers are XOR-scrambled with this fixed key
HIKVISION_KEY = bytes([0xBA, 0xCD, 0xBC, 0xFE, 0xD6, 0xCA, 0xDD, 0xD3, 0xBA, 0xB9, 0xA3, 0xAB, 0xBF, 0xCB, 0xB5, 0xBE])
# "SWKH" is the classic header magic, "02KH" the newer one (same header layout)
HIKVISION_MAGICS = (b"SWKH", b"02KH")

# Dahua's MCU, access control and newer encrypted firmwares don't start with "DH", but keep Dahua's naming
DAHUA_FILE_NAME = re.compile(r"^(Upall_|update_)?(General|DH|DHI|Customer)_|_V\d\.\d{3}\.[0-9A-Z]+\.\d+\.[A-Z]\.\d{6,8}")

# Only look at this many zip members, so a huge archive doesn't take forever
MAX_ZIP_MEMBERS = 50


def detect_header(header):
    if header[:2] == b"DH":
        return "dahua"
    decoded = bytes(b ^ HIKVISION_KEY[(i + (i >> 4)) & 0xF] for i, b in enumerate(header[:4]))
    if decoded in HIKVISION_MAGICS:
        return "hikvision"
    return "unknown"


def detect_platform(path):
    """Returns "dahua", "hikvision" or "unknown", from the firmware's header (looking inside zips), falling back
    on Dahua's file naming."""
    platform = detect_platform_from_header(path)
    if platform == "unknown" and DAHUA_FILE_NAME.search(os.path.basename(path)):
        return "dahua"
    return platform


def detect_platform_from_header(path):
    try:
        with open(path, "rb") as f:
            header = f.read(16)
    except OSError:
        return "unknown"

    if header[:2] != b"PK":
        return detect_header(header)

    try:
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist()[:MAX_ZIP_MEMBERS]:
                if member.is_dir():
                    continue
                with archive.open(member) as f:
                    platform = detect_header(f.read(16))
                if platform != "unknown":
                    return platform
    except (zipfile.BadZipFile, OSError, NotImplementedError, RuntimeError):
        pass

    return "unknown"
