import json
import os
import re
import time
from datetime import date
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup

from util import http

# Dahua firmware names start with a Dahua prefix, or carry a Dahua style version string like V2.800.0000000.11.R.230626
DAHUA_PREFIX = re.compile(r'(^|_)(General|DH|DHI|Customer|Group)_', re.IGNORECASE)
# The build date is yymmdd or yyyymmdd; a 7-digit stamp (e.g. ...R.2303031) is something else
DAHUA_VERSION = re.compile(r'V?(\d\.\d{3}\.[0-9A-Za-z]+\.\d+(?:\.[A-Z])?)\.(\d{8}|\d{6})(?!\d)')

FIRMWARE_EXTENSIONS = ('.bin', '.zip', '.img', '.rar', '.7z')

# Software, tools and documents that use Dahua style names but aren't firmware
NOT_FIRMWARE = re.compile(
    r'pss|configtool|config_tool|client|player|plugin|manual|toolbox|dss|_IS_|upgradetool|guide|\.pdf$|\.exe$',
    re.IGNORECASE,
)

# File names too generic to be unique across vendors
GENERIC_NAME = re.compile(r'^(update|upgrade|firmware|fw|all|dav)?[\s_-]*(v?[\d.]+)?\.(bin|zip|img|rar|7z)$', re.IGNORECASE)


def is_dahua_firmware_name(file_name):
    if NOT_FIRMWARE.search(file_name) or not file_name.lower().endswith(FIRMWARE_EXTENSIONS):
        return False
    return bool(DAHUA_PREFIX.search(file_name) or DAHUA_VERSION.search(file_name))


def parse_dahua_version(file_name):
    """Returns (version, release_date) from a Dahua style file name, either can be None"""
    match = DAHUA_VERSION.search(file_name)
    if not match:
        return None, None

    version = f"V{match[1]}"
    date = match[2]
    if len(date) == 6:
        date = f"20{date}"
    release_date = f"{date[0:4]}-{date[4:6]}-{date[6:8]}"

    return version, release_date


def sanitize_name(name):
    name = re.sub(r'[\\/:*?"<>|,&\']+', '_', name)
    name = re.sub(r'\s+', '_', name.strip())
    return re.sub(r'_+', '_', name)


def is_generic_name(file_name):
    return bool(GENERIC_NAME.match(file_name))


def load_index_cache(cache_file):
    try:
        with open(cache_file) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def save_index_cache(cache_file, cache):
    directory = os.path.dirname(cache_file)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temp_file = f"{cache_file}.{os.getpid()}.tmp"
    with open(temp_file, "w") as f:
        json.dump(cache, f, indent=1, sort_keys=True)
    os.replace(temp_file, cache_file)


def link_info(link):
    """The text next to a link in a directory index (date and size on Apache, nginx and IIS listings), as shown."""
    row = link.find_parent("tr")
    if row is not None:
        text = row.get_text(" ")
        text = text.replace(link.get_text(), "", 1)
    else:
        sibling = link.next_sibling
        text = sibling if isinstance(sibling, str) else ""
    return " ".join(text.split()) or None


def crawl_index(start_url, max_depth=8, is_directory=None, headers=None, skip_directories=(), delay=0,
                max_failures=None, timeout=None, cache_file=None, cache_days=14, save_every=20, offline=False):
    """Recursively crawl a web server directory index (Apache, IIS, ...).
    Returns a list of (file_url, directory_url) for every file found below start_url.

    For servers that rate limit: delay is the seconds to wait between requests, and after max_failures listings in a
    row fail (errors, 429s or 5xxs) the crawl stops fetching, so a dead host can't stall a run. timeout overrides
    http.TIMEOUT for each listing.

    For slow servers: cache_file keeps each directory's listing (files with the date/size text next to them, and
    subdirectories) with the date it was fetched. Listings fresher than cache_days are reused without a request,
    except the start URL and its top-level folders, which are always fetched so new folders show up. When a fetch
    fails, or after the failure cutoff, the cached listing is used instead. offline lists only from the cache (for a
    host that isn't answering)."""
    if is_directory is None:
        def is_directory(href):
            return href.endswith('/')

    cache = load_index_cache(cache_file) if cache_file else None
    today = date.today()
    files = []
    seen = set()
    state = {"requests": 0, "failures": 0, "aborted": offline, "unsaved": 0, "from_cache": 0}
    request_options = {"headers": headers or {}}
    if timeout is not None:
        request_options["timeout"] = timeout

    def failed():
        state["failures"] += 1
        if max_failures is not None and state["failures"] >= max_failures and not state["aborted"]:
            state["aborted"] = True
            print(f"\tGiving up on {start_url} after {state['failures']} failed listings in a row "
                  f"({len(files)} files found so far)" + ("; using cached listings for the rest" if cache else ""))

    def fetch(url):
        """{"files": [[url, info]], "directories": [[url, href]]}, or None if the listing couldn't be fetched."""
        if delay and state["requests"]:
            time.sleep(delay)
        state["requests"] += 1
        try:
            page = http.get(url, **request_options)
        except Exception as err:
            print(f"\tFailed to list {url}: {err}")
            failed()
            return None
        if page.status_code != 200:
            print(f"\tGot HTTP {page.status_code} listing {url}")
            if page.status_code == 429 or page.status_code >= 500:
                failed()
            return None
        state["failures"] = 0

        listing = {"files": [], "directories": []}
        soup = BeautifulSoup(page.content, "html.parser")
        for link in soup.find_all("a", href=True):
            href = link["href"]
            # Skip sort links and anything that leads back up the tree
            if href.startswith("?"):
                continue
            full_url = urljoin(url, href)
            if not full_url.startswith(url) or full_url == url:
                continue
            if is_directory(href):
                listing["directories"].append([full_url, href])
            else:
                listing["files"].append([full_url, link_info(link) if cache is not None else None])
        return listing

    def is_fresh(entry):
        try:
            return (today - date.fromisoformat(entry["fetched"])).days <= cache_days
        except (KeyError, TypeError, ValueError):
            return False

    def crawl(url, depth):
        if url in seen or depth > max_depth:
            return
        seen.add(url)

        cached = cache.get(url) if cache is not None else None
        listing = None
        if cached and depth > 1 and is_fresh(cached):
            listing = cached
            state["from_cache"] += 1
        elif not state["aborted"]:
            listing = fetch(url)
            if listing is not None and cache is not None:
                cache[url] = {"fetched": today.isoformat(), **listing}
                state["unsaved"] += 1
                if state["unsaved"] >= save_every:
                    save_index_cache(cache_file, cache)
                    state["unsaved"] = 0
        if listing is None and cached:
            if not state["aborted"]:
                print(f"\tUsing the cached listing of {url} from {cached.get('fetched')}")
            listing = cached
            state["from_cache"] += 1
        if listing is None:
            return

        for file_url, _ in listing["files"]:
            files.append((file_url, url))
        for directory_url, href in listing["directories"]:
            if unquote(href).strip('/') not in skip_directories:
                crawl(directory_url, depth + 1)

    try:
        crawl(start_url, 0)
    finally:
        if cache is not None and state["unsaved"]:
            save_index_cache(cache_file, cache)
    if cache is not None:
        print(f"\tListed {start_url}: {state['requests']} requests, {state['from_cache']} directories from the cache")
    return files


def get_content_disposition_name(response):
    disposition = response.headers.get("Content-Disposition", "")
    match = re.search(r"filename\*=(?:UTF-8'')?([^;]+)", disposition, re.IGNORECASE)
    if match:
        return unquote(match[1].strip().strip('"'))
    match = re.search(r'filename="?([^";]+)"?', disposition, re.IGNORECASE)
    if match:
        return match[1].strip()
    return None


def resolve_download(url):
    """Follow redirects without downloading the body. Returns (final_url, file_name) or (None, None)."""
    try:
        with http.get(url, stream=True, allow_redirects=True) as response:
            if response.status_code != 200:
                print(f"\tGot HTTP {response.status_code} resolving {url}")
                return None, None
            file_name = get_content_disposition_name(response) or unquote(response.url.split("/")[-1].split("?")[0])
            return response.url, file_name
    except Exception as err:
        print(f"\tFailed to resolve {url}: {err}")
        return None, None


def resolve_downloads(urls, workers=8):
    """Resolve many download URLs concurrently. Returns {url: (final_url, file_name)}"""
    urls = list(dict.fromkeys(urls))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(zip(urls, pool.map(resolve_download, urls)))


def make_firmware(camera_name, url, file_name=None, notes=None, version=None, release_date=None):
    """Build a firmware entry, filling version and release date from a Dahua style file name when not given"""
    name_for_version = file_name or unquote(url.split("/")[-1].split("?")[0])
    parsed_version, parsed_date = parse_dahua_version(name_for_version)

    firmware = {
        "camera_name": camera_name,
        "firmware_version": version or parsed_version,
        "firmware_size": None,
        "firmware_notes": notes,
        "firmware_changelog": None,
        "firmware_previous": None,
        "firmware_latest": url,
        "release_date": release_date or parsed_date,
    }
    if file_name:
        firmware["firmware_latest_file_name"] = file_name

    return firmware
