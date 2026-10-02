import threading
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Some vendor sites (dahuawiki.com especially) are slow and drop connections at random.
# (connect, read) timeout in seconds; the read timeout is the max wait between bytes
TIMEOUT = (60, 300)

RETRY = Retry(
    total=5,
    connect=5,
    read=5,
    status=5,
    status_forcelist=[429, 500, 502, 503, 504],
    backoff_factor=2,
    respect_retry_after_header=True,
    raise_on_status=False,
)

# requests.Session isn't guaranteed thread safe, so give each thread its own
_local = threading.local()


def get_session():
    if not hasattr(_local, "session"):
        session = requests.Session()
        adapter = HTTPAdapter(max_retries=RETRY)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        _local.session = session

    return _local.session


# Cloudflare clearance per host, captured from the stealth browser: {host: {"cookies": {...}, "user_agent": "..."}}
# cf_clearance is tied to the browser's User-Agent, so both get reused for plain requests to that host
_clearances = {}
_clearance_lock = threading.Lock()


def get(url, **kwargs):
    kwargs.setdefault("timeout", TIMEOUT)

    host = urlparse(url).hostname
    response = _get_with_clearance(url, host, **kwargs)

    # The clearance may have expired (or never existed for a protected file host), so refresh it once
    if response.status_code == 403 and response.headers.get("cf-mitigated") == "challenge":
        response.close()
        if _refresh_clearance(host):
            response = _get_with_clearance(url, host, **kwargs)

    return response


def _get_with_clearance(url, host, **kwargs):
    clearance = _clearances.get(host)
    if clearance:
        kwargs["cookies"] = {**clearance["cookies"], **kwargs.get("cookies", {})}
        kwargs["headers"] = {"User-Agent": clearance["user_agent"], **kwargs.get("headers", {})}

    return get_session().get(url, **kwargs)


def _refresh_clearance(host):
    stale = _clearances.get(host)
    with _clearance_lock:
        # Another thread may have refreshed it while we waited
        if _clearances.get(host) is not stale:
            return True

        print(f"\tRefreshing Cloudflare clearance for {host}")
        return get_protected_html(f"https://{host}/") is not None


def _store_clearance(url, page):
    cookies = {cookie["name"]: cookie["value"] for cookie in page.cookies}
    headers = {key.lower(): value for key, value in (page.request_headers or {}).items()}
    if "cf_clearance" in cookies and "user-agent" in headers:
        _clearances[urlparse(url).hostname] = {"cookies": cookies, "user_agent": headers["user-agent"]}


def get_protected_html(url, attempts=3):
    """Fetch a page behind Cloudflare using a stealth browser. Returns the HTML, or None on failure."""
    # Imported lazily since it pulls in a whole browser stack
    from scrapling.fetchers import StealthyFetcher

    for attempt in range(1, attempts + 1):
        try:
            page = StealthyFetcher.fetch(url, headless=True, solve_cloudflare=True, network_idle=True)
            if page.status == 200:
                _store_clearance(url, page)
                return page.html_content
            print(f"\tGot HTTP {page.status} from {url} (attempt {attempt}/{attempts})")
            if page.status == 404:
                break
        except Exception as err:
            print(f"\tFailed to fetch {url} (attempt {attempt}/{attempts}): {err}")

    return None
