import re
from urllib.parse import unquote

from bs4 import BeautifulSoup

from util import http
from util.oem_helpers import DAHUA_VERSION, FIRMWARE_EXTENSIONS, make_firmware, sanitize_name

name = "Speco"
vendor = "Speco"

# Lists a release notes page per recorder series, which is where the firmware links are
firmware_site = "https://specotech.com/recorder-software-updates-2/"

# The WordPress media library also holds firmware that no page links to (e.g. Dahua based IP cameras)
media_api = "https://specotech.com/wp-json/wp/v2/media"
media_search_terms = ["firmware", ".bin", ".zip", ".dav", "update"]


def get_release_notes_pages():
    page = http.get(firmware_site)
    soup = BeautifulSoup(page.content, "html.parser")

    pages = []
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if re.search(r'specotech\.com/[^/]+-software-release-notes/?$', href) and href not in pages:
            pages.append(href)

    return pages


def parse_dahua_file_name(file_name):
    """Speco only uses Dahua for some products, so only files with a Dahua style version string count.
    Returns (version, release_date) or None."""
    match = DAHUA_VERSION.search(file_name.upper())
    if not match:
        return None
    date = match[2] if len(match[2]) == 8 else f"20{match[2]}"
    return f"V{match[1]}", f"{date[0:4]}-{date[4:6]}-{date[6:8]}"


def get_media_items():
    """Every media library item matching the search terms (the API has no "list everything" for files)."""
    items = {}
    for term in media_search_terms:
        page = 1
        while True:
            response = http.get(media_api, params={"per_page": 100, "search": term, "page": page})
            if response.status_code != 200:
                print(f"\tSpeco media API returned {response.status_code} for {term!r}")
                break
            for item in response.json():
                items[item["id"]] = item
            if page >= int(response.headers.get("X-WP-TotalPages", "1")):
                break
            page += 1
    return list(items.values())


def get_media_firmwares(seen_urls):
    firmwares = []
    for item in get_media_items():
        url = item.get("source_url") or ""
        file_name = unquote(url.split("/")[-1])
        if url in seen_urls or not file_name.lower().endswith(FIRMWARE_EXTENSIONS):
            continue
        parsed = parse_dahua_file_name(file_name)
        if not parsed:
            continue
        seen_urls.add(url)
        version, release_date = parsed

        # Titles look like "O4P4X_V2.623.00SP004.0.R.200427.bin" or "O6MDP2 O6MDP2W_V2.622.00SP000.0.R.200107"
        title = BeautifulSoup(item.get("title", {}).get("rendered", ""), "html.parser").get_text()
        model_part = re.split(r"[_\s-]?v\d\.\d{3}", title or file_name, maxsplit=1, flags=re.IGNORECASE)[0]
        models = [model.upper() for model in re.split(r"[\s,/-]+", model_part) if model]

        firmwares.append(make_firmware(
            models,
            url,
            sanitize_name(f"Speco_{'-'.join(models) or 'media'}_{file_name}"),
            version=version,
            release_date=release_date,
        ))
    return firmwares


def get_firmwares():
    firmwares = []
    seen_urls = set()

    for page_url in get_release_notes_pages():
        # e.g. https://specotech.com/nxl-series-software-release-notes/ -> NXL
        series = page_url.rstrip("/").split("/")[-1].replace("-software-release-notes", "").replace("-series", "").upper()

        page = http.get(page_url)
        soup = BeautifulSoup(page.content, "html.parser")
        for link in soup.find_all("a", href=True):
            url = link["href"]
            file_name = unquote(url.split("/")[-1])
            if url in seen_urls or "/wp-content/uploads/" not in url or not file_name.lower().endswith(FIRMWARE_EXTENSIONS):
                continue
            # Most Speco recorders aren't Dahua, so only keep files with a Dahua style version string
            match = DAHUA_VERSION.search(file_name.upper())
            if not match:
                continue
            seen_urls.add(url)

            version = f"V{match[1]}"
            date = match[2] if len(match[2]) == 8 else f"20{match[2]}"
            release_date = f"{date[0:4]}-{date[4:6]}-{date[6:8]}"

            # Names like v3.215.00sp000.0.r.20190326.bin_.zip don't say what they're for
            firmwares.append(make_firmware(
                f"{series} Series",
                url,
                sanitize_name(f"Speco_{series}_{file_name}"),
                version=version,
                release_date=release_date,
            ))

    # Files only in the media library, skipping ones the release notes pages already link to
    firmwares += get_media_firmwares(seen_urls)

    return firmwares
