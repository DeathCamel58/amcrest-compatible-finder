"""Firmware we know exists but haven't got: wanted.json.

Each entry is a firmware file name or a version (with the models it's for) seen referenced somewhere: a dataset, a
forum post, a release note, another vendor's listing. Every enrich run checks the list against cameras.json and marks
entries found once the firmware is downloaded, so the list always shows what's still missing.

    python -m util.wanted add --name DH_XVR5x04-S2_EngSpnFrn_NP_V3.218.0000002.1.R.171229.bin \\
        --source "FirmSecDataset" --url https://github.com/FirmSecDataset/FirmSecDataset
    python -m util.wanted add --version V2.800.0000000.25.R.230425 --model IPC-HX5XXX-Volt --source "ipcamtalk"
    python -m util.wanted status
"""
import argparse
import json
import os
import re
import sys
from datetime import date

WANTED_JSON = "wanted.json"

# "1DH_...", "2_General_...", "414330_DH_..." (copy numbers and download IDs some servers prefix names with)
NAME_PREFIX = re.compile(r"^(\d{1,7}[_.]?)(?=[A-Za-z])")
EXTENSION = re.compile(r"\.(bin|zip|rar|7z|img|dav|tar|gz)$", re.IGNORECASE)


# " (1)", "(2)", "-copy": what browsers and people add to downloaded file names
DOWNLOAD_SUFFIX = re.compile(r"\s*(\(\d+\)|-copy|_copy)$", re.IGNORECASE)
# ".20180920.63540": a build number after the date, which some servers add (the files inside don't have it)
BUILD_SUFFIX = re.compile(r"(?<=\.\d{6})\.\d{3,6}$|(?<=\.\d{8})\.\d{3,6}$")


def name_key(name):
    """A name without its extensions, copy-number prefix, download or build suffix and case, so renamed copies
    match: "1DH_X_V2.R.20180920.63540 (1).bin.zip" and "DH_X_V2.R.20180920.bin" are the same firmware."""
    name = name.strip()
    while True:
        stripped = DOWNLOAD_SUFFIX.sub("", EXTENSION.sub("", name))
        if stripped == name:
            break
        name = stripped
    name = BUILD_SUFFIX.sub("", name)
    name = NAME_PREFIX.sub("", name)
    return name.casefold()


def rekey(wanted):
    """Re-key entries after name_key changes, merging entries that now share a key."""
    rekeyed = {}
    for entry in wanted.values():
        key = entry_key(entry.get("name"), entry.get("version"), entry.get("models") or ())
        existing = rekeyed.get(key)
        if existing is None:
            rekeyed[key] = entry
            continue
        for reference in entry.get("references") or []:
            if reference not in existing["references"]:
                existing["references"].append(reference)
        existing["found"] = existing.get("found") or entry.get("found")
    return rekeyed


def entry_key(name=None, version=None, models=()):
    if name:
        return f"name:{name_key(name)}"
    return f"version:{version.strip().casefold()}" + (f"@{','.join(sorted(m.casefold() for m in models))}"
                                                       if models else "")


def load(path=WANTED_JSON):
    try:
        with open(path) as f:
            return rekey(json.load(f))
    except FileNotFoundError:
        return {}


def save(wanted, path=WANTED_JSON):
    temp_path = f"{path}.{os.getpid()}.tmp"
    with open(temp_path, "w") as f:
        json.dump(wanted, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(temp_path, path)


def add(wanted, name=None, version=None, models=(), source=None, url=None, note=None, noted=None):
    """Add a reference to a wanted firmware (or another reference to one already listed). Returns its key."""
    if not name and not version:
        raise ValueError("a wanted firmware needs a name or a version")
    key = entry_key(name, version, models)
    entry = wanted.setdefault(key, {"name": name, "version": version, "models": sorted(set(models)),
                                    "references": [], "found": None})
    reference = {k: v for k, v in (("source", source), ("url", url), ("note", note),
                                   ("noted", noted or date.today().isoformat())) if v}
    if not any(r.get("source") == reference.get("source") and r.get("url") == reference.get("url")
               for r in entry["references"]):
        entry["references"].append(reference)
    return key


def find(entry, by_name, by_version):
    """The cameras.json file name that satisfies a wanted entry, or None."""
    if entry.get("name"):
        return by_name.get(name_key(entry["name"]))
    for file_name, models in by_version.get(entry["version"].strip().casefold(), []):
        wanted_models = {m.casefold() for m in entry.get("models") or []}
        if not wanted_models or wanted_models & models or any(m in file_name.casefold() for m in wanted_models):
            return file_name
    return None


def inner_firmware_names(compat_index):
    """{inner firmware name: outer file name} from the analysis detail files: the firmwares inside zips and bundles
    (a wanted name is sometimes the .bin inside a zip we have under another name)."""
    inner = {}
    for file_name, result in (compat_index or {}).items():
        path = result.get("detail")
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path) as f:
                packages = json.load(f).get("packages") or []
        except (OSError, ValueError):
            continue
        for package in packages:
            if package.get("firmware"):
                inner.setdefault(package["firmware"], file_name)
    return inner


def refresh(wanted, cameras_json, today=None, inner_names=None):
    """Mark entries found that cameras.json now has (also by the firmwares inside its zips, when inner_names is
    given). Returns (newly found, still missing)."""
    by_name, by_version = {}, {}
    for inner, file_name in (inner_names or {}).items():
        if (cameras_json.get(file_name) or {}).get("file_hashes"):
            by_name.setdefault(name_key(inner), file_name)
    for file_name, entry in cameras_json.items():
        if not entry.get("file_hashes"):
            continue  # listed somewhere but not downloaded: still wanted
        by_name[name_key(file_name)] = file_name
        if entry.get("firmware_version"):
            version = re.sub(r"\.\d{6,8}$", "", entry["firmware_version"]).casefold()
            models = {m.casefold() for m in (entry.get("camera_name") or []) + (entry.get("series") or [])}
            for v in {version, entry["firmware_version"].casefold()}:
                by_version.setdefault(v, []).append((file_name, models))

    newly_found = 0
    for entry in wanted.values():
        if entry.get("found"):
            continue
        file_name = find(entry, by_name, by_version)
        if file_name:
            entry["found"] = {"file": file_name, "date": today or date.today().isoformat()}
            newly_found += 1
    missing = sum(1 for entry in wanted.values() if not entry.get("found"))
    return newly_found, missing


def refresh_file(cameras_json, path=WANTED_JSON):
    """Refresh wanted.json against cameras.json, if it exists. Returns (newly found, still missing) or None."""
    if not os.path.exists(path):
        return None
    from util.json_tools import get_firmware_json
    wanted = load(path)
    result = refresh(wanted, cameras_json, inner_names=inner_firmware_names(get_firmware_json()))
    save(wanted, path)
    return result


def main(argv):
    parser = argparse.ArgumentParser(prog="python -m util.wanted", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    add_parser = commands.add_parser("add", help="note a firmware we don't have")
    add_parser.add_argument("--name")
    add_parser.add_argument("--version")
    add_parser.add_argument("--model", action="append", default=[])
    add_parser.add_argument("--source", required=True, help="where the reference was seen")
    add_parser.add_argument("--url")
    add_parser.add_argument("--note")
    commands.add_parser("status", help="refresh against cameras.json and print what's still missing")
    args = parser.parse_args(argv)

    from util.json_tools import get_cameras_json, get_firmware_json
    wanted = load()
    inner = inner_firmware_names(get_firmware_json())
    if args.command == "add":
        key = add(wanted, args.name, args.version, args.model, args.source, args.url, args.note)
        found, missing = refresh(wanted, get_cameras_json(), inner_names=inner)
        save(wanted)
        print(f"{key}: {'already have it (' + wanted[key]['found']['file'] + ')' if wanted[key]['found'] else 'wanted'}"
              f"; {missing} still missing")
    else:
        found, missing = refresh(wanted, get_cameras_json(), inner_names=inner)
        save(wanted)
        print(f"{len(wanted)} wanted firmwares: {len(wanted) - missing} found ({found} just now), {missing} missing")
        by_source = {}
        for entry in wanted.values():
            if not entry.get("found"):
                for reference in entry["references"][:1]:
                    by_source[reference.get("source")] = by_source.get(reference.get("source"), 0) + 1
        for source, count in sorted(by_source.items(), key=lambda item: -item[1]):
            print(f"    {count} missing, first referenced by {source}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
