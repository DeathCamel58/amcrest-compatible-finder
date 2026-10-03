import os
import threading
import time

from requests import HTTPError

from util import http

DOWNLOAD_ATTEMPTS = 4

# The same file is often listed by more than one vendor module, so only let one thread download a given file
_file_locks = {}
_file_locks_lock = threading.Lock()


def _get_file_lock(file_name):
    with _file_locks_lock:
        return _file_locks.setdefault(file_name, threading.Lock())


def download_firmware(url, file_name=None, downloader=None):
    """Download url to file_name. downloader(url, part_name) can override how the file is fetched."""
    if file_name is None:
        file_name = f'firmware/{url.split("/")[-1].split("?")[0]}'
    # Download to a .part file first so a dropped connection never leaves a truncated firmware behind
    part_name = f'{file_name}.part'
    print(f"Downloading URL: {url}")

    with _get_file_lock(file_name):
        # Another thread may have finished downloading it while we waited
        if os.path.exists(file_name):
            return None

        return _download_to(url, file_name, part_name, downloader or _http_download)


def _http_download(url, part_name):
    with http.get(url, stream=True) as r:
        r.raise_for_status()
        with open(part_name, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)


def _download_to(url, file_name, part_name, downloader):
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            downloader(url, part_name)
            # A server can answer 200 with no body; an empty file would block a real download forever
            if os.path.getsize(part_name) == 0:
                raise ValueError(f'empty download from {url}')
            os.replace(part_name, file_name)
            return file_name
        except HTTPError as http_err:
            # Retryable statuses were already retried by the session, so don't bother again
            print(f'\tHTTP error occurred: {http_err}')
            break
        except Exception as err:
            print(f'\tAn error occurred (attempt {attempt}/{DOWNLOAD_ATTEMPTS}): {err}')
            if attempt < DOWNLOAD_ATTEMPTS:
                time.sleep(10 * attempt)

    if os.path.exists(part_name):
        os.remove(part_name)

    return None


# Download URL -> MD5 of an earlier download that didn't match the vendor's published checksum
_mismatched_md5 = {}
_mismatched_md5_lock = threading.Lock()


def check_published_md5(url, actual, expected):
    """Raise (so the download is retried) when a download doesn't match the vendor's published MD5, unless an
    earlier download gave the same bytes: then the file is stable and the published value is what's wrong, so the
    file is kept (enrich flags the entry with vendor_md5_mismatch)."""
    if not expected or actual == expected.lower():
        return
    with _mismatched_md5_lock:
        if _mismatched_md5.get(url) == actual:
            print(f'\tKeeping {url}: two downloads match each other ({actual}) but not the published MD5 {expected}')
            return
        _mismatched_md5[url] = actual
    raise ValueError(f'MD5 mismatch for {url}: got {actual}, expected {expected}')
