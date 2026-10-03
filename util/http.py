import threading
from contextlib import contextmanager
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
# Hosts whose clearance refresh produced no cf_clearance this run. Refreshing them again on every 403 would just launch
# browser after browser (one at a time, blocking refreshes for every other host), so they're not retried
_failed_refreshes = set()


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


def post(url, **kwargs):
    """Like get(), for POST requests (e.g. WordPress admin-ajax pagination behind Cloudflare). Not retried by the
    session, since POSTs aren't idempotent."""
    kwargs.setdefault("timeout", TIMEOUT)
    host = urlparse(url).hostname
    clearance = _clearances.get(host)
    if clearance:
        kwargs["cookies"] = {**clearance["cookies"], **kwargs.get("cookies", {})}
        kwargs["headers"] = {"User-Agent": clearance["user_agent"], **kwargs.get("headers", {})}
    return get_session().post(url, **kwargs)


def _get_with_clearance(url, host, **kwargs):
    clearance = _clearances.get(host)
    if clearance:
        kwargs["cookies"] = {**clearance["cookies"], **kwargs.get("cookies", {})}
        kwargs["headers"] = {"User-Agent": clearance["user_agent"], **kwargs.get("headers", {})}

    return get_session().get(url, **kwargs)


def _refresh_clearance(host):
    """Get a fresh Cloudflare clearance for host. Returns whether there's a clearance to retry with."""
    if host in _failed_refreshes:
        return False
    stale = _clearances.get(host)
    with _clearance_lock:
        # Another thread may have refreshed (or failed to) while we waited
        if host in _failed_refreshes:
            return False
        if _clearances.get(host) is not stale:
            return True

        print(f"\tRefreshing Cloudflare clearance for {host}")
        get_protected_html(f"https://{host}/")
        if _clearances.get(host) is stale or host not in _clearances:
            print(f"\tNo Cloudflare clearance for {host}; not refreshing it again this run")
            _failed_refreshes.add(host)
            return False
        return True


class SlowDownloadError(Exception):
    """A download stayed below the minimum rate for a whole window."""


def iter_content_with_min_rate(response, chunk_size=1024 * 1024, min_bytes_per_sec=50_000, window=300,
                               clock=None):
    """Like response.iter_content(), but raises SlowDownloadError when the average rate over the last `window` seconds
    drops below min_bytes_per_sec. The read timeout only fires when no bytes arrive at all, so a server trickling a
    few bytes at a time (one 443 MB download took 5 hours) would otherwise hold a worker indefinitely."""
    import time
    from collections import deque

    clock = clock or time.monotonic
    started = clock()
    samples = deque([(started, 0)])
    received = 0
    for chunk in response.iter_content(chunk_size=chunk_size):
        received += len(chunk)
        now = clock()
        samples.append((now, received))
        while len(samples) > 1 and samples[1][0] <= now - window:
            samples.popleft()
        oldest_time, oldest_received = samples[0]
        if now - started >= window and now - oldest_time >= window:
            rate = (received - oldest_received) / (now - oldest_time)
            if rate < min_bytes_per_sec:
                raise SlowDownloadError(f"{rate / 1024:.1f} KB/s over the last {window} s "
                                        f"(minimum {min_bytes_per_sec / 1024:.0f} KB/s)")
        yield chunk


def _store_clearance(url, page):
    cookies = {cookie["name"]: cookie["value"] for cookie in page.cookies}
    headers = {key.lower(): value for key, value in (page.request_headers or {}).items()}
    if "cf_clearance" in cookies and "user-agent" in headers:
        _clearances[urlparse(url).hostname] = {"cookies": cookies, "user_agent": headers["user-agent"]}


# Vendors are listed in parallel, but each stealth browser is a whole Chromium; allow a few at once
BROWSER_SLOTS = 4
_browser_lock = threading.BoundedSemaphore(BROWSER_SLOTS)


def get_protected_html(url, attempts=3):
    """Fetch a page behind Cloudflare using a stealth browser. Returns the HTML, or None on failure."""
    with _browser_lock:
        return _get_protected_html(url, attempts)


class _ProtectedSession:
    def __init__(self, session):
        self.session = session

    def get_html(self, url, attempts=2, **fetch_kwargs):
        """Fetch one page in the shared browser. Returns the HTML, or None on failure."""
        fetch_kwargs.setdefault("solve_cloudflare", True)
        fetch_kwargs.setdefault("network_idle", True)
        for attempt in range(1, attempts + 1):
            try:
                page = self.session.fetch(url, **fetch_kwargs)
                if page.status == 200:
                    _store_clearance(url, page)
                    return page.html_content
                print(f"\tGot HTTP {page.status} from {url} (attempt {attempt}/{attempts})")
                if page.status == 404:
                    break
            except Exception as err:
                print(f"\tFailed to fetch {url} (attempt {attempt}/{attempts}): {err}")
        return None

    def download(self, file_url, destination, start_url, timeout_ms=600000):
        """Download a file that's itself behind a Cloudflare challenge, by navigating the browser to it from
        start_url (a page on the same site) and saving the resulting download. Raises on failure."""
        result = {}

        def action(page):
            try:
                with page.expect_download(timeout=timeout_ms) as info:
                    page.evaluate("url => { window.location.href = url }", file_url)
                info.value.save_as(destination)
                result["ok"] = True
            except Exception as err:
                result["error"] = err

        # Scrapling logs an error reading the page body after the navigation; the download itself is unaffected
        self.session.fetch(start_url, solve_cloudflare=True, disable_resources=True, page_action=action,
                           timeout=timeout_ms)
        if not result.get("ok"):
            raise RuntimeError(f"browser download of {file_url} failed: {result.get('error')!r}")


@contextmanager
def protected_session(solve_cloudflare=True):
    """One stealth browser kept open for many pages of a site behind Cloudflare (much faster than a browser per page).

    Holds one browser slot for the whole session. Pages fetched through it store the Cloudflare clearance, so later
    http.get calls to the same host reuse it."""
    from scrapling.fetchers import StealthySession

    with _browser_lock:
        session = StealthySession(headless=True, solve_cloudflare=solve_cloudflare)
        session.start()
        try:
            yield _ProtectedSession(session)
        finally:
            session.close()


def _get_protected_html(url, attempts):
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
