import hashlib
import os
import re

# Copy markers that make a name a worse choice for the main entry: "...230408+(6).bin", "...210816+.bin",
# "... (2).bin", and numeric download-ID prefixes like "317700_General_..."
COPY_MARKERS = re.compile(r"\+?\s?\(\d+\)(?=\.[^.]+$)|\+(?=\.[^.]+$)|^\d{4,}_")


def hash_file(path):
    md5, sha256 = hashlib.md5(), hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            md5.update(chunk)
            sha256.update(chunk)
    return md5.hexdigest(), sha256.hexdigest()


def needs_hashing(entry, path):
    stat = os.stat(path)
    hashes = entry.get("file_hashes") or {}
    return hashes.get("size") != stat.st_size or hashes.get("mtime") != int(stat.st_mtime)


def get_file_hashes(path):
    """Hashes of the file on disk, with the size and mtime they were computed for (so they're only redone when the
    file changes)."""
    stat = os.stat(path)
    md5, sha256 = hash_file(path)
    return {"md5": md5, "sha256": sha256, "size": stat.st_size, "mtime": int(stat.st_mtime)}


def main_entry_sort_key(name, entry):
    return (
        bool(COPY_MARKERS.search(name)),  # prefer names without copy markers
        not entry.get("listings"),        # then one that a vendor lists
        len(name),                        # then the shortest
        name,
    )


def assign_duplicates(cameras_json, file_names):
    """Group files with identical content. The main entry gets "aliases", the others "duplicate_of".
    Returns the number of duplicate files."""
    by_hash = {}
    for name in file_names:
        sha256 = (cameras_json.get(name, {}).get("file_hashes") or {}).get("sha256")
        if sha256:
            by_hash.setdefault(sha256, []).append(name)

    duplicates = 0
    for names in by_hash.values():
        names.sort(key=lambda name: main_entry_sort_key(name, cameras_json[name]))
        main, others = names[0], names[1:]

        cameras_json[main].pop("duplicate_of", None)
        if others:
            cameras_json[main]["aliases"] = others
        else:
            cameras_json[main].pop("aliases", None)

        for other in others:
            cameras_json[other]["duplicate_of"] = main
            cameras_json[other].pop("aliases", None)
        duplicates += len(others)

    return duplicates
