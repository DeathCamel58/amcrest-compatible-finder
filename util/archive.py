import hashlib
import html
import json
import os
import re
import tempfile
from urllib.parse import quote

import internetarchive

# Each firmware gets its own Internet Archive item, so it has its own searchable metadata and a stable link.
# Item identifiers can never be renamed, so don't change this scheme once items exist
IDENTIFIER_PREFIX = "cctv-firmware-"
COLLECTION = "open_source_software"
# Every item gets this tag so the whole archive is one search away, until there's a dedicated collection
# (only archive.org staff can create those)
PROJECT_TAG = "amcrest-compatible-finder"
SEARCH_URL = f"https://archive.org/search?query=subject%3A%22{PROJECT_TAG}%22"
MAX_IDENTIFIER_LENGTH = 100

# Keep item subjects (tags) to a sane size; the full model list is in the description
MAX_SUBJECT_MODELS = 40


def get_identifier(file_name):
    # Identifiers allow [A-Za-z0-9._-]. A short hash of the real name keeps them unique after sanitising/truncating
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", file_name).strip("-._")
    suffix = "-" + hashlib.sha1(file_name.encode()).hexdigest()[:8]
    slug = slug[:MAX_IDENTIFIER_LENGTH - len(IDENTIFIER_PREFIX) - len(suffix)].rstrip("-._")
    return f"{IDENTIFIER_PREFIX}{slug}{suffix}"


def get_urls(identifier, file_name):
    return (
        f"https://archive.org/details/{identifier}",
        f"https://archive.org/download/{identifier}/{quote(file_name)}",
    )


def md5_file(path):
    md5 = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            md5.update(chunk)
    return md5.hexdigest()


# Entry fields copied into the provenance record that's uploaded next to each firmware
RECORD_FIELDS = ["platform", "vendors", "listings", "camera_name", "notes", "url", "firmware_version",
                 "release_date", "changelog", "md5", "sha256"]

PLATFORM_NAMES = {"dahua": "Dahua", "hikvision": "Hikvision"}


def get_record(file_name, size, md5, entry, hardware_ids):
    """Machine-readable provenance for the item: where the file came from and what was found inside it."""
    record = {
        "file_name": file_name,
        "size": size,
        "archived_md5": md5,
        "hardware_ids": hardware_ids,
        "archived_by": "amcrest-compatible-finder",
    }
    for field in RECORD_FIELDS:
        if entry.get(field):
            # The vendor checksums are kept apart from the checksum of what was actually archived
            record[f"vendor_{field}" if field in ("md5", "sha256") else field] = entry[field]
    return record


def get_record_hash(record):
    return hashlib.sha1(json.dumps(record, sort_keys=True).encode()).hexdigest()


def link(url):
    return f'<a href="{html.escape(url)}">{html.escape(url)}</a>'


def get_metadata(record):
    file_name = record["file_name"]
    vendors = record.get("vendors") or []
    models = record.get("camera_name") or []
    hardware_ids = record.get("hardware_ids") or []
    esc = html.escape

    lines = [f"Firmware file <b>{esc(file_name)}</b>, archived by amcrest-compatible-finder so it stays available "
             f"after vendors remove it. The {esc(file_name)}.provenance.json file in this item has the same data "
             f"in machine-readable form."]

    # Where it came from, per vendor page
    listings = record.get("listings") or []
    if listings:
        lines.append("<b>Where it came from</b>")
        for listing in listings:
            source = listing.get("source")
            parts = [esc(listing["vendor"]) + (f" ({esc(source)})" if source and source != listing["vendor"] else "")]
            if listing.get("camera_name"):
                parts.append(f"models: {esc(', '.join(listing['camera_name']))}")
            if listing.get("release_date"):
                parts.append(f"listed date: {esc(listing['release_date'])}")
            if listing.get("url"):
                parts.append(f"original link: {link(listing['url'])}")
            lines.append(" - " + "; ".join(parts))
            for note in listing.get("notes") or []:
                lines.append(f"&nbsp;&nbsp;&nbsp;note: {esc(note)}")
    else:
        if vendors:
            lines.append(f"Distributed by: {esc(', '.join(vendors))}")
        if models:
            lines.append(f"Vendor model names: {esc(', '.join(models))}")
        if record.get("url"):
            lines.append(f"Original link: {link(record['url'])}")
        for note in record.get("notes") or []:
            lines.append(f"Note: {esc(note)}")

    # What was found by unpacking it
    lines.append("<b>Firmware details</b>")
    platform = PLATFORM_NAMES.get(record.get("platform"))
    if platform:
        lines.append(f"Platform: {platform} firmware (identified from the file's header and naming)")
    if hardware_ids:
        lines.append(f"Hardware IDs this firmware installs on (read from inside the firmware): {esc(', '.join(hardware_ids))}")
    for label, field in [("Version", "firmware_version"), ("Release date", "release_date"),
                         ("Size", "size"), ("MD5 of this file", "archived_md5"),
                         ("Vendor-published MD5", "vendor_md5"), ("Vendor-published SHA256", "vendor_sha256")]:
        if record.get(field):
            lines.append(f"{label}: {esc(str(record[field]))}")
    if record.get("changelog"):
        lines.append(f"Release notes: {link(record['changelog'])}")

    metadata = {
        "title": file_name,
        "mediatype": "software",
        "collection": COLLECTION,
        "description": "<br>".join(lines),
        # Only tag a platform that was actually identified; some OEMs (GSS Red|LINE) sell Hikvision, not Dahua
        "subject": [PROJECT_TAG, "firmware", "cctv"]
                   + ([record["platform"]] if record.get("platform") in PLATFORM_NAMES else [])
                   + vendors + models[:MAX_SUBJECT_MODELS],
    }
    if vendors:
        metadata["creator"] = vendors
    if record.get("release_date"):
        metadata["date"] = record["release_date"]
    if record.get("url"):
        metadata["originalurl"] = record["url"]
    if record.get("firmware_version"):
        metadata["version"] = record["firmware_version"]

    return metadata


def upload_record(identifier, record):
    file_name = record["file_name"]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(record, f, indent=2, sort_keys=True)
        temp_path = f.name
    try:
        responses = internetarchive.upload(identifier, files={f"{file_name}.provenance.json": temp_path},
                                           retries=10, retries_sleep=60, queue_derive=False)
        for response in responses:
            response.raise_for_status()
    finally:
        os.remove(temp_path)


def archive_firmware(path, entry, hardware_ids):
    """Upload a firmware and its provenance record to its own Internet Archive item, or reuse an identical upload.

    Returns a dict of the fields to store in cameras.json. Raises on failure.
    """
    file_name = os.path.basename(path)
    identifier = get_identifier(file_name)
    item_url, download_url = get_urls(identifier, file_name)

    local_md5 = md5_file(path)
    record = get_record(file_name, os.path.getsize(path), local_md5, entry, hardware_ids)
    result = {"archive_item": item_url, "archive_url": download_url, "archive_md5": local_md5,
              "archive_record_hash": get_record_hash(record)}

    # Idempotent: an earlier run may have uploaded it before being interrupted
    item = internetarchive.get_item(identifier)
    if item.exists:
        for item_file in item.files:
            if item_file.get("name") == file_name and item_file.get("md5") == local_md5:
                refresh_archive(file_name, entry, hardware_ids, local_md5, os.path.getsize(path))
                return result

    # New items only appear once archive.org works through its task queue, which can take a while.
    # If an upload is still being processed, don't upload it again
    tasks = internetarchive.get_session().get_tasks_summary(identifier)
    if tasks.get("queued", 0) or tasks.get("running", 0):
        # Leave the record hash unset so the next run fills in the metadata and provenance record
        result.pop("archive_record_hash")
        return result

    responses = internetarchive.upload(
        identifier,
        files={file_name: path},
        metadata=get_metadata(record),
        checksum=True,
        verify=True,
        retries=10,
        retries_sleep=60,
        queue_derive=False,
    )
    for response in responses:
        response.raise_for_status()
    upload_record(identifier, record)

    return result


def refresh_archive(file_name, entry, hardware_ids, archived_md5, size):
    """Update an archived item's metadata and provenance record (e.g. new hardware IDs or vendors) without
    re-uploading the firmware. Returns the new record hash."""
    identifier = get_identifier(file_name)
    record = get_record(file_name, size, archived_md5, entry, hardware_ids)

    response = internetarchive.get_item(identifier).modify_metadata(get_metadata(record))
    # 400 "no changes to _meta.xml" just means it's already current
    if response.status_code != 200 and "no changes" not in response.text.lower():
        response.raise_for_status()
    upload_record(identifier, record)

    return get_record_hash(record)


def needs_refresh(file_name, entry, hardware_ids, size):
    record = get_record(file_name, size, entry.get("archive_md5"), entry, hardware_ids)
    return get_record_hash(record) != entry.get("archive_record_hash")
