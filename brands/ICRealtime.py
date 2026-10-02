import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

from util import http
from util.oem_helpers import is_dahua_firmware_name, make_firmware

name = "IC Realtime"
vendor = "IC Realtime"

# The firmware page needs a dealer login, but the S3 bucket behind it is publicly listable
bucket_url = "https://icr-eb-bucket.s3.amazonaws.com/"
prefix = "storage/firmware/"

S3_NAMESPACE = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}

# e.g. EclipseX3_V2.820.00KL000.0.R.210630.bin -> EclipseX3, Elise-I_MultiLang_... -> Elise-I
MODEL = re.compile(r"^(?:Customer_|General_|DH_)?(.+?)(?:_(?:Eng|MultiLang|Multi)(?=_|\.|$).*|_V\d+\.\d.*)$")


def list_keys():
    """Every (key, size) under the firmware prefix, following S3 pagination."""
    keys = []
    token = None
    while True:
        params = {"list-type": "2", "prefix": prefix}
        if token:
            params["continuation-token"] = token
        response = http.get(bucket_url, params=params)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        for contents in root.findall("s3:Contents", S3_NAMESPACE):
            key = contents.findtext("s3:Key", namespaces=S3_NAMESPACE)
            size = contents.findtext("s3:Size", namespaces=S3_NAMESPACE)
            keys.append((key, int(size) if size and size.isdigit() else None))
        if root.findtext("s3:IsTruncated", namespaces=S3_NAMESPACE) != "true":
            return keys
        token = root.findtext("s3:NextContinuationToken", namespaces=S3_NAMESPACE)


def get_firmwares():
    firmwares = []
    for key, size in list_keys():
        file_name = key[len(prefix):]
        # Skips the folder marker, PDFs and version-less tools (ICNow, Control4, ...)
        if not file_name or "/" in file_name or not is_dahua_firmware_name(file_name):
            continue

        match = MODEL.match(file_name)
        firmware = make_firmware([match.group(1)] if match else [], bucket_url + quote(key), file_name=file_name)
        firmware["firmware_size"] = size
        firmwares.append(firmware)

    return firmwares
