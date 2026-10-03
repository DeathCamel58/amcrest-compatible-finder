import json
import os
import re
import threading
import time
from datetime import date, datetime, timezone
from urllib.parse import unquote

from util import http
from util.oem_helpers import make_firmware, sanitize_name

name = "Intelbras"
vendor = "Intelbras"

sitemap_url = "https://www.intelbras.com/sitemap.xml"
download_page = "https://www.intelbras.com/pt-br/ajuda-download/download/{slug}"

# Product slugs for the Dahua-made video lines: VIP (IP cameras), VHD/MHDX/iMHDX (HDCVI cameras and DVRs),
# NVD (NVRs), speed domes, and recorders in general. IVP is alarm sensors, so not included
CCTV_SLUG = re.compile(
    r"(^|-)(vip|mhdx|imhdx|nvd|vhd|camera|gravador|dvr|nvr|xvr|speed-dome|ptz|sd)(-|$)")
NOT_CCTV_SLUG = re.compile(r"^(lente|acessorio|suporte|caixa|fonte|cabo|conector|balun|rack)")

FIRMWARE_EXTENSIONS = (".zip", ".bin", ".rar", ".img", ".dav", ".7z")

# Product pages are cached so only new (or stale) products are fetched with the browser on later runs
CACHE_FILE = "tmp/intelbras_pages.json"
CACHE_DAYS = 30
# Stale products re-fetched per run, so a refresh doesn't turn into another multi-hour run
REFRESH_BATCH = 50
SAVE_EVERY = 20

# Download URL -> the product page it came from (a same-site page to start browser downloads from)
pages_by_url = {}


def load_cache():
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    temp_file = f"{CACHE_FILE}.tmp"
    with open(temp_file, "w") as f:
        json.dump(cache, f, indent=1, sort_keys=True)
    os.replace(temp_file, CACHE_FILE)


def get_cctv_slugs():
    sitemap = http.get_protected_html(sitemap_url) or ""
    slugs = []
    for loc in re.findall(r"<loc>([^<]+)</loc>", sitemap):
        if not loc.startswith("https://www.intelbras.com/pt-br/"):
            continue
        slug = loc.rstrip("/").rsplit("/", 1)[-1]
        if CCTV_SLUG.search(slug) and not NOT_CCTV_SLUG.match(slug):
            slugs.append(slug)
    return sorted(set(slugs))


def extract_json_array(html, key):
    """The JSON array that follows "key": in the page's embedded state, by bracket matching."""
    start = html.find(f'"{key}":[')
    if start < 0:
        return None
    start += len(f'"{key}":')
    depth = 0
    in_string = escaped = False
    for position in range(start, len(html)):
        char = html[position]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return json.loads(html[start:position + 1])
    return None


def parse_page(html):
    """(product title, firmware downloads) from a product download page."""
    title = re.search(r'"productLoaded":\{.{0,400}?"title":"([^"]+)"', html)
    downloads = extract_json_array(html, "downloads") or []
    firmwares = [
        {key: item.get(key) for key in ("title", "fileName", "fileDate", "download")}
        for item in downloads
        if item.get("groupName") == "Firmware" and (item.get("fileName") or "").lower().endswith(FIRMWARE_EXTENSIONS)
    ]
    return (json.loads(f'"{title.group(1)}"') if title else None), firmwares


def is_challenge(html):
    # Cloudflare's challenge-platform script is on ordinary pages too, so go by the challenge page's title
    return "<title>Just a moment" in html[:5000]


def fetch_pages(slugs, cache):
    """Fetch product pages in one browser session (about 9 s each), saving progress as it goes."""
    if not slugs:
        return
    # One browser per worker, each taking every Nth page, all writing into the same cache
    # Leave one browser slot free, so other modules' Cloudflare pages (Amcrest, GSS, Winic) aren't blocked
    workers = max(1, min(http.BROWSER_SLOTS - 1, len(slugs)))
    print(f"\tIntelbras: fetching {len(slugs)} product pages with {workers} browsers "
          f"(about {len(slugs) * 9 // 60 // workers} minutes)")
    started = time.time()
    lock = threading.Lock()
    done = [0]

    def fetch_share(share):
        with http.protected_session(solve_cloudflare=False) as session:
            for slug in share:
                url = download_page.format(slug=slug)
                # Product pages usually aren't challenged; asking Scrapling to solve a challenge that isn't there logs
                # an error per page, so only solve one when the page turns out to be a challenge
                html = session.get_html(url, network_idle=False, disable_resources=True, solve_cloudflare=False)
                if html is not None and is_challenge(html):
                    html = session.get_html(url, network_idle=False, disable_resources=True, solve_cloudflare=True)
                if html is None or is_challenge(html):
                    continue  # not cached, so it's retried next run
                title, firmwares = parse_page(html)
                with lock:
                    cache[slug] = {"fetched": date.today().isoformat(), "title": title, "firmwares": firmwares}
                    done[0] += 1
                    if done[0] % SAVE_EVERY == 0:
                        save_cache(cache)
                        print(f"\tIntelbras: {done[0]}/{len(slugs)} pages "
                              f"({(time.time() - started) / done[0]:.1f} s each overall)")

    threads = [threading.Thread(target=fetch_share, args=(slugs[i::workers],)) for i in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    with lock:
        save_cache(cache)


def get_firmwares(max_pages=None):
    """max_pages (or the INTELBRAS_MAX_PAGES environment variable) limits how many product pages are fetched,
    for testing; the default fetches every product that isn't cached yet."""
    if max_pages is None and os.environ.get("INTELBRAS_MAX_PAGES"):
        max_pages = int(os.environ["INTELBRAS_MAX_PAGES"])

    cache = load_cache()
    slugs = get_cctv_slugs()

    today = date.today()
    new = [slug for slug in slugs if slug not in cache]
    stale = [slug for slug in slugs if slug in cache
             and (today - date.fromisoformat(cache[slug]["fetched"])).days > CACHE_DAYS]
    to_fetch = new + stale[:REFRESH_BATCH]
    if max_pages is not None:
        to_fetch = to_fetch[:max_pages]
    print(f"\tIntelbras: {len(slugs)} video products, {len(slugs) - len(new)} cached, fetching {len(to_fetch)}")
    fetch_pages(to_fetch, cache)

    # Keyed by download URL: several products often share one firmware file
    firmwares = {}
    for slug in slugs:
        page = cache.get(slug)
        if not page:
            continue
        title = (page.get("title") or slug).strip()
        for item in page["firmwares"]:
            url = item["download"]
            if url in firmwares:
                if title not in firmwares[url]["camera_name"]:
                    firmwares[url]["camera_name"].append(title)
                continue
            file_name = unquote(item["fileName"])
            # Intelbras renames Dahua's files (firmware-mhdx-3104-18-10-2021.zip), so prefix them with the product
            local_name = f"Intelbras_{sanitize_name(title)}_{file_name}"
            release_date = (datetime.fromtimestamp(item["fileDate"], tz=timezone.utc).date().isoformat()
                            if item.get("fileDate") else None)
            firmwares[url] = make_firmware([title], url, file_name=local_name, notes=item.get("title"),
                                           release_date=release_date)
            pages_by_url[url] = download_page.format(slug=slug)

    return list(firmwares.values())


def download_file(url, part_name):
    """backend.intelbras.com files are behind a Cloudflare challenge that plain requests can't pass, so download
    them in the browser; if that fails, use the Wayback Machine's copy."""
    start_url = pages_by_url.get(url, "https://www.intelbras.com/pt-br/ajuda-download")
    try:
        with http.protected_session() as session:
            session.download(url, part_name, start_url)
        return
    except Exception as err:
        print(f"\tIntelbras browser download failed, trying the Wayback Machine: {err}")

    # "2" asks for the capture nearest to the year 2xxx, i.e. the most recent one
    with http.get(f"https://web.archive.org/web/2id_/{url}", stream=True) as r:
        r.raise_for_status()
        if "text/html" in r.headers.get("Content-Type", ""):
            from requests import HTTPError
            raise HTTPError(f"no usable Wayback copy of {url}")
        with open(part_name, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
