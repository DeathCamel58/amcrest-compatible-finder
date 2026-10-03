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
RECORD_FIELDS = ["platform", "integrity", "vendors", "camera_name", "series", "notes", "url", "firmware_version",
                 "release_date", "changelog", "md5", "sha256"]
# Listing fields that change on every run; leaving them out keeps items from being updated each time
VOLATILE_LISTING_FIELDS = {"last_seen", "last_seen_latest", "url_checked_at", "url_status"}

PLATFORM_NAMES = {"dahua": "Dahua", "hikvision": "Hikvision"}


def stable_listings(listings):
    return [{k: v for k, v in listing.items() if k not in VOLATILE_LISTING_FIELDS} for listing in listings or []]


def get_record(file_name, size, md5, entry, analysis, alias_entries):
    """Machine-readable provenance for the item: where the file came from and what was found inside it.

    analysis is the file's firmware_compatible.json result, alias_entries maps other names with identical content
    to their cameras.json entries."""
    analysis = analysis or {}
    record = {
        "file_name": file_name,
        "size": size,
        "archived_md5": md5,
        "archived_sha256": (entry.get("file_hashes") or {}).get("sha256"),
        "hardware_ids": analysis.get("hardware_ids") or [],
        "hardware": analysis.get("hardware"),
        "analysis_status": analysis.get("status"),
        # Where each hardware ID was read from, and each firmware image set's SoC, partitions, security and versions
        "hardware_sources": analysis.get("hardware_sources"),
        "packages": analysis.get("packages"),
        "package_format": analysis.get("package_format"),
        "listings": stable_listings(entry.get("listings")),
        "archived_by": "amcrest-compatible-finder",
    }
    for field in RECORD_FIELDS:
        if entry.get(field):
            # The vendor checksums are kept apart from the checksums of what was actually archived
            record[f"vendor_{field}" if field in ("md5", "sha256") else field] = entry[field]

    # The same file under other names, and where those were published
    aliases = []
    for alias, alias_entry in sorted(alias_entries.items()):
        aliases.append({"file_name": alias, "url": alias_entry.get("url"),
                        "listings": stable_listings(alias_entry.get("listings"))})
    if aliases:
        record["aliases"] = aliases

    return {key: value for key, value in record.items() if value not in (None, [], {})}


def get_record_hash(record):
    return hashlib.sha1(json.dumps(record, sort_keys=True).encode()).hexdigest()


def get_socs(record):
    """Distinct SoC names (or vendors, when the chip isn't known) of the record's packages."""
    socs = []
    for package in record.get("packages") or []:
        soc = package.get("soc") or {}
        label = soc.get("name") or soc.get("vendor")
        if label and label not in socs:
            socs.append(label)
    return socs


def package_lines(record):
    """Description lines for what the firmware says about itself: SoC, architecture, versions, flash, security."""
    esc = html.escape
    lines = []
    packages = record.get("packages") or []
    for package in packages:
        parts = []
        soc = package.get("soc") or {}
        if soc.get("name") or soc.get("vendor"):
            parts.append(f"SoC: {esc(' '.join(v for v in (soc.get('vendor'), soc.get('name')) if v))}")
        for label, field in [("architecture", "architecture"), ("kernel", "kernel_version"),
                             ("boot loader", "bootloader_version"), ("OEM build", "oem_vendor")]:
            if package.get(field):
                parts.append(f"{label}: {esc(str(package[field]))}")
        if package.get("flash_size"):
            parts.append(f"flash: {package['flash_size'] // (1024 * 1024)} MB")
        if package.get("partitions"):
            parts.append(f"{len(package['partitions'])} flash partitions")
        security = package.get("security") or {}
        if security.get("signed"):
            parts.append("signed")
        if security.get("encrypted"):
            parts.append("encrypted images")
        if security.get("security_baseline"):
            parts.append(f"security baseline {esc(security['security_baseline'])}")
        if parts:
            prefix = f"{esc(package['firmware'])}: " if package.get("firmware") and len(packages) > 1 else ""
            lines.append(prefix + "; ".join(parts))
    return lines


def link(url):
    return f'<a href="{html.escape(url)}">{html.escape(url)}</a>'


def get_metadata(record):
    file_name = record["file_name"]
    vendors = record.get("vendors") or []
    models = record.get("camera_name") or []
    hardware = record.get("hardware") or {}
    aliases = record.get("aliases") or []
    esc = html.escape
    # Vendors of identical copies under other names count as distributors of this item too
    for alias in aliases:
        for listing in alias.get("listings") or []:
            if listing.get("vendor") and listing["vendor"] not in vendors:
                vendors = vendors + [listing["vendor"]]

    lines = []
    integrity = record.get("integrity") or {}
    if integrity.get("status") == "truncated":
        source_note = (" The vendor's own copy is cut off in the same place." if integrity.get("vendor_copy_truncated")
                       else "")
        lines.append(f"<b>Warning: this file is incomplete</b> ({esc(integrity.get('reason', 'truncated'))}), so it "
                     f"can't be installed. It's kept because no complete copy could be found.{source_note} The "
                     f"hardware IDs below come from the intact part of the file.")
    lines.append(f"Firmware file <b>{esc(file_name)}</b>, archived by amcrest-compatible-finder so it stays available "
                 f"after vendors remove it. The {esc(file_name)}.provenance.json file in this item has the same data "
                 f"in machine-readable form.")

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
        if record.get("series"):
            lines.append(f"Product series: {esc(', '.join(record['series']))}")
    else:
        if vendors:
            lines.append(f"Distributed by: {esc(', '.join(vendors))}")
        if models:
            lines.append(f"Vendor model names: {esc(', '.join(models))}")
        if record.get("url"):
            lines.append(f"Original link: {link(record['url'])}")
        for note in record.get("notes") or []:
            lines.append(f"Note: {esc(note)}")

    # Identical copies published under other names
    if aliases:
        lines.append("<b>Also published under these names</b> (byte-for-byte identical)")
        for alias in aliases:
            sources = [f"{esc(l['vendor'])}: {link(l['url'])}" for l in alias.get("listings") or [] if l.get("url")]
            if not sources and alias.get("url"):
                sources = [link(alias["url"])]
            lines.append(f" - {esc(alias['file_name'])}" + (f" ({'; '.join(sources)})" if sources else ""))

    # What was found by unpacking it
    lines.append("<b>Firmware details</b>")
    platform = PLATFORM_NAMES.get(record.get("platform"))
    if platform:
        lines.append(f"Platform: {platform} firmware (identified from the file's header and naming)")
    # Read from inside the firmware: what its installer accepts
    for label, group in [("Models it installs on", "models"), ("Board families it installs on", "boards"),
                         ("Raw hardware IDs it installs on", "hwids")]:
        if hardware.get(group):
            lines.append(f"{label} (read from inside the firmware): {esc(', '.join(hardware[group]))}")
    lines += package_lines(record)
    for label, field in [("Version", "firmware_version"), ("Release date", "release_date"),
                         ("Size", "size"), ("MD5 of this file", "archived_md5"), ("SHA256 of this file", "archived_sha256"),
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
                   + (["incomplete"] if integrity.get("status") == "truncated" else [])
                   + vendors + models[:MAX_SUBJECT_MODELS]
                   + [f"soc:{soc}" for soc in get_socs(record)],
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


def archive_firmware(path, entry, analysis, alias_entries):
    """Upload a firmware and its provenance record to its own Internet Archive item, or reuse an identical upload.

    Returns a dict of the fields to store in cameras.json. Raises on failure.
    """
    file_name = os.path.basename(path)
    identifier = get_identifier(file_name)
    item_url, download_url = get_urls(identifier, file_name)

    # Reuse the hashes from the enrich step when they're for this exact file
    hashes = entry.get("file_hashes") or {}
    local_md5 = hashes["md5"] if hashes.get("size") == os.path.getsize(path) and hashes.get("md5") else md5_file(path)
    record = get_record(file_name, os.path.getsize(path), local_md5, entry, analysis, alias_entries)
    result = {"archive_item": item_url, "archive_url": download_url, "archive_md5": local_md5,
              "archive_record_hash": get_record_hash(record)}

    # Idempotent: an earlier run may have uploaded it before being interrupted
    item = internetarchive.get_item(identifier)
    if item.exists:
        for item_file in item.files:
            if item_file.get("name") == file_name and item_file.get("md5") == local_md5:
                refresh_archive(file_name, entry, analysis, alias_entries, local_md5, os.path.getsize(path))
                return result

    # New items only appear once archive.org works through its task queue, which can take a while.
    # If an upload is still being processed, don't upload it again
    tasks = internetarchive.get_session().get_tasks_summary(identifier)
    if tasks.get("queued", 0) or tasks.get("running", 0):
        # Not archived yet (the upload may still fail): the next run checks the item again and fills in the
        # archive URL, MD5 and provenance record once the file is there
        return {"archive_item": item_url, "archive_pending": True}

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


def refresh_archive(file_name, entry, analysis, alias_entries, archived_md5, size):
    """Update an archived item's metadata and provenance record (e.g. new hardware IDs or vendors) without
    re-uploading the firmware. Returns the new record hash."""
    identifier = get_identifier(file_name)
    record = get_record(file_name, size, archived_md5, entry, analysis, alias_entries)

    response = internetarchive.get_item(identifier).modify_metadata(get_metadata(record))
    # 400 "no changes to _meta.xml" just means it's already current
    if response.status_code != 200 and "no changes" not in response.text.lower():
        response.raise_for_status()
    upload_record(identifier, record)

    return get_record_hash(record)


def needs_refresh(file_name, entry, analysis, alias_entries, size):
    record = get_record(file_name, size, entry.get("archive_md5"), entry, analysis, alias_entries)
    return get_record_hash(record) != entry.get("archive_record_hash")
