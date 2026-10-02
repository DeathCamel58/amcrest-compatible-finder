import os
import struct

# Zip "end of central directory" record. Dahua's .bin firmwares are zips with "DH" in place of "PK" in the
# signatures, but their end record still uses "PK"
END_RECORDS = (b"PK\x05\x06", b"DH\x05\x06")
LOCAL_HEADERS = (b"PK\x03\x04", b"DH\x03\x04")
# The end record is 22 bytes plus a comment of up to 64 KB, so it's always within this distance of the end
END_RECORD_SEARCH = 22 + 65535


def check_integrity(path):
    """Returns {"status": "ok" | "truncated" | "unverified", "reason": ...} for a firmware file.

    Zip-style files (including Dahua's DH variant) must end with an intact zip directory; a download cut off
    partway has file entries but no directory. Other formats can't be checked this way, so they're "unverified".
    """
    size = os.path.getsize(path)
    if size == 0:
        return {"status": "truncated", "reason": "empty file"}

    with open(path, "rb") as f:
        header = f.read(4)
        if header not in LOCAL_HEADERS:
            return {"status": "unverified", "reason": "not a zip-style file"}
        tail_start = max(0, size - END_RECORD_SEARCH)
        f.seek(tail_start)
        tail = f.read()

    position = max(tail.rfind(signature) for signature in END_RECORDS)
    if position < 0:
        return {"status": "truncated", "reason": "zip directory missing (download was cut off)"}

    record = tail[position:position + 22]
    if len(record) < 22:
        return {"status": "truncated", "reason": "zip end record cut off"}

    directory_size, directory_offset = struct.unpack("<II", record[12:20])
    end_record_offset = tail_start + position
    # 0xFFFFFFFF means the real values are in a zip64 record, which isn't worth parsing; the end record being
    # present already shows the file wasn't cut off
    if directory_offset != 0xFFFFFFFF and directory_offset + directory_size > end_record_offset:
        return {"status": "truncated", "reason": "zip directory extends past the end record"}

    return {"status": "ok"}
