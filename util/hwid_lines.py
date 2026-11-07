"""Decoding the hardware ID lines of a firmware's hwid file and check.img ("MODEL:f1:f2:...:f21[:f22[:f23]]").

Each line is one hardware build the firmware installs on; the installer compares the device's stored ID with them.
The field names are Dahua's own (gethwid's help) and the parsing follows Dahua's parser (HWID_initFromBoot in
pdc.ko). Only fields read from Dahua's code, checked on hardware or consistent across the data are decoded; the
guesses (fields 6, 8, 13, 15, 17, 18, 20, 22, 23) stay in the raw line. See docs/FIRMWARE_DATA.md.
"""

import re

# Sources whose raw entries are hardware ID lines (install's are [id, version] pairs)
LINE_SOURCES = ("hwid", "check_img", "folder")

# Field 1 (HWID_VERSION): only this layout is decoded. Switches use 02, with different meanings (port counts), and
# the NKB1000 keyboard's line is a one-off (91)
CAMERA_LAYOUT = 0x01

# Field 2 (CATEGORY)
CATEGORIES = {
    0x01: "Traffic camera (ITC)",
    0x02: "IP camera",
    0x03: "PTZ / speed dome",  # also CA-HZ zoom modules
    0x04: "Access control / intercom",  # ASI, VTO, ARC
    0x06: "Switch",
    0x08: "Thermal camera (TPC)",
    0x0B: "Wireless bridge (PFR)",
}
IP_CAMERA = 0x02

# Field 3 (SUB_CATEGORY): the form factor, only known for IP cameras. 00 is "not set" (newer firmware)
FORM_FACTORS = {
    0x01: "Box",
    0x02: "Bullet",
    0x03: "Wi-Fi bullet / dome",
    0x04: "Dome (mostly)",  # also some bullets
    0x05: "Dome / turret",
    0x07: "Consumer cube",
    0x08: "Consumer pan-tilt",
    0x0B: "Fisheye",
    0x0C: "Pinhole",
    0x0F: "Consumer C series",
    0x10: "Multi-sensor / panoramic",
    0x13: "Newer value line",
}

# Field 4 (FVIDEO_CHIP): the image sensor the board was built with (the sensor driver may correct it at boot). The
# SC codes are Dahua's own sensor constants (SYS_AD_*); the rest come from the Ambarella and HiSilicon sensor drivers'
# tables, each checked against the resolution of the models using it. 23 is the one code the drivers disagree on
# (they map OV4689 to 22), but it's an OV4689 on hardware. Left out: AD9928 / DH9931 / RJ52V (53, 68, 69, 7B, 7D,
# 82, 83), which aren't image sensors, and the codes only guessed from tuning folder names (B4, 89, 75, B3)
SENSORS = {
    0x12: "AR0331",
    0x13: "IMX136",
    0x15: "IMX122",
    0x16: "AR0130",
    0x17: "AR0130",
    0x18: "IMX104",
    0x19: "AR0330",
    0x1A: "IMX104",
    0x1B: "IMX122",
    0x1C: "IMX172",
    0x1D: "OV9712",
    0x1E: "IMX185",
    0x1F: "AR0330",
    0x20: "MT9P006",
    0x21: "IMX178",
    0x22: "OV4689",
    0x23: "OV4689",
    0x24: "PO3100",
    0x25: "IMX238",
    0x26: "AR0331 (parallel)",
    0x28: "AR0140",
    0x29: "IMX123",
    0x2A: "OV10633",
    0x2B: "PS1210M",
    0x2C: "IMX124",
    0x2E: "AR0140",
    0x2F: "PS4100M",
    0x32: "MN34220",
    0x33: "IMX322",
    0x34: "IMX178",
    0x35: "IMX225",
    0x36: "AR0230",
    0x37: "IMX225",
    0x38: "OV9750",
    0x39: "JX-H22",
    0x3E: "OV5658",
    0x42: "IMX185",
    0x49: "OV9732",
    0x4A: "IMX290",
    0x4C: "AR0237",
    0x4E: "IMX185 (FPGA)",
    0x4F: "IMX322",
    0x50: "IMX274",
    0x51: "IMX290 (parallel)",
    0x57: "IMX290 (FPGA)",
    0x59: "IMX299",
    0x5F: "IMX326",
    0x60: "OV2735",
    0x62: "B5N + IMX290",
    0x63: "OS05A10",
    0x64: "OS08",
    0x65: "OV2735",
    0x66: "OV9750 (MIPI)",
    0x6A: "IMX307",
    0x6B: "AR0239",
    0x6C: "B5N + OS05A10",
    0x6D: "B5N + IMX123",
    0x6E: "IMX385",
    0x71: "IMX290",
    0x72: "OS08",
    0x73: "OV4689",
    0x74: "IMX334",
    0x76: "IMX327",
    0x77: "SC2238",
    0x78: "IMX335",
    0x79: "IMX327",
    0x86: "OS04",
    0x9B: "SC3235",
    0xA8: "SC2239",
    0xAC: "SC2239 (MIPI)",
    0xB0: "SC2339 (DVP)",
    0xB1: "SC2339 (MIPI)",
    0xBF: "SC401AI",
    0xC7: "SC3335",
    0xC8: "SC530AI",
    0xCD: "SC2239P",
    0xCF: "SC223A",
    0xD7: "SC430AI",
    0xDE: "SC830AI",
    0xE4: "SC2336",
    0xE6: "SC4336",
    0xF0: "SC430AI (4-lane)",
    0x102: "SC223AP",
    0x109: "SC430AI (4-lane SLVS)",
    0x111: "SC2336P",
    0x113: "SC835HAI",
    0x12A: "SC533HAI",
    0x136: "SC43JHAI",
    0xFFFF: "MN34420PL",
}

# Field 5 (DSP_CHIP): from the SoC each code goes with in the data (20/21/25/29 from Dahua's scripts). A package's
# own soc (boot loader / kernel) is more reliable. 40 and 4A are left out: too few detections, and a Dahua script
# comment hints at the Hi3516DV300 family
SOCS = {
    0x0E: "Hi3518",
    0x13: "Hi3518",
    0x15: "Ambarella S2L",
    0x17: "Hi3516A",
    0x1A: "Hi3516A",
    0x18: "Ambarella S3L",
    0x19: "Ambarella S3L",
    0x20: "Ambarella S3L (S3LM)",
    0x21: "Ambarella S3L (S3LM)",
    0x25: "Ambarella S3L (S3LM)",
    0x29: "Ambarella S3L (S3LM)",
    0x1D: "Hi3518EV200",
    0x1E: "Hi3518EV200",
    0x1F: "Hi3519",
    0x23: "Hi3519V101",
    0x32: "Hi3519V101",
    0x28: "Hi3516CV300",
    0x2F: "Hi3516CV300",
    0x2D: "Hi3559AV100",
    0x35: "Hi3516CV500",
    0x36: "Hi3516DV300 or Hi3516CV500",
    0x51: "Hi3516CV500 or Hi3516AV300",
    0x37: "Hi3516EV200",
    0x38: "Hi3516EV300",
    0x39: "SSC325",
    0x3B: "SSC325",
    0x3C: "SSC325",
    0x300: "SSC325",
    0x48: "SSC335",
    0x4F: "SSC335",
    0x4E: "SSC335DE",
    0x50: "SSC335DE",
    0x52: "SS928V100",
    0x6C: "SS928V100",
    0x53: "AX630A",
    0x55: "GK7205V200",
    0x5B: "NT98560",
    0x62: "Infinity6C",
    0x66: "Infinity6C",
    0x6D: "Infinity6C",
    0x6E: "Infinity6C",
    0x69: "Hi3519DV500",
    0x6A: "Hi3519DV500",
    0x83: "SSC385",
    0x68: "SigmaStar Infinity6C/SSC027A",
    0x73: "Axera AX620E family",
    0x5E: "Rockchip",
}

# Field 16 (VD_INTERFACE)
VIDEO_OUTPUTS = {
    0x00: "None",
    0x03: "HDMI",
    0x04: "Analog (CVBS)",
    0x06: "Built-in display",  # intercoms, access readers
    0x09: "SDI",
    0x0C: "HDMI",
    0x0E: "Built-in display",
}

# Field 14 (WIRELESS_INTERFACE): 03 is some other wireless
WIFI = {0x00: False, 0x02: True}
# Field 9 (AUDIO_IN_CHANNEL): 03 / 07 are probably 2 / 3 inputs as a bitmask, which isn't certain enough to show
AUDIO_IN = {0x00: False, 0x01: True}

HEX = re.compile(r"[0-9A-Fa-f]+")


def hex_value(text):
    """A field's value (base 16, any case), or None when it's absent or isn't hex."""
    text = text.strip() if isinstance(text, str) else ""
    return int(text, 16) if HEX.fullmatch(text) else None


def coded(value, table):
    """{"code", "name"} for a table field; name is None for a code the table doesn't know yet."""
    return {"code": f"{value:02X}", "name": table.get(value)}


def decode_fields(fields):
    """The trustworthy fields of a camera-layout line (fields[0] is field 1), skipping absent or unreadable ones."""
    def field(number):
        return hex_value(fields[number - 1]) if number <= len(fields) else None

    decoded = {}
    category = field(2)
    if category is not None:
        decoded["category"] = coded(category, CATEGORIES)
    # The sub-category's values depend on the category and are only known for IP cameras
    form_factor = field(3)
    if category == IP_CAMERA and form_factor:
        decoded["formFactor"] = coded(form_factor, FORM_FACTORS)
    for number, key, table in ((4, "sensor", SENSORS), (5, "soc", SOCS)):
        value = field(number)
        if value is not None:
            decoded[key] = coded(value, table)
    if field(7) is not None:
        decoded["videoChannels"] = field(7)
    for number, key, table in ((9, "audioIn", AUDIO_IN), (14, "wifi", WIFI)):
        if field(number) in table:
            decoded[key] = table[field(number)]
    # Any audio output, and any SD slot (field 11 is 04 on most lines, 01 on some newer ones)
    for number, key in ((10, "audioOut"), (11, "microSD")):
        if field(number) is not None:
            decoded[key] = field(number) != 0
    # One clock per core or cluster, e.g. 708_4B0_4B0 on a big.LITTLE chip
    if len(fields) >= 12:
        clocks = [hex_value(item) for item in fields[11].split("_")]
        clocks = [clock for clock in clocks if clock]
        if clocks:
            decoded["cpuMhz"] = clocks
    if field(16) is not None:
        decoded["videoOut"] = coded(field(16), VIDEO_OUTPUTS)
    # Field 21 (PHY_MEMSIZE): RAM in MiB
    if field(21):
        decoded["ramMiB"] = field(21)
    return decoded


def decode_line(line):
    """One hardware ID line as {"model", "raw", "layout", "fieldCount", "decoded"}, or None if it isn't text. Lines
    that aren't the camera layout (switches, the NKB1000, bare 16-hex board IDs) get an empty decoded."""
    if not isinstance(line, str):
        return None
    model, *fields = line.split(":")
    layout = hex_value(fields[0]) if fields else None
    camera = layout == CAMERA_LAYOUT
    return {
        "model": model,
        "raw": line,
        "layout": layout if camera else None,
        "fieldCount": len(fields),
        "decoded": decode_fields(fields) if camera else {},
    }


def with_lines(sources):
    """A copy of a detail file's hardware_sources with each hwid / check.img / folder source's lines decoded into
    lines (from its own raw, or the raw of the source raw_same_as points to)."""
    result = []
    for source in sources or []:
        source = {key: value for key, value in source.items() if key != "lines"}
        if source.get("source") in LINE_SOURCES:
            raw = source.get("raw")
            same_as = source.get("raw_same_as")
            if raw is None and isinstance(same_as, int) and 0 <= same_as < len(sources):
                raw = sources[same_as].get("raw")
            if raw:
                source["lines"] = [decoded for decoded in map(decode_line, raw) if decoded is not None]
        result.append(source)
    return result
