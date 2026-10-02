import re
from urllib.parse import unquote

from util.oem_helpers import crawl_index, is_dahua_firmware_name, make_firmware

name = "SecurityTronix"
vendor = "SecurityTronix"

# Their legacy HD-CVI recorders were Dahua; the current line (Guarding Vision) is Hikvision and isn't listed here
firmware_site = "http://support.securitytronix.co/firmware/"

# Software rather than firmware
SKIP = re.compile(r"configtool|smart-?pss|sadp|ipcsearch|search tool|evo cameras", re.IGNORECASE)


def get_models(file_name):
    # e.g. ST-HD-CVR8CH-V2_HCVR5108H-V2_V3.200.0004.6.R.20151123.bin -> [ST-HD-CVR8CH-V2, HCVR5108H-V2]
    stem = re.split(r"_V\d+\.\d", file_name)[0]
    return [model for model in stem.split("_") if model]


def get_firmwares():
    firmwares = []
    for url, _ in crawl_index(firmware_site):
        file_name = unquote(url.split("/")[-1])
        # The Dahua check also drops the non-Dahua NR51P / TVR-AR / MBDHVR firmware also hosted here
        if SKIP.search(file_name) or not is_dahua_firmware_name(file_name):
            continue
        firmwares.append(make_firmware(get_models(file_name), url, file_name=file_name))
    return firmwares
