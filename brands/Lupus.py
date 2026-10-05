import html
import re
from urllib.parse import unquote

from util import http
from util.oem_helpers import make_firmware

name = "Lupus Electronics"
vendor = "Lupus Electronics"
# A German Dahua OEM (LUPUSNET HD LE2xx cameras, LE8xx XVRs, LE9xx NVRs). Its firmware versions carry Dahua's OEM
# code 1005 (e.g. V2.800.1005002.4.R). One downloads page lists every product's documents and firmware
downloads_page = "https://www.lupus-electronics.de/de/service/downloads"

# Each product is an accordion entry: a title ("LUPUS - LE281 PoE") followed by its links
PRODUCT = re.compile(r'<div class="lpsAccordion--title">(?P<title>.*?)</div>(?P<body>.*?)(?=<div class="lpsAccordion--title">|\Z)',
                     re.DOTALL)
LINK = re.compile(r'href="(?P<url>https?://[^"]*/shop/documents/[^"]+\.(?:bin|zip))"', re.IGNORECASE)
# Documents and PC/Mac software that share the folder, and Lupus's alarm system gear (XT, HPGW gateways, relays)
NOT_FIRMWARE = re.compile(r"^(Software_|Handbuch|Kurzanleitung|Datenblatt|Konformit)|smartvision|webplugin|^XT_|^HPGW"
                          r"|Relais", re.IGNORECASE)
# The recorders' firmware is zipped without a version in the name ("LE 820.zip", wrapping one Dahua .bin)
VERSIONLESS_FIRMWARE = re.compile(r"^(Firmware_)?LE ?\d{3}[^/]*\.zip$", re.IGNORECASE)


def product_model(title):
    """"LUPUS - LE281 PoE" -> "LE281 PoE"."""
    title = re.sub(r"<[^>]+>", "", html.unescape(title)).strip()
    return re.sub(r"^LUPUS\s*-\s*", "", title, flags=re.IGNORECASE).strip()


def is_firmware(file_name):
    if NOT_FIRMWARE.search(file_name):
        return False
    return bool(re.search(r"_V\d+\.\d{3}\.", file_name)) or bool(VERSIONLESS_FIRMWARE.match(file_name))


def parse_page(page):
    """{download URL: [product models]} for every firmware link, in page order."""
    links = {}
    for product in PRODUCT.finditer(page):
        model = product_model(product["title"])
        for link in LINK.finditer(product["body"]):
            url = html.unescape(link["url"])
            if not is_firmware(unquote(url.rsplit("/", 1)[-1])):
                continue
            models = links.setdefault(url, [])
            if model and model not in models:
                models.append(model)
    return links


def get_firmwares():
    response = http.get(downloads_page)
    response.raise_for_status()
    firmwares = []
    for url, models in parse_page(response.text).items():
        file_name = unquote(url.rsplit("/", 1)[-1])
        notes = "Beta firmware" if re.search(r"beta|\.T\.\d{6}", file_name, re.IGNORECASE) else None
        firmwares.append(make_firmware(models, url, notes=notes))
    return firmwares
