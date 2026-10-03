import re
from urllib.parse import unquote

from util.file_type import classify_file_type
from util.hardware import FIRMWARE_LIKE
from util.oem_helpers import DAHUA_VERSION, crawl_index, is_dahua_firmware_name, make_firmware

# Folder names that organise files rather than name a model (languages, old/new builds, years, ...)
GENERIC_FOLDER = re.compile(
    r"^(old|new|latest|archive|previous|fw|update|beta|test|backup|other|others|misc|"
    r"english|eng|polish|czech|german|french|spanish|italian|russian|multilang|"
    r"general|dh|dhi|dahua|"
    # Polish, Ukrainian and Russian distributors' folders: old, older, new, archive, current, other, temporary,
    # Russian/Ukrainian/Polish builds, Polish translation
    r"stare|stary|starsze|starszy|nowe|nowy|archiwum|aktualne|inne|temp|rus|ukr|pol|old-pl|spolszczenie|"
    r"\d{4}(_a_starsi)?|v\d[\d.]*|\d+)$"
    # Descriptions rather than models: "PTZ firmware", "old-firmware", "fimrware", "Greek config", "Upgrade_z_V1.0",
    # numbered groups like "02-Pin-hole", notes like "SIP_4.5_210812_CZ" or "Custom_FW_pro_..."
    r"|firm?ware|fimrware|config|upgrade|^custom|^sip_|^\d{2}-|\s"
    # Numbered category folders like "04IPC", "05SD", "060-Lite", "06VDP"
    r"|^\d{2,3}-?[A-Za-z]+$"
    # "EASYCAM_STARSZE_MODELE" (older models), "Starszy-2.460-PL" / "Nowy-Aktualny-2.622-ENG" (older / new current
    # builds), versions like "5.0" or "V4.0-PL", and sub-series folders like "S2"
    r"|starsze|^(nowy|nowe|stary|stare|starszy)[-_]|^v?\d+(\.\d+)+(-[a-z]+)*$|^s\d$",
    re.IGNORECASE,
)


# Folders without a digit or an "XX" placeholder ("IPC-XXBXX", "NVRXBXX") never name a model or series: platform
# code names ("Mao-Rhea", "Cauchy"), product lines ("Eco-Savvy", "Consumer"), languages, "master"/"slave" boards, ...
# For mirrors whose trees have many of those
NO_MODEL_FOLDER = re.compile(r"^(?!.*(\d|xx)).*$", re.IGNORECASE)


def get_models_from_path(relative_directory, category_folders=(), generic_folder=None):
    """The nearest folder that names models, split into models (e.g. "DVR0804HD-A,DVR1604HD-L/old/").
    category_folders are the mirror's own grouping folders (e.g. "IPC_Cameras"), which never name a model, and
    generic_folder an extra regex for folders that don't."""
    for folder in reversed([part for part in relative_directory.split("/") if part]):
        # Some mirrors put each firmware in a folder named after it
        if FIRMWARE_LIKE.search(folder) or DAHUA_VERSION.search(folder):
            continue
        if generic_folder is not None and generic_folder.search(folder):
            continue
        if not GENERIC_FOLDER.search(folder) and folder not in category_folders:
            return [model.strip() for model in re.split(r",|;", folder) if model.strip()]
    return []


def is_firmware_file(file_name, url=None):
    """A Dahua style firmware name that isn't software or a document."""
    return is_dahua_firmware_name(file_name) and classify_file_type(file_name, url) == "firmware"


def get_mirror_firmwares(start_url, headers=None, skip_directories=(), category_folders=(), generic_folder=None,
                         keep=None, **crawl_options):
    """Every Dahua firmware below a directory index, with models taken from the folder names.
    keep(file_name, url) replaces the default is_dahua_firmware_name filter; crawl_options (delay, max_failures,
    timeout) go to crawl_index."""
    keep = keep or (lambda file_name, url: is_dahua_firmware_name(file_name))
    firmwares = []
    for url, directory in crawl_index(start_url, headers=headers, skip_directories=skip_directories,
                                      **crawl_options):
        file_name = unquote(url.split("/")[-1])
        if not keep(file_name, url):
            continue

        relative_directory = unquote(directory[len(start_url):])
        models = get_models_from_path(relative_directory, category_folders, generic_folder)
        firmware = make_firmware(models, url, file_name=file_name)
        firmware["firmware_notes"] = f"Folder: {relative_directory.strip('/')}" if relative_directory.strip("/") else None
        firmwares.append(firmware)

    return firmwares
