import html
import json
import re
from urllib.parse import unquote, urljoin

from util import http
from util.oem_helpers import is_dahua_firmware_name, make_firmware, resolve_downloads, sanitize_name

name = "RVI"
vendor = "RVI"

firmware_site = "https://rvigroup.ru/download/proshivki/"

# RVI also resells other OEMs, which we don't want
OTHER_OEMS = re.compile(r'tiandy|sunell|raysharp|hikvision|_hik\b|/hik', re.IGNORECASE)


def get_link_entries():
    page = http.get(firmware_site)
    page.encoding = "utf-8"

    # Every download on the page lives in an inline `var json_links = {...};`
    match = re.search(r'var json_links\s*=\s*(\{.*?\});', page.text, re.DOTALL)
    if not match:
        print("\tCouldn't find json_links on the RVI firmware page")
        return []

    entries = []

    def walk(node):
        if node.get("link"):
            entries.append(node)
        children = node.get("childs") or []
        for child in (children.values() if isinstance(children, dict) else children):
            walk(child)

    for group in json.loads(match[1]).values():
        walk(group)

    return entries


def get_model(entry_name, resolved_path):
    # Older entries are named after the RVI model, e.g. "RVI-R08LA Прошивка"
    match = re.match(r'(RVi-[^\s(]+)', entry_name, re.IGNORECASE)
    if match:
        return match[1].rstrip(",;.")

    # Newer entries are just a version, but the path has the Dahua board, e.g. .../Dahua/XVR7x16-I2/20210412_R/...
    match = re.search(r'/Dahua/([^/]+)/', resolved_path)
    return match[1] if match else None


def clean_notes(description):
    # Descriptions are HTML-ish with "|" as line breaks
    lines = [line.strip() for line in html.unescape(description or "").replace("\xa0", " ").split("|")]
    notes = "\n".join(line for line in lines if line)
    return notes or None


def get_firmwares():
    entries = get_link_entries()

    urls = {entry["link"]: urljoin(firmware_site, entry["link"]) for entry in entries}
    # Most links are /download/api/?download-id=N redirects, so follow them to find the real file
    resolved = resolve_downloads(urls.values())

    firmwares = []
    for entry in entries:
        url = urls[entry["link"]]
        final_url, file_name = resolved[url]
        if final_url is None:
            continue

        resolved_path = unquote(final_url)
        if OTHER_OEMS.search(resolved_path) or OTHER_OEMS.search(entry["name"]):
            continue
        if "/Dahua/" not in resolved_path and not is_dahua_firmware_name(file_name):
            continue

        model = get_model(entry["name"], resolved_path)
        # RVI file names like update_V4.001.00GP000.0.R.210412.bin are shared across models, so make them unique
        local_file_name = sanitize_name(f"RVI_{model or entry['id']}_{file_name}")

        firmwares.append(make_firmware(model, url, local_file_name, notes=clean_notes(entry.get("description"))))

    return firmwares
