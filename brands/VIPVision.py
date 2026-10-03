import re
import time
from concurrent.futures import ThreadPoolExecutor

from bs4 import BeautifulSoup

from util import http
from util.oem_helpers import FIRMWARE_EXTENSIONS, make_firmware, resolve_download, sanitize_name

name = "VIP Vision"
vendor = "VIP Vision"

firmware_site = "https://www.vip-vision.com/support/downloads"

# rhinoco.com.au drops connections when asked for many files at once, so resolve names gently and retry
RESOLVE_WORKERS = 4
RESOLVE_ATTEMPTS = 3


def resolve_with_retry(href):
    """(final_url, file_name), or (None, None) if the name couldn't be found after a few tries."""
    for attempt in range(1, RESOLVE_ATTEMPTS + 1):
        final_url, file_name = resolve_download(href)
        if file_name:
            return final_url, file_name
        time.sleep(3 * attempt)
    return None, None


def guessed_file_name(href, link_text):
    """A stand-in name for a file whose real name couldn't be looked up, so the listing isn't lost."""
    text = re.sub(r"\s*-?\s*\d{4}-\d{2}-\d{2}\s*$", "", link_text)
    return sanitize_name(f"VIPVision_{href.rstrip('/').split('/')[-1]}_{text}") + ".bin"


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
    hrefs = list(links)
    with ThreadPoolExecutor(max_workers=RESOLVE_WORKERS) as pool:
        resolved = dict(zip(hrefs, pool.map(resolve_with_retry, hrefs)))

    firmwares = []
    guessed = 0
    for href, entry in links.items():
        final_url, file_name = resolved[href]
        notes = None
        if file_name is None:
            # Keep the listing rather than silently dropping it (a flaky connection once cost 15 of 54 entries)
            local_name = guessed_file_name(href, entry["text"])
            notes = "File name guessed from the link text (the server's name couldn't be looked up)"
            guessed += 1
        elif not file_name.lower().endswith(FIRMWARE_EXTENSIONS):
            continue
        else:
            # Their names are just the link text, so prefix them and keep the file id to stay unique
            local_name = sanitize_name(f"VIPVision_{href.rstrip('/').split('/')[-1]}_{file_name}")

        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', entry["text"])
        firmwares.append(make_firmware(entry["models"], href, local_name, notes=notes,
                                       release_date=date_match[1] if date_match else None))

    if guessed:
        print(f"\tVIP Vision: {guessed} file names couldn't be looked up; using names guessed from the link text")
    return firmwares
