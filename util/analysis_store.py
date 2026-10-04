"""Where firmware analysis results are stored.

firmware_compatible.json is a small index: one entry per firmware file name with what search and listings need
(status, hardware_ids, hardware, a SoC summary) and the path of its detail file. The detail (where each hardware ID
was read from, and every package with its SoC, security and partition layout) is in one file per firmware content:

    data/firmware/<first 2 hex>/<first 16 hex of the file's SHA-256>.json
    data/layouts/<first 2 hex>/<16 hex>.json     one per distinct partition layout, shared by every firmware using it

Identical copies of a firmware (duplicates) share one detail file. Nothing here depends on file names, so long,
mixed-case or non-ASCII firmware names can't cause path-length or case-collision problems.
"""
import hashlib
import json
import os
import tempfile

DATA_DIR = "data"
DETAIL_DIR = os.path.join(DATA_DIR, "firmware")
LAYOUT_DIR = os.path.join(DATA_DIR, "layouts")
ID_LENGTH = 16

# What stays in the index; everything else in a result goes to its detail file
DETAIL_FIELDS = ("hardware_sources", "packages")
# A partition row's layout (shared between firmwares) and the rest (this firmware's image for that partition)
LAYOUT_FIELDS = ("name", "start", "end", "size", "read_only", "backup_start")


def content_id(sha256):
    return sha256[:ID_LENGTH].lower()


def detail_path(sha256):
    """Repo-relative path of a firmware's detail file, from the file's SHA-256."""
    file_id = content_id(sha256)
    return f"{DETAIL_DIR}/{file_id[:2]}/{file_id}.json"


def layout_id(rows):
    text = json.dumps([{field: row.get(field) for field in LAYOUT_FIELDS} for row in rows], sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()[:ID_LENGTH]


def layout_path(layout):
    return f"{LAYOUT_DIR}/{layout[:2]}/{layout}.json"


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=1, sort_keys=True)
            f.write("\n")
        os.replace(temp_path, path)
    except BaseException:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise


def _read_json(path):
    with open(path) as f:
        return json.load(f)


def socs_summary(packages):
    """The distinct SoCs named across a firmware's packages, for the index: [{"name", "vendor"}]."""
    socs = []
    for package in packages or []:
        soc = package.get("soc") or {}
        summary = {key: soc.get(key) for key in ("name", "vendor") if soc.get(key)}
        if summary and summary not in socs:
            socs.append(summary)
    return socs


def dedupe_sources(sources):
    """When a source lists exactly the same raw entries as an earlier one of the same firmware (its hwid file and
    check.img usually do), drop its raw and set raw_same_as to the earlier source's position in the list."""
    result = []
    for source in sources or []:
        source = dict(source)
        raw = source.get("raw")
        earlier = next((i for i, s in enumerate(result) if raw and "raw" in s and s["raw"] == raw
                        and s.get("firmware") == source.get("firmware")), None)
        if earlier is not None:
            del source["raw"]
            source["raw_same_as"] = earlier
        result.append(source)
    return result


def expand_sources(sources):
    result = []
    for source in sources or []:
        source = dict(source)
        same_as = source.pop("raw_same_as", None)
        if same_as is not None:
            source["raw"] = [list(item) if isinstance(item, list) else item for item in result[same_as]["raw"]]
        result.append(source)
    return result


def split_packages(packages):
    """(packages with partitions replaced by a layout reference, {layout id: layout rows})."""
    layouts = {}
    stored = []
    for package in packages or []:
        package = dict(package)
        rows = package.pop("partitions", None)
        if not rows:
            if rows is not None:
                package["partitions"] = rows  # an empty table stays as it is
        else:
            layout = layout_id(rows)
            layouts[layout] = [{field: row[field] for field in LAYOUT_FIELDS if field in row} for row in rows]
            package["partition_layout"] = layout
            package["partition_images"] = [{key: value for key, value in row.items() if key not in LAYOUT_FIELDS}
                                           for row in rows]
        stored.append(package)
    return stored, layouts


def join_packages(packages, load_layout):
    joined = []
    for package in packages or []:
        package = dict(package)
        layout = package.pop("partition_layout", None)
        images = package.pop("partition_images", None)
        if layout:
            rows = load_layout(layout)["partitions"]
            package["partitions"] = [{**row, **(images[i] if images and i < len(images) else {})}
                                     for i, row in enumerate(rows)]
        joined.append(package)
    return joined


def split_result(result, sha256):
    """(index entry, detail document or None, {layout id: rows}) for a full analysis result."""
    index = {key: value for key, value in result.items() if key not in DETAIL_FIELDS}
    if not sha256 or not any(result.get(field) for field in DETAIL_FIELDS):
        return index, None, {}
    packages, layouts = split_packages(result.get("packages"))
    detail = {
        "sha256": sha256.lower(),
        "hardware_sources": dedupe_sources(result.get("hardware_sources")),
        "packages": packages,
    }
    socs = socs_summary(result.get("packages"))
    if socs:
        index["socs"] = socs
    index["detail"] = detail_path(sha256)
    return index, detail, layouts


def write_detail(detail, layouts):
    """Write a detail file and any layouts it uses that aren't stored yet."""
    for layout, rows in layouts.items():
        path = layout_path(layout)
        if not os.path.exists(path):
            _write_json(path, {"id": layout, "partitions": rows})
    _write_json(detail_path(detail["sha256"]), detail)


def load_layout(layout):
    return _read_json(layout_path(layout))


def join_result(index_entry):
    """The full result (as before the split) for an index entry: its detail merged back in, layouts expanded."""
    if not index_entry:
        return index_entry
    result = {key: value for key, value in index_entry.items() if key not in ("detail", "socs")}
    path = index_entry.get("detail")
    if path and os.path.exists(path):
        detail = _read_json(path)
        result["hardware_sources"] = expand_sources(detail.get("hardware_sources"))
        result["packages"] = join_packages(detail.get("packages"), load_layout)
    elif "package_format" in index_entry:
        # Analysed (extractor version 5+) but nothing found to put in a detail file
        result["hardware_sources"] = []
        result["packages"] = []
    return result


def referenced_files(index):
    """(detail paths, layout paths) the index refers to."""
    details = {entry["detail"] for entry in index.values() if entry.get("detail")}
    layouts = set()
    for path in details:
        if os.path.exists(path):
            for package in _read_json(path).get("packages") or []:
                if package.get("partition_layout"):
                    layouts.add(layout_path(package["partition_layout"]))
    return details, layouts


def stored_files(directory):
    found = set()
    for root, _, names in os.walk(directory):
        for name in names:
            if name.endswith(".json") and not name.startswith("."):
                found.add(os.path.join(root, name).replace(os.sep, "/"))
    return found


def remove_unreferenced(index):
    """Delete detail and layout files nothing in the index refers to (e.g. after a file's content changed).
    Returns how many were removed."""
    details, layouts = referenced_files(index)
    removed = 0
    for path in (stored_files(DETAIL_DIR) - details) | (stored_files(LAYOUT_DIR) - layouts):
        os.remove(path)
        removed += 1
    return removed
