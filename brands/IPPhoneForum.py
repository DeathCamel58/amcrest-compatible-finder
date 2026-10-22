from util import http, mediafire
from util.mirror import is_firmware_upload
from util.oem_helpers import make_firmware

name = "ip-phone-forum.de (Dahua VTO/VTH firmware collection)"
vendor = "ip-phone-forum.de"
# A German forum thread collecting Dahua intercom (VTO/VTH) firmware, every file a MediaFire link
kind = "mirror"

thread_url = "https://www.ip-phone-forum.de/threads/305121/"


def parse_thread(html):
    """MediaFire file links in the thread that look like Dahua firmware: [(file name, page url)]."""
    found = []
    for key, file_name, page_url in mediafire.file_links(html):
        if file_name and is_firmware_upload(file_name, page_url):
            found.append((file_name, page_url))
    return found


def get_firmwares():
    # The forum is behind Cloudflare
    html = http.get_protected_html(thread_url)
    if not html:
        print(f"\tCouldn't load {thread_url}")
        return []
    return [make_firmware([], page_url, file_name=file_name, notes="ip-phone-forum.de thread 305121")
            for file_name, page_url in parse_thread(html)]


def download_file(url, part_name):
    mediafire.download(url, part_name)
