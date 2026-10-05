import ftplib
import os
import time
from urllib.parse import quote, unquote, urlparse

from util.mirror import is_firmware_file
from util.oem_helpers import make_firmware

name = "Winco (ftp.winco.com.hk)"
vendor = "Winco"
# A Hong Kong Dahua distributor's anonymous FTP server
kind = "mirror"

host = "ftp.winco.com.hk"
root = "/product/Dahua/Firmware"
MAX_DEPTH = 4
# Names are read as latin-1, which round-trips any bytes, so folders with Chinese names (UTF-8 or GBK) can be sent
# back to the server unchanged; they're decoded properly only for display and URLs
WIRE_ENCODING = "latin-1"


def readable(wire_name):
    raw = wire_name.encode(WIRE_ENCODING)
    for encoding in ("utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def to_wire(name):
    return name.encode("utf-8").decode(WIRE_ENCODING)


def walk(ftp, path, depth=0):
    """(path, name) for every file below path. A name that can't be entered as a folder is a file."""
    entries = []
    try:
        names = ftp.nlst(path)
    except ftplib.error_perm:
        return entries
    for full in names:
        entry = full.rsplit("/", 1)[-1]
        if entry in (".", ".."):
            continue
        child = full if full.startswith("/") else f"{path}/{entry}"
        if depth < MAX_DEPTH:
            try:
                ftp.cwd(child)
                time.sleep(0.5)
                entries += walk(ftp, child, depth + 1)
                continue
            except ftplib.error_perm:
                pass
        entries.append((path, entry))
    return entries


def to_firmwares(entries):
    firmwares = []
    for wire_path, wire_name in entries:
        path, file_name = readable(wire_path), readable(wire_name)
        url = f"ftp://{host}{quote(f'{path}/{file_name}')}"
        if not is_firmware_file(file_name, url):
            continue
        # A product folder ("BF3221", "ASI7213Y-V3-T1_Traditonal_Chinese") names the model
        folder = path[len(root):].strip("/").split("/")[0] if path != root else None
        model = folder.split("_")[0] if folder else None
        firmwares.append(make_firmware([model] if model else [], url, file_name=file_name))
    return firmwares


def get_firmwares():
    ftp = ftplib.FTP(host, timeout=30, encoding=WIRE_ENCODING)
    try:
        ftp.login()
        return to_firmwares(walk(ftp, root))
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()


def download_file(url, part_name):
    """Download an ftp:// URL, resuming a .part an earlier attempt left."""
    path = to_wire(unquote(urlparse(url).path))
    have = os.path.getsize(part_name) if os.path.exists(part_name) else 0
    ftp = ftplib.FTP(host, timeout=120, encoding=WIRE_ENCODING)
    try:
        ftp.login()
        ftp.voidcmd("TYPE I")
        with open(part_name, "ab" if have else "wb") as f:
            ftp.retrbinary(f"RETR {path}", f.write, blocksize=1024 * 1024, rest=have or None)
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()
