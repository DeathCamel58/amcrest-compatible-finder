from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup

from util import http
from util.oem_helpers import FIRMWARE_EXTENSIONS, crawl_index, is_dahua_firmware_name, make_firmware

name = "Optiview"
vendor = "Optiview"

# The main site is behind Cloudflare, but the legacy IIS support server is still public. Its firmware is spread over
# several directory listings, and some files are only linked from the support home page
support_site = "http://support.optiviewusa.com/"
listing_roots = [
    "http://support.optiviewusa.com/firmware/",
    "http://support.optiviewusa.com/IPC/",
    "http://support.optiviewusa.com/NVR/",
    "http://support.optiviewusa.com/DVR/",
    "http://support.optiviewusa.com/Thermal_Kit/",
    "http://support.optiviewusa.com/VRSeries/",
]
# Pages (not listings) that link straight to firmware files
link_pages = [support_site, "http://support.optiviewusa.com/firmware/"]


def is_firmware_url(url):
    return is_dahua_firmware_name(unquote(url.split("/")[-1]))


def get_listed_urls():
    urls = []
    for root in listing_roots:
        urls += [url for url, _ in crawl_index(root) if is_firmware_url(url)]
    return urls


def get_linked_urls():
    """Firmware links on the support pages. Some point at files that no longer exist, so each is checked."""
    urls = []
    for page_url in link_pages:
        try:
            page = http.get(page_url)
        except Exception as err:
            print(f"\tOptiview: failed to read {page_url}: {err!r}")
            continue
        if page.status_code != 200:
            continue
        for link in BeautifulSoup(page.content, "html.parser").find_all("a", href=True):
            url = urljoin(page_url, link["href"])
            if url.lower().endswith(FIRMWARE_EXTENSIONS) and is_firmware_url(url):
                urls.append(url)

    available = []
    for url in dict.fromkeys(urls):
        try:
            response = http.get(url, stream=True)
            response.close()
        except Exception:
            continue
        if response.status_code == 200:
            available.append(url)
    return available


def get_firmwares():
    firmwares = []
    # IIS paths aren't case-sensitive, so the same file can show up as /DVR/... and /dvr/...
    seen = set()
    for url in get_listed_urls() + get_linked_urls():
        if url.lower() in seen:
            continue
        seen.add(url.lower())

        file_name = unquote(url.split("/")[-1])
        # Optiview prefixes the Dahua file name with their own model, e.g. IP4MIAB-36_IPC-HX5X3X-Rhea_...
        firmwares.append(make_firmware(file_name.split("_")[0], url))

    return firmwares
