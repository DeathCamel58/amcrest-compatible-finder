import json
import re
from urllib.parse import unquote

from bs4 import BeautifulSoup

from util import http
from util.oem_helpers import is_dahua_firmware_name, make_firmware

name = "Winic"
vendor = "Winic"

firmware_page = "https://winictech.com/firmware-download/"
ajax_url = "https://winictech.com/mall26/wp-admin/admin-ajax.php"

# Winic sells both Dahua- and Hikvision-made lines. The Dahua ones are named like XVR531-16_210415.bin,
# NVR5XXX-4KS2.210510.bin and IPC-PD656A.bin; the Hikvision ones like DZ_K72A_ML_NEU_V4.21.100_210208.zip,
# PJ14PC..._IPCE_E10_EN_NEU_V5.8.10_build250929.zip and NC32X_G1_EN_NEU_5.6.2_210422.zip
DAHUA_PREFIXES = re.compile(r"^(XVR|NVR|HCVR|IPC-)")
HIKVISION_MARKERS = re.compile(r"(^|_)(DZ|IPCE)_|_NEU|_K\d{1,2}[A-Z]?\d?_", re.IGNORECASE)
FIRMWARE_EXTENSIONS = (".bin", ".zip", ".img", ".dav", ".rar")


def is_winic_dahua_firmware(file_name):
    if not file_name.lower().endswith(FIRMWARE_EXTENSIONS) or HIKVISION_MARKERS.search(file_name):
        return False
    return is_dahua_firmware_name(file_name) or bool(DAHUA_PREFIXES.match(file_name))


def split_models(text):
    # "IPC-PD65/IPC-PD6A", "AR324-8A & AR326-4A", "NC325-XB 4mm , NC325-XD 2.8mm"; but "XVR32ch&4K" is one model
    return [part.strip() for part in re.split(r"\s*[/,]\s*|\s+&\s+", text) if part.strip()]


def parse_name(file_name):
    """(version, release date) from names like XVR32ch4K_V4.001.211013.bin or XVR531-16_210415.bin."""
    version = re.search(r"[_.-](V\d+\.\d+)[._]", file_name)
    date = re.search(r"[._](\d{2})(\d{2})(\d{2})\.[A-Za-z]+$", file_name)
    release_date = None
    if date and 1 <= int(date.group(2)) <= 12 and 1 <= int(date.group(3)) <= 31:
        release_date = f"20{date.group(1)}-{date.group(2)}-{date.group(3)}"
    return (version.group(1) if version else None), release_date


def get_rows():
    """Every row of the firmware table, fetched in one AJAX request (the page itself only shows 10 at a time)."""
    # The page sets the Cloudflare clearance that the AJAX request then reuses
    page = http.get_protected_html(firmware_page)
    if page is None:
        return []

    nonce = re.search(r"const nonce = '([0-9a-f]+)'", page)
    params = re.search(r'<script type="application/json" class="flt-params">(.*?)</script>', page, re.S)
    if not nonce or not params:
        print("\tWinic: firmware table parameters not found on the page")
        return []
    params = json.loads(params.group(1))

    form = {"action": "firmware_table_update", "nonce": nonce.group(1)}
    for key, value in params.items():
        if key == "fields":
            for index, field in enumerate(value):
                for field_key, field_value in field.items():
                    form[f"fields[{index}][{field_key}]"] = field_value
        else:
            form[key] = value
    form["paged"] = 1
    form["items_per_page"] = 1000

    response = http.post(ajax_url, data=form, headers={"X-Requested-With": "XMLHttpRequest", "Referer": firmware_page})
    response.raise_for_status()
    result = response.json()
    if not result.get("success"):
        print(f"\tWinic: firmware table request failed: {str(result)[:200]}")
        return []

    rows = []
    for row in BeautifulSoup(result["data"], "html.parser").find_all("tr"):
        cells = {cell.get("data-label"): cell for cell in row.find_all("td")}
        if "Download" not in cells:
            continue
        title = cells["Title"].get_text(" ", strip=True) if "Title" in cells else ""
        related = cells["Related Product"].get_text(" ", strip=True) if "Related Product" in cells else ""
        for link in cells["Download"].find_all("a", href=True):
            rows.append((title, related, link["href"]))
    return rows


def get_firmwares():
    firmwares = {}
    for title, related, url in get_rows():
        file_name = unquote(url.split("/")[-1])
        if not is_winic_dahua_firmware(file_name):
            continue

        models = list(dict.fromkeys(split_models(title) + split_models(related)))
        # Winic's names are model-like rather than Dahua's full names, so prefix them to avoid colliding with other
        # vendors' files of the same name (GSS uses the same ones, e.g. IPC-144M.bin)
        local_name = file_name if is_dahua_firmware_name(file_name) else f"Winic_{file_name}"
        if local_name in firmwares:
            firmwares[local_name]["camera_name"] = list(dict.fromkeys(firmwares[local_name]["camera_name"] + models))
            continue
        version, release_date = parse_name(file_name)
        firmwares[local_name] = make_firmware(models, url, file_name=local_name, version=version,
                                              release_date=release_date)

    return list(firmwares.values())


def download_file(url, part_name):
    # http.get replays (and refreshes, if needed) the Cloudflare clearance for winictech.com
    with http.get(url, stream=True) as r:
        r.raise_for_status()
        if "text/html" in r.headers.get("Content-Type", ""):
            raise http_error(url, "got an HTML page (Cloudflare challenge?) instead of the file")
        with open(part_name, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)


def http_error(url, message):
    from requests import HTTPError
    return HTTPError(f"{message}: {url}")
