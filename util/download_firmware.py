import errno
import json
import os
import re
import threading
import time
from datetime import date, datetime
from urllib.parse import urlparse

from requests import HTTPError

from util import http

DOWNLOAD_ATTEMPTS = 4
# A server that answers with nothing twice in a row isn't going to send the file on a third try
EMPTY_DOWNLOAD_ATTEMPTS = 2

# Download URLs that failed in a way retrying won't fix (404, 403, a MEGA link that no longer exists, an empty
# response, a file Google Drive flags as infected), with the number of separate runs they failed in
FAILED_DOWNLOADS_FILE = 'tmp/failed_downloads.json'
# Download URLs whose file turned out not to be firmware (software, documents) and was moved out of firmware/
REJECTED_DOWNLOADS_FILE = 'tmp/rejected_downloads.json'
# A URL that failed permanently in this many different runs isn't tried again
SKIP_AFTER_FAILED_RUNS = 2
PERMANENT_HTTP_STATUSES = {403, 404, 410}
# A live download that crawls below the minimum rate this many times is fetched from the Wayback Machine's copy of
# the same URL instead (when there is one), rather than retried from a server that's throttling us
SLOW_ATTEMPTS_BEFORE_WAYBACK = 2
# A slow download that's still making progress resumes this many times without using up its attempts
MAX_SLOW_RESUMES = 50
# A .part file left by an earlier run is resumed if it was written within this many seconds
RESUME_PART_MAX_AGE = 2 * 24 * 3600
# Minimum download rates (bytes/s) for servers that are slow but steady; others use the default in util.http.
# Eltrox sends 35-45 KB/s per connection, just under the default, so every download was aborted as too slow
MIN_RATE_BY_HOST = {'ftp.eltrox.pl': 10_000, 'ftp.cifra.cv.ua': 4_000}
# A dead URL's Wayback copy is looked for again after this many days
WAYBACK_RECHECK_DAYS = 30
# Servers that throttle us to a few KB/s: their files are fetched from the Wayback Machine's copy first, when it has
# one (about 70 KB/s against Cifra's 10 KB/s)
WAYBACK_FIRST_HOSTS = {'ftp.cifra.cv.ua'}
# Download URL -> the Wayback capture its file was downloaded from instead; main records it as downloaded_from
downloaded_via_wayback = {}

# Identifies this run in FAILED_DOWNLOADS_FILE, so a URL failing several times in one run counts once
RUN_ID = datetime.now().isoformat(timespec='seconds')

# The same file is often listed by more than one vendor module, so only let one thread download a given file
_file_locks = {}
_file_locks_lock = threading.Lock()

_registry_lock = threading.Lock()
_registries = {}

# Set after MEGA's first "509 Bandwidth Limit Exceeded" in a run: its anonymous quota is per IP and lasts hours, so
# the rest of the run's MEGA downloads would only fail too
_mega_quota_exceeded = threading.Event()


def _get_file_lock(file_name):
    with _file_locks_lock:
        return _file_locks.setdefault(file_name, threading.Lock())


def _load_registry(path):
    if path not in _registries:
        try:
            with open(path) as f:
                _registries[path] = json.load(f)
        except (FileNotFoundError, ValueError):
            _registries[path] = {}
    return _registries[path]


def _save_registry(path):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    temp_path = f'{path}.{os.getpid()}.tmp'
    with open(temp_path, 'w') as f:
        json.dump(_registries[path], f, indent=1, sort_keys=True)
    os.replace(temp_path, path)


def record_failed_download(url, reason):
    """Remember a download that failed for good (the URL is dead, or the server refuses it)."""
    with _registry_lock:
        failed = _load_registry(FAILED_DOWNLOADS_FILE)
        entry = failed.setdefault(url, {'runs': 0})
        if entry.get('last_run') != RUN_ID:
            entry['runs'] = entry.get('runs', 0) + 1
            entry['last_run'] = RUN_ID
        entry['reason'] = reason
        entry['last_date'] = date.today().isoformat()
        _save_registry(FAILED_DOWNLOADS_FILE)


def clear_failed_download(url):
    with _registry_lock:
        failed = _load_registry(FAILED_DOWNLOADS_FILE)
        if failed.pop(url, None) is not None:
            _save_registry(FAILED_DOWNLOADS_FILE)


def record_rejected_download(url, file_name, reason):
    """Remember a download whose file wasn't firmware, so it isn't downloaded again."""
    if not url:
        return
    with _registry_lock:
        rejected = _load_registry(REJECTED_DOWNLOADS_FILE)
        rejected[url] = {'file_name': file_name, 'reason': reason, 'date': date.today().isoformat()}
        _save_registry(REJECTED_DOWNLOADS_FILE)


def is_rejected(url=None, file_name=None):
    with _registry_lock:
        rejected = _load_registry(REJECTED_DOWNLOADS_FILE)
        if url and url in rejected:
            return True
        return bool(file_name) and any(entry.get('file_name') == file_name for entry in rejected.values())


def skip_reason(url):
    """Why this URL shouldn't be downloaded, or None."""
    with _registry_lock:
        if url in _load_registry(REJECTED_DOWNLOADS_FILE):
            return 'not firmware'
        failed = _load_registry(FAILED_DOWNLOADS_FILE).get(url)
    if failed and failed.get('runs', 0) >= SKIP_AFTER_FAILED_RUNS:
        return f"failed in {failed['runs']} runs ({failed.get('reason')})"
    if is_mega(url) and _mega_quota_exceeded.is_set():
        return 'MEGA quota exceeded this run'
    return None


def is_mega(url):
    host = urlparse(url or '').hostname or ''
    return host == 'mega.nz' or host.endswith(('.mega.nz', '.mega.co.nz'))


def _http_status(err):
    response = getattr(err, 'response', None)
    if response is not None and getattr(response, 'status_code', None):
        return response.status_code
    match = re.match(r'\s*(\d{3})\b', str(err))
    return int(match.group(1)) if match else None


def _permanent_failure(err):
    """A reason when an error means retrying (now or in a later run) won't help, otherwise None."""
    text = str(err)
    status = _http_status(err)
    if status in PERMANENT_HTTP_STATUSES:
        return f'HTTP {status}'
    if 'infected' in text.lower():
        return 'flagged as infected by Google Drive'
    if re.search(r'MEGA API error -9\b', text):
        return 'MEGA link no longer exists'
    return None


def wayback_capture(url):
    """The Wayback Machine's most recent capture of url that isn't an HTML page, as a raw (id_) download URL, or
    None. MEGA and Google Drive links aren't captured usefully, so they aren't looked up."""
    host = urlparse(url).hostname or ''
    if not host or host == 'web.archive.org' or is_mega(url) or 'google.com' in host or 'sharepoint.com' in host:
        return None
    try:
        response = http.get('https://web.archive.org/cdx/search/cdx', timeout=60, params={
            'url': url, 'output': 'json', 'fl': 'timestamp,original',
            'filter': ['statuscode:200', '!mimetype:text/html'], 'limit': '-1'})
        rows = response.json()[1:] if response.ok and response.text.strip() else []
    except Exception as err:
        print(f'\tCould not look up a Wayback copy of {url}: {err!r}')
        return None
    if not rows:
        return None
    timestamp, original = rows[-1][:2]
    return f'https://web.archive.org/web/{timestamp}id_/{original}'


def _download_from_wayback(url, file_name, part_name=None):
    """Download the Wayback Machine's copy of url to file_name. Returns file_name, or None if there's no usable copy.
    Uses its own .part file, so a partial download from the live server is kept to resume if there's no copy."""
    capture = wayback_capture(url)
    if capture is None:
        return None
    print(f'\tDownloading the Wayback Machine\'s copy instead: {capture}')
    part_name = f'{file_name}.wayback.part'
    if os.path.exists(part_name):
        os.remove(part_name)
    for attempt in range(1, 3):
        try:
            _http_download(capture, part_name)
            with open(part_name, 'rb') as f:
                head = f.read(512)
            if not head:
                raise ValueError('empty download')
            if head.lstrip()[:15].lower().startswith((b'<!doctype html', b'<html')):
                raise ValueError('the capture is an HTML page, not the file')
            os.replace(part_name, file_name)
            downloaded_via_wayback[url] = capture
            return file_name
        except Exception as err:
            print(f'\tWayback download failed (attempt {attempt}/2): {err}')
            if isinstance(err, ValueError):
                break
    if os.path.exists(part_name):
        os.remove(part_name)
    return None


def download_dead_url_from_wayback(url, file_name):
    """For a URL earlier runs gave up on (404, refused): download the Wayback Machine's copy, at most once every
    WAYBACK_RECHECK_DAYS. Returns file_name, or None."""
    with _registry_lock:
        entry = _load_registry(FAILED_DOWNLOADS_FILE).get(url) or {}
        checked = entry.get('wayback_checked')
    if checked and (date.today() - date.fromisoformat(checked)).days < WAYBACK_RECHECK_DAYS:
        return None
    with _get_file_lock(file_name):
        if os.path.exists(file_name):
            return None
        result = _download_from_wayback(url, file_name, f'{file_name}.part')
    with _registry_lock:
        failed = _load_registry(FAILED_DOWNLOADS_FILE)
        if result:
            failed.pop(url, None)
        elif url in failed:
            failed[url]['wayback_checked'] = date.today().isoformat()
        _save_registry(FAILED_DOWNLOADS_FILE)
    return result


def download_firmware(url, file_name=None, downloader=None):
    """Download url to file_name. downloader(url, part_name) can override how the file is fetched."""
    if file_name is None:
        file_name = f'firmware/{url.split("/")[-1].split("?")[0]}'
    # Download to a .part file first so a dropped connection never leaves a truncated firmware behind
    part_name = f'{file_name}.part'

    if is_mega(url) and _mega_quota_exceeded.is_set():
        return None

    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Downloading URL: {url}")

    with _get_file_lock(file_name):
        # Another thread may have finished downloading it while we waited
        if os.path.exists(file_name):
            return None

        if (urlparse(url).hostname or '') in WAYBACK_FIRST_HOSTS and _download_from_wayback(url, file_name, part_name):
            return file_name
        return _download_to(url, file_name, part_name, downloader or _http_download)


def resume_offset(response, have):
    """Where a ranged response starts, if it continues a partial file of `have` bytes; else 0 (start over)."""
    if have <= 0 or response.status_code != 206:
        return 0
    match = re.match(r"bytes (\d+)-\d+/(\d+|\*)", response.headers.get("Content-Range", ""))
    return have if match and int(match.group(1)) == have else 0


def _http_download(url, part_name):
    """Download url to part_name. A retry continues the .part an earlier attempt left (an HTTP Range request) when
    the server supports it, so a slow server's big file isn't restarted from zero every time."""
    have = os.path.getsize(part_name) if os.path.exists(part_name) else 0
    headers = {"Range": f"bytes={have}-", "Accept-Encoding": "identity"} if have else {}
    with http.get(url, stream=True, headers=headers) as r:
        if have and r.status_code == 416:
            return  # the .part already holds the whole file
        r.raise_for_status()
        offset = resume_offset(r, have)
        if have:
            print(f"\tResuming {os.path.basename(part_name)} at {offset} bytes" if offset
                  else f"\tThe server can't resume {os.path.basename(part_name)}; starting over")
        rate = MIN_RATE_BY_HOST.get(urlparse(url).hostname or '')
        with open(part_name, 'ab' if offset else 'wb') as f:
            # A download that crawls (e.g. one stuck at a few KB/s for hours) is aborted and retried
            for chunk in (http.iter_content_with_min_rate(r, min_bytes_per_sec=rate) if rate
                          else http.iter_content_with_min_rate(r)):
                f.write(chunk)


def _note_mega_quota(err, url):
    if _http_status(err) == 509 and (is_mega(url) or 'mega' in str(err).lower()) \
            and not _mega_quota_exceeded.is_set():
        _mega_quota_exceeded.set()
        print("\tMEGA bandwidth quota exceeded; skipping the rest of this run's MEGA downloads")


def _download_to(url, file_name, part_name, downloader):
    # A .part left by a recent run (e.g. one restarted mid-download) is resumed; an older one may be of a version of
    # the file the server has since replaced, so it starts over
    if os.path.exists(part_name) and time.time() - os.path.getmtime(part_name) > RESUME_PART_MAX_AGE:
        os.remove(part_name)
    empty_responses = 0
    slow_attempts = 0
    slow_resumes = 0
    wayback_tried = False
    attempt = 0
    while attempt < DOWNLOAD_ATTEMPTS:
        attempt += 1
        progress = os.path.getsize(part_name) if os.path.exists(part_name) else 0
        try:
            downloader(url, part_name)
            # A server can answer 200 with no body; an empty file would block a real download forever
            if os.path.getsize(part_name) == 0:
                empty_responses += 1
                if empty_responses >= EMPTY_DOWNLOAD_ATTEMPTS:
                    print(f'\tEmpty download from {url} ({empty_responses} times); giving up')
                    record_failed_download(url, 'empty response')
                    break
                raise ValueError(f'empty download from {url}')
            os.replace(part_name, file_name)
            clear_failed_download(url)
            return file_name
        except HTTPError as http_err:
            # Retryable statuses were already retried by the session, so don't bother again
            print(f'\tHTTP error occurred: {http_err}')
            _note_mega_quota(http_err, url)
            reason = _permanent_failure(http_err)
            if reason:
                record_failed_download(url, reason)
            break
        except OSError as err:
            if err.errno == errno.ENAMETOOLONG:
                # The name won't get shorter by trying again
                print(f'\tFile name too long, not retrying: {err}')
                break
            print(f'\tAn error occurred (attempt {attempt}/{DOWNLOAD_ATTEMPTS}): {err}')
            # A dropped connection on a big file (requests' errors are OSErrors): carry on from where it got to
            # without using up an attempt
            if os.path.exists(part_name) and os.path.getsize(part_name) > progress and slow_resumes < MAX_SLOW_RESUMES:
                slow_resumes += 1
                attempt -= 1
                time.sleep(10)
                continue
            if attempt < DOWNLOAD_ATTEMPTS:
                time.sleep(10 * attempt)
        except Exception as err:
            print(f'\tAn error occurred (attempt {attempt}/{DOWNLOAD_ATTEMPTS}): {err}')
            _note_mega_quota(err, url)
            reason = _permanent_failure(err)
            if reason:
                record_failed_download(url, reason)
                break
            if _mega_quota_exceeded.is_set() and is_mega(url):
                break
            # A dropped connection on a big file: carry on from where it got to without using up an attempt
            if (not isinstance(err, http.SlowDownloadError) and os.path.exists(part_name)
                    and os.path.getsize(part_name) > progress and slow_resumes < MAX_SLOW_RESUMES):
                slow_resumes += 1
                attempt -= 1
                time.sleep(10)
                continue
            if isinstance(err, http.SlowDownloadError):
                slow_attempts += 1
                if slow_attempts >= SLOW_ATTEMPTS_BEFORE_WAYBACK and not wayback_tried:
                    # The server is throttling us: the Wayback Machine's copy may be faster
                    wayback_tried = True
                    result = _download_from_wayback(url, file_name)
                    if result:
                        if os.path.exists(part_name):
                            os.remove(part_name)
                        clear_failed_download(url)
                        return result
                # Still sending, just slowly: keep resuming without using up an attempt
                if (os.path.exists(part_name) and os.path.getsize(part_name) > progress
                        and slow_resumes < MAX_SLOW_RESUMES):
                    slow_resumes += 1
                    attempt -= 1
                    continue
            if attempt < DOWNLOAD_ATTEMPTS:
                time.sleep(10 * attempt)

    # The live server failed: use the Wayback Machine's copy of the same URL if it has one
    if not wayback_tried:
        result = _download_from_wayback(url, file_name)
        if result:
            if os.path.exists(part_name):
                os.remove(part_name)
            clear_failed_download(url)
            return result

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
