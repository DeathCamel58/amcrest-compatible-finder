import re

# Raw 16 hex digit hardware IDs some older IPCs use (e.g. 0800425346118427)
HWID = re.compile(r"^[0-9A-Fa-f]{16}$")

# Names with placeholders describe a family of devices rather than one model. Real Dahua models often contain an
# X too (IPC-HDW8441X-3D, ASI7213X, CA-HZ2023XA-A), so only match actual placeholder patterns:
FAMILY_PATTERNS = [
    re.compile(r"\dx\d|x{2,}"),                       # HCVR5x04-S2, XVR7x16-I2, DVRxx04LE-AS
    re.compile(r"X{2,}"),                               # IPC-HX3XXX, SD6XXX, NVR4XXX-X
    re.compile(r"\dX\dX"),                              # IPC-HX5X3X, IPC-HX1X3X
    re.compile(r"(?:^|[-_ ])[A-Z]{2,4}\dX(?=$|[-/_ ])"),  # NVR4X-4KS2/L, NVR5X-4K
    re.compile(r"\(\d\)"),                              # IPC-HX3(2)XXX
]

# Tokens installer scripts use that aren't models (vendor names, RVI's "Group")
IGNORED_IDS = {"dahua", "group", "general", "default", "oem"}


def is_family_name(name):
    return any(pattern.search(name) for pattern in FAMILY_PATTERNS)


def fold_name(name):
    # check.img sometimes prefixes the board with "General_" (General_HCVR5x08-S2 vs HCVR5x08-S2)
    return re.sub(r"^General_", "", name.strip())


def classify_hardware_ids(hardware_ids):
    """Sort the raw IDs found in a firmware into models, board families, raw hardware IDs and ignored tokens."""
    hardware = {"models": [], "boards": [], "hwids": [], "ignored": []}
    for raw in hardware_ids:
        name = fold_name(raw)
        if not name or name.casefold() in IGNORED_IDS or FIRMWARE_LIKE.search(name):
            group = "ignored"
            name = raw
        elif HWID.match(name):
            group = "hwids"
        elif is_family_name(name):
            group = "boards"
        else:
            group = "models"
        if name not in hardware[group]:
            hardware[group].append(name)

    return {group: sorted(names) for group, names in hardware.items()}


SERIES_WORDS = re.compile(r"\bseries\b", re.IGNORECASE)
# Firmware file names that ended up as a "model" or hardware ID (version string, date suffix or extension)
FIRMWARE_LIKE = re.compile(r"_V\d+\.\d{2,3}\.|\.(bin|zip|dav|img|rar|pak)$|_\d{6,8}$", re.IGNORECASE)


# Product descriptions vendors put where a model name belongs ("Full Color firmware", "Updated Voice File",
# "32 Channel Firmware (Older than 2020)", "Special")
DESCRIPTION_WORDS = re.compile(
    r"\b(firmware|file|files|update|updated|upgrade|special|voice|channel|version|older|newer|latest|full color|"
    r"deterrence|tracking|main|produced|recommended|beta)\b",
    re.IGNORECASE,
)
# "IPC-TPC124X-AI-S2 (S2 version Produced in Feb.2024)": a model followed by a description in brackets
MODEL_WITH_DESCRIPTION = re.compile(r"^([A-Za-z0-9][\w\-/.+&]*\d[\w\-/.+&]*)\s*\((.+)\)$")
# A token that looks like a model number: letters and digits together, e.g. IPC-HDW2431T-AS-S2 or NVR5208
MODEL_TOKEN = re.compile(r"[A-Za-z]+[-_]?\d|\d+[A-Za-z]")


def split_model_names(names, file_name):
    """Split vendor model names into (models, series, notes).

    Names that are really a firmware file name are dropped, series names move to series, and descriptions move to
    notes. (A name equal to the file name is fine: GSS names files after the model, e.g. IPC-144M.bin.)"""
    models, series, notes = [], [], []
    for name in names:
        # Rhino and others sometimes use the firmware's file name as the "model"
        if not name or FIRMWARE_LIKE.search(name):
            continue

        match = MODEL_WITH_DESCRIPTION.match(name)
        if match and DESCRIPTION_WORDS.search(match.group(2)):
            name = match.group(1)
            notes.append(match.group(2).strip())
        else:
            # "PTZ3E10X-T180 Vehicle automatic tracking firmware": a model followed by a description
            first, _, rest = name.partition(" ")
            if rest and MODEL_TOKEN.search(first) and "-" in first and DESCRIPTION_WORDS.search(rest):
                name = first
                notes.append(rest.strip())

        if SERIES_WORDS.search(name) or is_family_name(name):
            series.append(name)
        elif DESCRIPTION_WORDS.search(name) and (" " in name or not MODEL_TOKEN.search(name)):
            notes.append(name)
        else:
            models.append(name)
    # Removing descriptions can leave the same name twice ("PTZ3E10X-T180 Vehicle tracking firmware" and
    # "PTZ3E10X-T180 ... firmware")
    return unique_names(models), unique_names(series), unique_names(notes)


def unique_names(names):
    """names without repeats, ignoring case; the first spelling is kept."""
    seen, unique = set(), []
    for name in names:
        if name.casefold() not in seen:
            seen.add(name.casefold())
            unique.append(name)
    return unique
