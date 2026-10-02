import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup

from util import http

# Dahua firmware names start with a Dahua prefix, or carry a Dahua style version string like V2.800.0000000.11.R.230626
DAHUA_PREFIX = re.compile(r'(^|_)(General|DH|DHI|Customer|Group)_', re.IGNORECASE)
DAHUA_VERSION = re.compile(r'V?(\d\.\d{3}\.[0-9A-Za-z]+\.\d+(?:\.[A-Z])?)\.(\d{6,8})')

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


def crawl_index(start_url, max_depth=8, is_directory=None, headers=None, skip_directories=()):
    """Recursively crawl a web server directory index (Apache, IIS, ...).
    Returns a list of (file_url, directory_url) for every file found below start_url."""
    if is_directory is None:
        def is_directory(href):
            return href.endswith('/')

    files = []
    seen = set()

    def crawl(url, depth):
        if url in seen or depth > max_depth:
            return
        seen.add(url)

        try:
            page = http.get(url, headers=headers or {})
        except Exception as err:
            print(f"\tFailed to list {url}: {err}")
            return
        if page.status_code != 200:
            print(f"\tGot HTTP {page.status_code} listing {url}")
            return

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
                if unquote(href).strip('/') not in skip_directories:
                    crawl(full_url, depth + 1)
            else:
                files.append((full_url, url))

    crawl(start_url, 0)
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
