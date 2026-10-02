import base64
import json
import random
import re
import struct
import time

from Crypto.Cipher import AES
from requests import HTTPError

from util import http

# Minimal client for public MEGA links: list shared folders, and download + decrypt files.
# Protocol reference: https://github.com/meganz/sdk (public folder links use the folder key to wrap node keys)

API_URL = "https://g.api.mega.co.nz/cs"

# MEGA API error codes
EAGAIN = -3
ERATELIMIT = -4
EOVERQUOTA = -17

# Listings are needed again at download time to get node keys, so keep them for the life of the process
_folder_cache = {}


class MegaError(Exception):
    pass


def _b64decode(data):
    data = data.replace("-", "+").replace("_", "/").replace(",", "")
    return base64.b64decode(data + "=" * (-len(data) % 4))


def _to_words(data):
    return struct.unpack(f">{len(data) // 4}I", data)


def _from_words(words):
    return struct.pack(f">{len(words)}I", *words)


def _api_request(payload, folder_handle=None, attempts=6):
    params = {"id": random.randint(0, 0xFFFFFFFF)}
    if folder_handle:
        params["n"] = folder_handle

    for attempt in range(attempts):
        response = http.get_session().post(API_URL, params=params, data=json.dumps([payload]), timeout=http.TIMEOUT)
        response.raise_for_status()
        result = response.json()
        if isinstance(result, int):
            code = result
        else:
            result = result[0]
            code = result if isinstance(result, int) else 0

        if code in (EAGAIN, ERATELIMIT):
            # MEGA asks clients to back off and retry
            time.sleep(2 ** attempt)
            continue
        if code == EOVERQUOTA:
            # Not worth retrying until the transfer quota resets, so surface it like an HTTP error
            raise HTTPError(f"MEGA transfer quota exceeded ({code})")
        if code < 0:
            raise MegaError(f"MEGA API error {code} for {payload}")

        return result

    raise MegaError(f"MEGA API kept asking to retry for {payload}")


def _decrypt_key(encrypted, key):
    cipher = AES.new(key, AES.MODE_ECB)
    return cipher.decrypt(encrypted)


def _file_key_parts(full_key):
    """A 32 byte file key packs the AES key, CTR nonce and meta MAC together"""
    k = _to_words(full_key)
    aes_key = _from_words((k[0] ^ k[4], k[1] ^ k[5], k[2] ^ k[6], k[3] ^ k[7]))
    nonce = _from_words(k[4:6])
    meta_mac = _from_words(k[6:8])
    return aes_key, nonce, meta_mac


def _decrypt_attributes(encrypted, aes_key):
    data = AES.new(aes_key, AES.MODE_CBC, iv=b"\0" * 16).decrypt(_b64decode(encrypted))
    data = data.rstrip(b"\0")
    if not data.startswith(b"MEGA"):
        raise MegaError("Couldn't decrypt node attributes (wrong key?)")

    return json.loads(data[4:].decode("utf-8", errors="replace"))


def parse_url(url):
    """Parse a mega.nz URL into ("folder", handle, key, node) or ("file", handle, key, None)."""
    # Folder file links look like https://mega.nz/folder/<handle>#<key>/file/<node>
    match = re.search(r"mega\.nz/folder/([^#/?]+)#([^/?#]+)(?:/file/([^/?#]+))?", url)
    if match:
        return "folder", match[1], match[2], match[3]

    match = re.search(r"mega\.nz/file/([^#/?]+)#([^/?#]+)", url)
    if match:
        return "file", match[1], match[2], None

    # Legacy formats: #F!<handle>!<key> and #!<handle>!<key>
    match = re.search(r"mega\.nz/#F!([^!]+)!([^!/?]+)", url)
    if match:
        return "folder", match[1], match[2], None

    match = re.search(r"mega\.nz/#!([^!]+)!([^!/?]+)", url)
    if match:
        return "file", match[1], match[2], None

    raise MegaError(f"Unrecognised MEGA URL: {url}")


def list_folder(folder_handle, folder_key_b64):
    """Return every file node in a public folder as dicts with handle, name, size, key and nonce."""
    if folder_handle in _folder_cache:
        return _folder_cache[folder_handle]

    folder_key = _b64decode(folder_key_b64)
    result = _api_request({"a": "f", "c": 1, "r": 1, "ca": 1}, folder_handle)

    files = []
    for node in result.get("f", []):
        # t=0 is a file; folders and special nodes aren't downloadable
        if node.get("t") != 0 or ":" not in node.get("k", ""):
            continue

        # k is "<owner handle>:<encrypted key>[/<owner handle>:<encrypted key>...]". Only the pair belonging to
        # the shared root folder is wrapped with the folder key, so try each until the attributes decrypt
        attributes = None
        for pair in node["k"].split("/"):
            if ":" not in pair:
                continue
            full_key = _decrypt_key(_b64decode(pair.split(":", 1)[1]), folder_key)
            if len(full_key) != 32:
                continue
            aes_key, nonce, meta_mac = _file_key_parts(full_key)
            try:
                attributes = _decrypt_attributes(node["a"], aes_key)
                break
            except Exception:
                continue

        if attributes is None:
            print(f"\tSkipping MEGA node {node.get('h')}: couldn't decrypt it with the folder key")
            continue

        files.append({
            "handle": node["h"],
            "name": attributes.get("n"),
            "size": node.get("s"),
            "key": aes_key,
            "nonce": nonce,
            "meta_mac": meta_mac,
        })

    _folder_cache[folder_handle] = files

    return files


def get_file_info(file_handle, file_key_b64):
    """Return name, size, key and nonce for a public file link."""
    full_key = _b64decode(file_key_b64)
    aes_key, nonce, meta_mac = _file_key_parts(full_key)
    result = _api_request({"a": "g", "p": file_handle, "ssl": 2})
    attributes = _decrypt_attributes(result["at"], aes_key)

    return {
        "handle": file_handle,
        "name": attributes.get("n"),
        "size": result.get("s"),
        "key": aes_key,
        "nonce": nonce,
        "meta_mac": meta_mac,
    }


def _chunk_boundaries(size):
    """MEGA MACs files in chunks of 128KB, 256KB, ... up to 1MB, then 1MB each"""
    position = 0
    chunk_size = 0x20000
    while position < size:
        yield position, min(chunk_size, size - position)
        position += chunk_size
        if chunk_size < 0x100000:
            chunk_size += 0x20000


def download(url, destination):
    """Download a public MEGA file (or a file inside a public folder) to destination, decrypted and MAC-verified."""
    kind, handle, key, node = parse_url(url)

    if kind == "folder":
        if node is None:
            raise MegaError(f"MEGA folder URL has no file node: {url}")
        files = {f["handle"]: f for f in list_folder(handle, key)}
        if node not in files:
            raise MegaError(f"File {node} not found in MEGA folder {handle}")
        info = files[node]
        result = _api_request({"a": "g", "g": 1, "n": node, "ssl": 2}, handle)
    else:
        info = get_file_info(handle, key)
        result = _api_request({"a": "g", "g": 1, "p": handle, "ssl": 2})

    if "g" not in result:
        raise MegaError(f"MEGA didn't return a download URL for {url}: {result}")

    size = result.get("s", info["size"])
    aes_key, nonce = info["key"], info["nonce"]

    decryptor = AES.new(aes_key, AES.MODE_CTR, nonce=nonce, initial_value=0)
    mac_iv = nonce + nonce
    file_mac = b"\0" * 16
    mac_cipher = AES.new(aes_key, AES.MODE_ECB)

    boundaries = list(_chunk_boundaries(size))
    chunk_index = 0
    pending = b""
    written = 0

    def mac_chunk(chunk):
        # A chunk's MAC is the last block of AES-CBC over it, with the IV built from the nonce
        padded = chunk + b"\0" * (-len(chunk) % 16)
        return AES.new(aes_key, AES.MODE_CBC, iv=mac_iv).encrypt(padded)[-16:]

    with http.get(result["g"], stream=True) as response:
        response.raise_for_status()
        with open(destination, "wb") as f:
            for encrypted in response.iter_content(chunk_size=0x100000):
                plain = decryptor.decrypt(encrypted)
                f.write(plain)
                written += len(plain)
                pending += plain

                # Fold every complete MAC chunk into the file MAC
                while chunk_index < len(boundaries) and len(pending) >= boundaries[chunk_index][1]:
                    length = boundaries[chunk_index][1]
                    chunk_mac = mac_chunk(pending[:length])
                    file_mac = mac_cipher.encrypt(bytes(a ^ b for a, b in zip(file_mac, chunk_mac)))
                    pending = pending[length:]
                    chunk_index += 1

    if written != size:
        raise MegaError(f"MEGA download of {url} was {written} bytes, expected {size}")

    words = _to_words(file_mac)
    meta_mac = _from_words((words[0] ^ words[1], words[2] ^ words[3]))
    if meta_mac != info["meta_mac"]:
        raise MegaError(f"MEGA MAC mismatch for {url}, the download is corrupt")
