import re

from bs4 import BeautifulSoup

from util import http
from util.oem_helpers import FIRMWARE_EXTENSIONS, make_firmware, resolve_downloads, sanitize_name

name = "VIP Vision"
vendor = "VIP Vision"

firmware_site = "https://www.vip-vision.com/support/downloads"


def parse_link_models(link_text):
    # e.g. "NVR32ULTNPV2, NVR64ULTNPV2, NVR64ULT8S, NVR128ULTNPV2 Firmware - 2024-10-18"
    models_text = re.split(r'\s+firmware\b', link_text, flags=re.IGNORECASE)[0]
    return [model.strip() for model in re.split(r'\s*(?:,|&|/|\band\b)\s*', models_text) if model.strip()]


def get_firmware_links():
    page = http.get(firmware_site)
    soup = BeautifulSoup(page.content, "html.parser")

    # Many products share the same firmware file, so group them by link
    links = {}
    for block in soup.select("div.row.mb-5"):
        model_tag = block.select_one("small.text-secondary")
        block_model = model_tag.get_text(strip=True).replace("Model:", "").strip() if model_tag else None

        for link in block.select("table.table-small td > a[href]"):
            text = link.get_text(" ", strip=True)
            href = link["href"]
            # Skip PDFs and the how-to articles on help.c5k.info
            if not re.search(r'firmware', text, re.IGNORECASE) or href.lower().endswith(".pdf") or "c5k.info" in href:
                continue

            entry = links.setdefault(href, {"text": text, "models": []})
            for model in parse_link_models(text) + ([block_model] if block_model else []):
                if model not in entry["models"]:
                    entry["models"].append(model)

    return links


def get_firmwares():
    links = get_firmware_links()

    # Download links are /file/display/<id>, so the real file name only comes from Content-Disposition
    resolved = resolve_downloads(links.keys())

    firmwares = []
    for href, entry in links.items():
        final_url, file_name = resolved[href]
        if file_name is None or not file_name.lower().endswith(FIRMWARE_EXTENSIONS):
            continue

        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', entry["text"])
        firmwares.append(make_firmware(
            entry["models"],
            href,
            # Their names are just the link text, so prefix them and keep the file id to stay unique
            sanitize_name(f"VIPVision_{href.rstrip('/').split('/')[-1]}_{file_name}"),
            release_date=date_match[1] if date_match else None,
        ))

    return firmwares
