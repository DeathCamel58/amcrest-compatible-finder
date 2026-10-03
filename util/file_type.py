import os
import re
import zipfile

DOCUMENT_EXTENSIONS = (".pdf", ".doc", ".docx", ".txt", ".xls", ".xlsx", ".ppt", ".pptx", ".rtf", ".odt", ".csv",
                       ".chm", ".html", ".htm")
SOFTWARE_EXTENSIONS = (".exe", ".msi", ".dmg", ".apk", ".pkg", ".deb", ".rpm", ".iso", ".appimage")

# Word boundaries that also treat "_" as a separator (regex \b doesn't: "win11Pro_" has no \b after "Pro")
_B = r"(?<![A-Za-z0-9])"
_E = r"(?![A-Za-z0-9])"

# PC software, apps, tools, drivers and operating system images that vendors file next to firmware. These names
# always mean software
SOFTWARE_NAMES = re.compile(
    "|".join([
        r"smart.?pss", _B + r"dss" + _E, r"dss-", r"mobile.?center", r"smart.?parking", r"visual.?presenter",
        r"dahua.?link", r"config.?tool", r"disk.?manager", r"disk.?calculator", r"toolbox", r"upgrade.?tool",
        r"update.?tool", r"web.?plugin", r"-plugin", r"_plugin", r"plugin\.", r"smart.?player", r"stormvms",
        r"setup", r"installer", r"install_is", r"surveillance.?pro", r"openeuler",
        _B + r"win\d{1,2}(pro|home|ent)?" + _E, _B + r"win(32|64)" + _E, _B + r"windows" + _E, r"\.pkg" + _E,
        r"aio_software", r"cd\+contents", r"cd_contents", _B + r"shengchan" + _E,
    ]),
    re.IGNORECASE,
)
# Words that usually mean software, but also appear in firmware names (FLIR's "...Themis-VMS_Eng_NP_Stream3..."),
# so they only count when the name has no firmware markers
WEAK_SOFTWARE_NAMES = re.compile(
    "|".join(_B + word + _E for word in [r"pss", r"vms", r"cms", r"client", r"player", r"sdk", r"mac", r"drivers?"]),
    re.IGNORECASE,
)
FIRMWARE_NAME_MARKERS = re.compile(
    r"stream\d|_(np|pn|n|p)_|" + _B + r"(ipc|nvr|xvr|hcvr|dvr|sd|ptz|vto|vth|itc|tpc|evs|ivss)[-_0-9x]",
    re.IGNORECASE,
)
DOCUMENT_NAMES = re.compile(r"manual|user.?s.?guide|release.?notes?|datasheet|quick.?start", re.IGNORECASE)

# Zip members that mark a firmware package
FIRMWARE_MEMBERS = re.compile(r"(\.bin|\.img|\.dav|\.pak|\.sw|\.mfi|\.iav)$|(^|/)(hwid|install|install\.lua|"
                              r"check\.img|update\.img|update\.zip)$", re.IGNORECASE)
SOFTWARE_MEMBERS = re.compile(r"\.(exe|msi|apk|dmg|pkg|deb|rpm|jar|dll|appimage)$|\.app/|/contents/archive\.bom$|"
                              r"(^|/)install(_driver)?\.sh$|\.tar\.gz$|"
                              # PXE / network boot images (production tooling, not device firmware)
                              r"(^|/)(ldlinux\.c32|pxelinux\.0|isolinux\.bin)$", re.IGNORECASE)
DOCUMENT_MEMBERS = re.compile(r"\.(pdf|docx?|xlsx?|pptx?|txt|rtf|html?|chm)$", re.IGNORECASE)


def classify_file_type(file_name, url=None):
    """"firmware", "software" (PC tools, apps, drivers, OS images) or "document" (manuals, release notes), from the
    file name and source path. Used before downloading, to skip what isn't firmware."""
    name = file_name.lower()
    extension = os.path.splitext(name)[1]
    if extension in DOCUMENT_EXTENSIONS:
        return "document"
    if extension in SOFTWARE_EXTENSIONS:
        return "software"
    if url and "/software/" in url.lower():
        return "software"
    if DOCUMENT_NAMES.search(file_name) and extension not in (".bin", ".img", ".dav"):
        return "document"
    if SOFTWARE_NAMES.search(file_name):
        return "software"
    if WEAK_SOFTWARE_NAMES.search(file_name) and not FIRMWARE_NAME_MARKERS.search(file_name):
        return "software"
    return "firmware"


def classify_file_content(path):
    """"firmware", "software" or "document" when the downloaded file's content is conclusive, otherwise None.

    Catches what names miss: Windows executables, PDFs and Office files, and zips holding only installers, OS
    images or documents. Zips that bundle a tool (e.g. webplugin.exe) next to a firmware stay firmware."""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        return None
    if head[:2] == b"MZ":
        return "software"
    if head[:4] == b"%PDF":
        return "document"
    if head[:2] not in (b"PK", b"DH"):
        return None

    try:
        with zipfile.ZipFile(path) as archive:
            members = [member.filename for member in archive.infolist() if not member.is_dir()]
    except (zipfile.BadZipFile, OSError, NotImplementedError, RuntimeError, ValueError):
        return None
    if not members:
        return None

    # Office documents are zips too
    if any(m == "[Content_Types].xml" or m.startswith(("word/", "xl/", "ppt/")) for m in members):
        return "document"
    if any(FIRMWARE_MEMBERS.search(m) for m in members):
        return "firmware"
    if any(SOFTWARE_MEMBERS.search(m) for m in members):
        return "software"
    if all(DOCUMENT_MEMBERS.search(m) for m in members):
        return "document"
    return None


def classify_file(path, file_name, url=None):
    """The file's type, from its content where that's conclusive and its name otherwise."""
    return classify_file_content(path) or classify_file_type(file_name, url)
