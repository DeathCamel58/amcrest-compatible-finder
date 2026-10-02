import re

from bs4 import BeautifulSoup

from util import http
from util import mega
from util.oem_helpers import parse_dahua_version

name = "EmpireTech"
vendor = "EmpireTech"

firmware_site = "https://empiretech01.com/pages/download-firmwares"

FIRMWARE_EXTENSIONS = (".bin", ".zip", ".img", ".rar", ".7z", ".dav", ".tar", ".gz")


def split_models(text):
    # Cells list several models like "IPC-T22IR-ZS / IPC-T2231T-ZS" or "NVR5208-8P-4KS2E, NVR5216-16P-4KS2E",
    # or "IPC-T54IR-ZE | IPC-T5442T-ZE", but a bare "/" is part of a model name (NVR4108HS-8P-4KS2/L)
    return [" ".join(model.split()) for model in re.split(r",|\||\s+/\s+", text) if model.strip()]


def get_firmware_version(file_name):
    version, _ = parse_dahua_version(file_name)
    return version or file_name.rsplit(".", 1)[0]


def get_unique_file_name(url, file_name):
    # Files named like "firmware.bin" are different firmwares in different folders, but firmware/ and cameras.json
    # are keyed by file name, so make those unique with the MEGA node handle
    if re.search(r"V\d+\.\d+\.", file_name):
        return file_name
    node_handle = url.rstrip("/").split("/")[-1].split("#")[0]
    return f"EmpireTech_{node_handle}_{file_name}"


def get_firmware_rows():
    page = http.get(firmware_site)
    page.raise_for_status()
    soup = BeautifulSoup(page.content, "html.parser")

    rows = []
    for container in soup.select("div.firmwares_content_container"):
        cols = container.find_all("div", recursive=False)
        if len(cols) < 5:
            continue

        changelog = cols[3].find("a")
        rows.append({
            "models": split_models(cols[0].get_text(" ", strip=True)),
            "firmware_date": cols[2].get_text(strip=True),
            "changelog": changelog["href"] if changelog else None,
            "links": [a["href"] for a in cols[4].find_all("a", href=True)],
        })

    return rows


def normalize_date(text):
    match = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text or "")
    if not match:
        return None

    return f"{match[1]}-{int(match[2]):02d}-{int(match[3]):02d}"


def list_link_files(link):
    """Return (firmware_latest url, file name, size) for every firmware file behind a MEGA link."""
    kind, handle, key, node = mega.parse_url(link)

    if kind == "file":
        info = mega.get_file_info(handle, key)
        return [(f"https://mega.nz/file/{handle}#{key}", info["name"], info["size"])]

    files = mega.list_folder(handle, key)
    if node is not None:
        files = [f for f in files if f["handle"] == node]

    return [(f"https://mega.nz/folder/{handle}#{key}/file/{f['handle']}", f["name"], f["size"]) for f in files]


def get_firmwares():
    rows = get_firmware_rows()

    # Several models often share one MEGA folder, so list each link once and merge the models
    links = {}
    skipped = {"no link": 0, "not MEGA": 0}
    for row in rows:
        mega_links = [link for link in row["links"] if "mega.nz" in link]
        if not row["links"]:
            skipped["no link"] += 1
        elif not mega_links:
            # The Google Drive folders need a signed in account
            skipped["not MEGA"] += 1

        for link in mega_links:
            entry = links.setdefault(link, {"models": [], "dates": [], "changelog": None})
            entry["models"] += [model for model in row["models"] if model not in entry["models"]]
            entry["dates"].append(normalize_date(row["firmware_date"]))
            entry["changelog"] = entry["changelog"] or row["changelog"]

    print(f"\tEmpireTech: {len(rows)} rows, {len(links)} unique MEGA links, skipped {skipped}")

    firmwares = []
    for link, entry in links.items():
        try:
            files = list_link_files(link)
        except Exception as err:
            print(f"\tFailed to list EmpireTech MEGA link {link}: {err}")
            continue

        dates = [date for date in entry["dates"] if date]
        for url, file_name, size in files:
            if not file_name or not file_name.lower().endswith(FIRMWARE_EXTENSIONS):
                continue

            firmwares.append({
                "camera_name": entry["models"],
                "firmware_version": get_firmware_version(file_name),
                "firmware_size": size,
                "firmware_notes": None,
                "firmware_changelog": entry["changelog"],
                "firmware_previous": None,
                "firmware_latest": url,
                "firmware_latest_file_name": get_unique_file_name(url, file_name),
                "release_date": max(dates) if dates else None,
            })

    return firmwares


def download_file(url, part_name):
    mega.download(url, part_name)
