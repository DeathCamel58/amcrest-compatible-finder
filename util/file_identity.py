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


def main_entry_sort_key(name, entry, analysed_names=()):
    return (
        # prefer a copy already on archive.org, then one already analysed, so neither is redone for another copy
        not (entry.get("archive_url") and not entry.get("archive_pending")),
        name not in analysed_names,
        bool(COPY_MARKERS.search(name)),  # then names without copy markers
        not entry.get("listings"),        # then one that a vendor lists
        len(name),                        # then the shortest
        name,
    )


def assign_duplicates(cameras_json, file_names, analysed_names=()):
    """Group files with identical content. The main entry gets "aliases", the others "duplicate_of".
    analysed_names are files that already have results in firmware_compatible.json.
    Returns the number of duplicate files."""
    by_hash = {}
    for name in file_names:
        sha256 = (cameras_json.get(name, {}).get("file_hashes") or {}).get("sha256")
        if sha256:
            by_hash.setdefault(sha256, []).append(name)

    duplicates = 0
    for names in by_hash.values():
        names.sort(key=lambda name: main_entry_sort_key(name, cameras_json[name], analysed_names))
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


# How much of a file's start is hashed to find the complete copy of a truncated file
HEAD_BYTES = 64 * 1024


def head_md5(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read(HEAD_BYTES)).hexdigest()


def is_prefix(short_path, long_path, chunk_size=4 * 1024 * 1024):
    """Whether the file at short_path is the start of the one at long_path."""
    with open(short_path, "rb") as short, open(long_path, "rb") as long:
        for chunk in iter(lambda: short.read(chunk_size), b""):
            if long.read(len(chunk)) != chunk:
                return False
    return True


def assign_truncated_copies(cameras_json, file_names, path_for):
    """Make each truncated file that is the start of a complete file (the same download cut off, saved under another
    name) a duplicate of it, marked "truncated_copy". Run after assign_duplicates. The start of each file is hashed
    once and kept in its file_hashes as "head_md5". Returns the number of files marked."""
    def head(name):
        hashes = cameras_json[name].get("file_hashes") or {}
        if "head_md5" not in hashes and hashes:
            hashes["head_md5"] = head_md5(path_for(name))
        return hashes.get("head_md5")

    def is_truncated(name):
        return (cameras_json[name].get("integrity") or {}).get("status") == "truncated"

    truncated = [name for name in file_names
                 if is_truncated(name) and not cameras_json[name].get("duplicate_of")
                 and not cameras_json[name].get("aliases")]
    for name in file_names:
        cameras_json[name].pop("truncated_copy", None)
    if not truncated:
        return 0

    sizes = {name: (cameras_json[name].get("file_hashes") or {}).get("size") or 0 for name in file_names}
    by_head = {}
    for name in file_names:
        if not is_truncated(name) and not cameras_json[name].get("duplicate_of") and sizes[name] > HEAD_BYTES:
            by_head.setdefault(head(name), []).append(name)

    marked = 0
    for name in truncated:
        if sizes[name] < HEAD_BYTES:
            continue
        candidates = [other for other in by_head.get(head(name), []) if sizes[other] > sizes[name]]
        main = next((other for other in sorted(candidates, key=lambda other: sizes[other])
                     if is_prefix(path_for(name), path_for(other))), None)
        if main:
            cameras_json[name]["duplicate_of"] = main
            cameras_json[name]["truncated_copy"] = True
            aliases = cameras_json[main].setdefault("aliases", [])
            if name not in aliases:
                aliases.append(name)
            marked += 1
    return marked
