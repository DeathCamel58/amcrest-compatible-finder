"""Earlier builds still on Dahua's download server.

When Dahua's download center replaces a firmware, the old build usually stays in the same
materialfile.dahuasecurity.com/uploads/cpq/<id>/firmware/ folder, unlisted. The server has no listing (missing files
return 403), so this builds candidate names and checks each with a one-byte Range request:

  - for every cpq folder we know, the firmware families already seen in it (the file name before its version);
  - the versions those families' release notes list in their version history, the versions other vendors' copies of
    the same family have, and the wanted.json entries for the family;
  - release notes at SWR/<id>/<name>_Release_Notes.pdf, whose firmware is at SWR/<id-1>/M/<name>.zip.

Every check is cached in tmp/dahua_old_builds_index.json, so each run only checks new candidates, and a run checks at
most MAX_PROBES_PER_RUN of them (a full cold run is several hours at one request a second).
"""
import json
import os
import re
import time
from datetime import date

from util import http
from util.oem_helpers import make_firmware
from util.release_notes import pdf_text, versions_in_text

name = "Dahua download center (earlier builds)"
vendor = "Dahua"
kind = "vendor"

CAMERAS_JSON = "cameras.json"
WANTED_JSON = "wanted.json"
CACHE_FILE = "tmp/dahua_old_builds_index.json"
# One request a second keeps this polite; a run checks at most this many candidates (about 45 minutes)
PROBE_DELAY = 1.0
MAX_PROBES_PER_RUN = 2500
# An old build that wasn't there isn't likely to appear, but look again eventually
MISS_RECHECK_DAYS = 180
# Release notes get new versions added; read them again after this long
NOTES_RECHECK_DAYS = 60
SAVE_EVERY = 50

BASE = "https://materialfile.dahuasecurity.com/uploads/cpq/"
FIRMWARE_FOLDER = re.compile(r"^(https://materialfile\.dahuasecurity\.com/uploads/cpq/\d+/firmware/)([^/?#]+)$")
SWR_NOTES = re.compile(r"^https://materialfile\.dahuasecurity\.com/uploads/cpq/SWR/(\d+)/([^/?#]+?)_Release_Notes?\.pdf$",
                       re.IGNORECASE)
FIRMWARE_NAME = re.compile(r"^(?P<family>.+?)_(?P<version>V\d+\.\d+\.[0-9A-Za-z]{4,8}\.\d+\.[A-Z])"
                           r"\.(?P<date>\d{6}|\d{8})(?P<ext>\.(?:zip|bin|rar))$", re.IGNORECASE)
RELEASE_NOTES_URL = re.compile(r"https?://\S+?\.pdf", re.IGNORECASE)


def parse_name(file_name):
    """{family, version, date, ext} for a Dahua-style firmware file name, or None."""
    match = FIRMWARE_NAME.match(file_name)
    return match.groupdict() if match else None


def series(version):
    """"V3.140" for "V3.140.0000000.71.R": builds of one branch share it."""
    return ".".join(version.split(".")[:2])


def release_date(build_date):
    if len(build_date) == 6:
        return f"20{build_date[0:2]}-{build_date[2:4]}-{build_date[4:6]}"
    return f"{build_date[0:4]}-{build_date[4:6]}-{build_date[6:8]}"


def load_cache(path=CACHE_FILE):
    try:
        with open(path) as f:
            cache = json.load(f)
    except (FileNotFoundError, ValueError):
        cache = {}
    cache.setdefault("probes", {})
    cache.setdefault("release_notes", {})
    return cache


def save_cache(cache, path=CACHE_FILE):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    temp_path = f"{path}.{os.getpid()}.tmp"
    with open(temp_path, "w") as f:
        json.dump(cache, f, indent=1, sort_keys=True)
    os.replace(temp_path, path)


def is_stale(checked, days, today):
    return (today - date.fromisoformat(checked)).days > days


def entry_urls(entry):
    for listing in entry.get("listings") or []:
        yield listing.get("url")
        for item in listing.get("url_history") or []:
            yield item.get("url")


def release_notes_urls(entry):
    texts = [entry.get("changelog")] + [listing.get("changelog") for listing in entry.get("listings") or []]
    return {url for text in texts if text for url in RELEASE_NOTES_URL.findall(text)}


def known_firmwares(cameras):
    """What cameras.json says about Dahua's download server:
    (folders {folder url: {family: {ext, versions, models, notes}}}, every known URL,
     {family: {(version, date)}} across all vendors, SWR firmware candidates [(url, name, models)])."""
    folders, known_urls, family_versions, swr = {}, set(), {}, []
    for file_name, entry in cameras.items():
        parsed = parse_name(file_name)
        if parsed:
            family_versions.setdefault(parsed["family"], set()).add((parsed["version"], parsed["date"]))
        models = list(entry.get("camera_name") or []) + list(entry.get("series") or [])
        for url in entry_urls(entry):
            if not url:
                continue
            known_urls.add(url)
            match = FIRMWARE_FOLDER.match(url)
            name_parsed = parse_name(match.group(2)) if match else None
            if not name_parsed:
                continue
            family = folders.setdefault(match.group(1), {}).setdefault(
                name_parsed["family"], {"ext": name_parsed["ext"], "versions": set(), "models": [], "notes": set()})
            family["versions"].add((name_parsed["version"], name_parsed["date"]))
            family["models"] += [m for m in models if m not in family["models"]]
            family["notes"] |= release_notes_urls(entry)
        for notes_url in release_notes_urls(entry):
            match = SWR_NOTES.match(notes_url)
            if match:
                swr.append((f"{BASE}SWR/{int(match.group(1)) - 1}/M/{match.group(2)}.zip", match.group(2), models))
    return folders, known_urls, family_versions, swr


def wanted_versions(wanted):
    """{family: {(version, date)}} from wanted.json's named entries that aren't found yet."""
    families = {}
    for entry in wanted.values():
        if entry.get("found") or not entry.get("name"):
            continue
        parsed = parse_name(entry["name"]) or parse_name(entry["name"] + ".zip")
        if parsed:
            families.setdefault(parsed["family"], set()).add((parsed["version"], parsed["date"]))
    return families


def variant_base(family):
    """A family without its last part: DH_VTHX421H_MultiLang_SIP_EMEA -> DH_VTHX421H_MultiLang_SIP. Families that
    share it are regional or stream variants (EMEA / Asia / CEE-Nordic, Stream2 / Stream3) Dahua often puts in one
    folder at the same version."""
    return family.rsplit("_", 1)[0] if "_" in family else family


def candidates(folders, known_urls, family_versions, wanted_families, note_versions, swr=()):
    """[(url, family, version, date, models, origin)] to check, most likely first: wanted names, then versions other
    vendors have, then release-notes versions (newest first). Only versions of a branch the folder already holds."""
    found = {}
    rank = {"wanted": 0, "other vendors": 1, "variant": 1, "release notes": 2, "swr": 1}
    # Only families the download center itself uses (seen in some cpq folder), not other vendors' renamed copies
    variants = {}
    for families in folders.values():
        for family in families:
            variants.setdefault(variant_base(family), set()).add(family)
    for folder, families in folders.items():
        for family, info in families.items():
            # Sibling variants of this family, at the versions this folder holds
            for sibling in sorted(variants.get(variant_base(family), set()) - {family} - set(families)):
                for version, build_date in info["versions"]:
                    url = f"{folder}{sibling}_{version}.{build_date}{info['ext']}"
                    if url not in known_urls and url not in found:
                        found[url] = (url, sibling, version, build_date, info["models"], "variant")
            branches = {series(version) for version, _ in info["versions"]}
            sources = [("wanted", wanted_families.get(family, set())),
                       ("other vendors", family_versions.get(family, set())),
                       ("release notes", {v for notes in info["notes"] for v in note_versions.get(notes, [])})]
            for origin, versions in sources:
                for version, build_date in versions:
                    if (version, build_date) in info["versions"] or series(version) not in branches:
                        continue
                    url = f"{folder}{family}_{version}.{build_date}{info['ext']}"
                    if url in known_urls or url in found:
                        continue
                    found[url] = (url, family, version, build_date, info["models"], origin)
    for url, file_name, models in swr:
        parsed = parse_name(f"{file_name}.zip")
        if url not in known_urls and url not in found and parsed:
            found[url] = (url, parsed["family"], parsed["version"], parsed["date"], models, "swr")
    newest_first = lambda build_date: -int(("20" + build_date) if len(build_date) == 6 else build_date)
    return sorted(found.values(), key=lambda c: (rank[c[5]], newest_first(c[3]), c[0]))


def probe(url):
    """("hit", size) if the file is there, ("miss", None) if the server says it isn't, ("error", None) otherwise."""
    try:
        with http.get(url, stream=True, headers={"Range": "bytes=0-0", "Accept-Encoding": "identity"},
                      timeout=(15, 30)) as response:
            if response.status_code in (200, 206):
                total = response.headers.get("Content-Range", "").rpartition("/")[2]
                size = int(total) if total.isdigit() else (int(response.headers.get("Content-Length") or 0) or None)
                return "hit", size
            if response.status_code in (403, 404, 410):
                return "miss", None
            return "error", None
    except Exception:
        return "error", None


def fetch_release_notes(urls, cache, today, delay=PROBE_DELAY):
    """{release notes url: [(version, date)]}, reading PDFs not cached yet (or stale)."""
    for url in sorted(urls):
        cached = cache["release_notes"].get(url)
        if cached and not is_stale(cached["fetched"], NOTES_RECHECK_DAYS, today):
            continue
        try:
            response = http.get(url, timeout=(15, 120))
            versions = versions_in_text(pdf_text(response.content)) if response.status_code == 200 else []
        except Exception as err:
            print(f"\tCouldn't read release notes {url}: {err!r}")
            continue
        cache["release_notes"][url] = {"fetched": today.isoformat(), "versions": [list(v) for v in versions]}
        time.sleep(delay)
    return {url: [tuple(v) for v in entry["versions"]] for url, entry in cache["release_notes"].items()}


def get_firmwares(max_probes=None, today=None):
    if max_probes is None:
        max_probes = int(os.environ.get("DAHUA_OLD_BUILDS_MAX_PROBES", MAX_PROBES_PER_RUN))
    today = today or date.today()
    with open(CAMERAS_JSON) as f:
        cameras = json.load(f)
    try:
        with open(WANTED_JSON) as f:
            wanted = json.load(f)
    except FileNotFoundError:
        wanted = {}

    cache = load_cache()
    folders, known_urls, family_versions, swr = known_firmwares(cameras)
    notes_urls = {url for families in folders.values() for info in families.values() for url in info["notes"]}
    note_versions = fetch_release_notes(notes_urls, cache, today)
    save_cache(cache)

    todo = candidates(folders, known_urls, family_versions, wanted_versions(wanted), note_versions, swr)
    unchecked = [c for c in todo if c[0] not in cache["probes"]
                 or (cache["probes"][c[0]]["status"] == "miss"
                     and is_stale(cache["probes"][c[0]]["checked"], MISS_RECHECK_DAYS, today))]
    checked = 0
    for url, *_ in unchecked[:max_probes]:
        status, size = probe(url)
        if status != "error":
            cache["probes"][url] = {"status": status, "size": size, "checked": today.isoformat()}
        checked += 1
        if checked % SAVE_EVERY == 0:
            save_cache(cache)
        time.sleep(PROBE_DELAY)
    save_cache(cache)

    firmwares = []
    for url, family, version, build_date, models, origin in todo:
        probe_result = cache["probes"].get(url)
        if not probe_result or probe_result["status"] != "hit":
            continue
        firmware = make_firmware(list(models), url, version=version, release_date=release_date(build_date),
                                 notes="An earlier build still on Dahua's download server after the download center "
                                       "replaced it")
        if probe_result.get("size"):
            firmware["firmware_size"] = probe_result["size"]
        firmwares.append(firmware)

    remaining = max(0, len(unchecked) - checked)
    print(f"\tDahua earlier builds: {len(folders)} folders, {len(todo)} candidates, checked {checked} this run "
          f"({remaining} left for later runs), {len(firmwares)} found")
    return firmwares
