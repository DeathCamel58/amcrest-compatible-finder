"""Restore what earlier versions of cameras.json recorded and the current one has lost.

    python -m util.restore_history [--dry-run]

Earlier versions come from git history and backups/cameras*.json. Restored:
  - listing URLs that were overwritten when a vendor moved a file or listed it under several products (into each
    listing's url_history)
  - listings that are missing entirely
  - first_seen / last_seen dates missing from listings (from the dates of the versions that had them)
  - md5, sha256, changelog and vendors that an entry has lost
  - the records of entries that left cameras.json (moved out as non-firmware, or placeholder link text like
    "ClickHere"), into removed-entries.json next to the non-firmware files

Run it while nothing else is writing cameras.json (no download stage running).
"""
import glob
import json
import os
import re
import subprocess
import sys
from collections import Counter
from urllib.parse import unquote, urlparse

BACKUP_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")
CHECKSUM_PATTERNS = {"md5": re.compile(r"[0-9a-fA-F]{32}"), "sha256": re.compile(r"[0-9a-fA-F]{64}")}


def history_versions(json_file="cameras.json", backups="backups/cameras*.json"):
    """[(date, data)] for every committed version of json_file and every backup, oldest first."""
    versions = []
    log = subprocess.run(["git", "log", "--format=%H %cs", "--", json_file], capture_output=True, text=True).stdout
    for line in log.splitlines():
        commit, date = line.split()
        shown = subprocess.run(["git", "show", f"{commit}:{json_file}"], capture_output=True, text=True)
        if shown.returncode == 0:
            try:
                versions.append((date, json.loads(shown.stdout)))
            except ValueError:
                pass
    for path in glob.glob(backups):
        match = BACKUP_DATE.search(os.path.basename(path))
        if match:
            with open(path) as f:
                versions.append((match.group(1), json.load(f)))
    return sorted(versions, key=lambda version: version[0])


def add_url_history(listing, url, first_seen, last_seen):
    """Record that the listing offered url between those dates, without changing its current url."""
    history = [dict(item) for item in listing.get("url_history") or []]
    if listing.get("url") and not any(item["url"] == listing["url"] for item in history):
        history.append({key: value for key, value in (("url", listing["url"]), ("first_seen", listing.get("first_seen")),
                                                       ("last_seen", listing.get("last_seen"))) if value})
    item = next((item for item in history if item["url"] == url), None)
    if item is None:
        item = {"url": url}
        history.append(item)
    for key, value, pick in (("first_seen", first_seen, min), ("last_seen", last_seen, max)):
        if value:
            item[key] = pick(item.get(key) or value, value)
    history.sort(key=lambda item: item.get("first_seen") or "")
    if len(history) > 1:
        listing["url_history"] = history


def listing_urls(listing):
    return {listing.get("url")} | {item["url"] for item in listing.get("url_history") or []}


def find_listing(listings, old_listing):
    """The current listing an old one is the same page as: same vendor and source, else same vendor and host."""
    for listing in listings:
        if listing.get("vendor") == old_listing.get("vendor") and listing.get("source") == old_listing.get("source"):
            return listing
    host = urlparse(old_listing.get("url") or "").hostname
    same_host = [listing for listing in listings if listing.get("vendor") == old_listing.get("vendor")
                 and urlparse(listing.get("url") or "").hostname == host]
    return same_host[0] if len(same_host) == 1 else None


def legacy_listing(listings, old_listing):
    vendor = old_listing.get("vendor")
    candidates = [l for l in listings if l.get("vendor") == vendor] if vendor else \
        [l for l in listings if l.get("kind", "vendor") == "vendor"]
    if not candidates:
        return None
    host = urlparse(old_listing.get("url") or "").hostname
    return next((l for l in candidates if urlparse(l.get("url") or "").hostname == host), candidates[0])


def old_listings_of(old_entry):
    """An old entry's listings; entries from before listings existed only had a url and vendors."""
    if old_entry.get("listings"):
        return old_entry["listings"]
    if old_entry.get("url") and "://" in old_entry["url"]:
        vendors = old_entry.get("vendors") or [None]
        return [{"vendor": vendors[0], "source": None, "url": old_entry["url"]}]
    return []


def is_placeholder(name, entry, files):
    """An entry for link text a scraper took for a file ("ClickHere", "ComingSoon"), not a real firmware."""
    url = entry.get("url") or ""
    return name not in files and "://" not in url and not entry.get("listing_only_reason")


def restore(cameras, versions, removed, files):
    """Apply the restorations to cameras and removed (both changed in place). Returns (counts, unplaced URLs)."""
    counts = Counter()
    unplaced = []

    for date, old in versions:
        for old_name, old_entry in old.items():
            name = next((n for n in (old_name, unquote(old_name), unquote(unquote(old_name))) if n in cameras), None)
            if name is None:
                if old_name not in removed and unquote(old_name) not in removed:
                    removed[old_name] = {**old_entry, "removed_on": f"before {date}",
                                         "removed_reason": "restored from history"}
                    counts["removed entry record restored"] += 1
                continue

            entry = cameras[name]
            listings = entry.setdefault("listings", [])
            for old_listing in old_listings_of(old_entry):
                url = old_listing.get("url")
                if not url or "://" not in url:
                    continue
                listing = find_listing(listings, old_listing)
                if listing is None and old_listing.get("source") is None:
                    # A url from before listings existed: it belongs to the same vendor's listing (preferring one on
                    # the same host), or to a vendor listing when the old version recorded no vendor
                    if any(url in listing_urls(l) for l in listings):
                        continue
                    listing = legacy_listing(listings, old_listing)
                    if listing is None:
                        unplaced.append((name, url))
                        continue
                if listing is None:
                    restored = {**old_listing}
                    restored.setdefault("kind", "vendor")
                    restored.setdefault("camera_name", [])
                    restored.setdefault("notes", [])
                    restored.setdefault("first_seen", old_listing.get("first_seen") or date)
                    restored.setdefault("last_seen", old_listing.get("last_seen") or date)
                    if restored["kind"] != "vendor":
                        restored.pop("latest", None)
                        restored.pop("last_seen_latest", None)
                    listings.append(restored)
                    counts["listing restored"] += 1
                    continue
                if url not in listing_urls(listing):
                    add_url_history(listing, url, old_listing.get("first_seen") or date,
                                    old_listing.get("last_seen") or date)
                    counts["listing URL restored to url_history"] += 1
                if not listing.get("first_seen") or (old_listing.get("first_seen") or date) < listing["first_seen"]:
                    if not listing.get("first_seen"):
                        counts["first_seen filled"] += 1
                    listing["first_seen"] = old_listing.get("first_seen") or date
                if not listing.get("last_seen"):
                    listing["last_seen"] = old_listing.get("last_seen") or date
                    counts["last_seen filled"] += 1

            for field in ("md5", "sha256", "changelog"):
                if field in CHECKSUM_PATTERNS and not CHECKSUM_PATTERNS[field].fullmatch(str(old_entry.get(field) or "")):
                    continue  # older versions kept placeholders like "0"
                if old_entry.get(field) and not entry.get(field):
                    entry[field] = old_entry[field]
                    counts[f"{field} restored"] += 1
            for vendor in old_entry.get("vendors") or []:
                if vendor and vendor not in (entry.get("vendors") or []):
                    entry.setdefault("vendors", []).append(vendor)
                    counts["vendor restored"] += 1

    for name in [name for name, entry in cameras.items() if is_placeholder(name, entry, files)]:
        removed.setdefault(name, {**cameras.pop(name), "removed_reason": "placeholder link text, not a file"})
        counts["placeholder entry moved to removed-entries.json"] += 1

    # A listing whose url_history now has the current URL's dates should keep last_seen as its latest
    for entry in cameras.values():
        for listing in entry.get("listings") or []:
            if listing.get("last_seen") and listing.get("first_seen") and listing["first_seen"] > listing["last_seen"]:
                listing["first_seen"] = listing["last_seen"]

    return counts, unplaced


def main(argv):
    sys.path.insert(0, os.getcwd())
    import main as pipeline
    from util.json_tools import get_cameras_json, save_cameras_json

    dry_run = "--dry-run" in argv
    cameras = get_cameras_json()
    removed_path = os.path.join(pipeline.NON_FIRMWARE_DIR, pipeline.REMOVED_ENTRIES_FILE)
    try:
        with open(removed_path) as f:
            removed = json.load(f)
    except (FileNotFoundError, ValueError):
        removed = {}
    files = set(pipeline.list_firmware_files())

    counts, unplaced = restore(cameras, history_versions(), removed, files)
    for what, count in sorted(counts.items()):
        print(f"{what}: {count}")
    print(f"legacy URLs that match no current listing: {len(unplaced)}")
    for name, url in unplaced[:10]:
        print(f"    {name}: {url}")

    if dry_run:
        print("Dry run: nothing written")
        return 0
    save_cameras_json(cameras)
    os.makedirs(os.path.dirname(removed_path), exist_ok=True)
    temp_path = f"{removed_path}.{os.getpid()}.tmp"
    with open(temp_path, "w") as f:
        json.dump(removed, f, indent=1, sort_keys=True)
    os.replace(temp_path, removed_path)
    print(f"Saved cameras.json and {removed_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
