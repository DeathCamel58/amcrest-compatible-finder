import hashlib
import math
import re
from concurrent.futures import ThreadPoolExecutor

from requests import HTTPError

from util import http

name = "Dahua Download Center"
vendor = "Dahua"

api_url = "https://www.dahuasecurity.com/api/{region}/downloadCenter/firmware/list"

# Each regional site lists a different subset of firmwares. Unknown region codes fall back to the "en" list,
# so only codes that return their own list are included here
regions = [
    "en", "uk", "au", "fr", "de", "es", "it", "mx", "br", "la", "in", "mena",
    "jp", "kr", "pt", "tr", "nl", "cz", "sa", "id", "th", "ar",
]

# The HTML site 403s non-browser user agents, so look like one for the API too
headers = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
}

# The API returns a fixed 10 firmwares per page
page_size = 10

# Firmware URL -> md5 from the API, so downloads can be verified
md5_by_url = {}
# Firmware URL -> MD5 of a download that didn't match the published one
mismatched_md5_by_url = {}


def get_page(region, page):
    response = http.get(api_url.format(region=region), params={"page": page, "child_menu_id": ""}, headers=headers)
    response.raise_for_status()
    data = response.json()
    if data.get("code") != "200":
        raise ValueError(f"Dahua API returned {data.get('code')}: {data.get('message')}")
    return data["data"]


def get_region_firmwares(region, pool):
    first_page = get_page(region, 1)
    pages = math.ceil(int(first_page["total"]) / page_size)

    items = list(first_page["list"])
    for page_data in pool.map(lambda page: get_page(region, page), range(2, pages + 1)):
        items.extend(page_data["list"])

    return items


def get_firmware_version(firmware_name):
    # e.g. DH_IPC-HX3XXX-Goethe_MultiLang_PN_Stream3_V3.140.0000000.38.R.260911 -> V3.140.0000000.38.R.260911
    match = re.search(r"V\d+\.\d+\.[0-9A-Za-z]+\.\d+\.[A-Z]\.\d{6,8}", firmware_name)
    return match.group(0) if match else None


def get_firmwares():
    # Merge every region's list, de-duplicated on firmware_id, combining the products each region maps to it
    items_by_id = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for region in regions:
            try:
                region_items = get_region_firmwares(region, pool)
            except Exception as err:
                print(f"\tFailed to get Dahua {region} firmwares: {err!r}")
                continue

            for item in region_items:
                existing = items_by_id.setdefault(item["firmware_id"], {**item, "product": []})
                for product in item.get("product") or []:
                    if product["product_name"] not in [p["product_name"] for p in existing["product"]]:
                        existing["product"].append(product)

    firmwares = []
    for item in items_by_id.values():
        if not item.get("firmware_url"):
            continue

        models = [product["product_name"] for product in item["product"]]

        firmware = {
            "camera_name": models or item["firmware_name"],
            "firmware_version": get_firmware_version(item["firmware_name"]),
            "firmware_size": None,
            "firmware_notes": item.get("firmware_updates") or None,
            "firmware_changelog": item.get("firmware_note") or None,
            "firmware_previous": None,
            "firmware_latest": item["firmware_url"],
            "release_date": item.get("post_date") or None,
            "md5": item.get("md5") or None,
            "sha256": item.get("hash") or None,
        }

        if firmware["md5"]:
            md5_by_url[firmware["firmware_latest"]] = firmware["md5"].lower()

        firmwares.append(firmware)

    return firmwares


def download_file(url, part_name):
    md5 = hashlib.md5()
    with http.get(url, stream=True) as r:
        r.raise_for_status()
        with open(part_name, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
                md5.update(chunk)

    expected = md5_by_url.get(url)
    actual = md5.hexdigest()
    if expected and actual != expected:
        # Dahua sometimes publishes a wrong checksum. If two downloads give the same bytes, the file is stable and the
        # published value is what's wrong, so keep it (enrich flags the entry with vendor_md5_mismatch)
        if mismatched_md5_by_url.get(url) == actual:
            print(f"\tKeeping {url}: two downloads match each other ({actual}) but not Dahua's published MD5 {expected}")
            return
        mismatched_md5_by_url[url] = actual
        # Raised as a normal error so the download gets retried
        raise ValueError(f"MD5 mismatch for {url}: got {actual}, expected {expected}")
