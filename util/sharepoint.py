import threading
from urllib.parse import quote, urlparse

from util import http

# Walks an anonymous ("anyone with the link") SharePoint share through its REST API.
# Visiting the share link sets a FedAuth cookie; without it the API and file downloads return 401.

API_HEADERS = {"Accept": "application/json;odata=nometadata"}


class SharePointShare:
    def __init__(self, share_url, site_url):
        self.share_url = share_url
        self.site_url = site_url.rstrip("/")
        self.cookies = None
        self._lock = threading.Lock()

    def _refresh_cookies(self, stale=None):
        with self._lock:
            # Another thread may have refreshed them while we waited
            if self.cookies is not None and self.cookies is not stale:
                return
            response = http.get(self.share_url)
            response.raise_for_status()
            self.cookies = response.cookies.get_dict()
            # The FedAuth cookie can be set on a redirect, so collect cookies from the whole chain
            for previous in response.history:
                self.cookies.update(previous.cookies.get_dict())

    def get(self, url, **kwargs):
        if self.cookies is None:
            self._refresh_cookies()
        cookies = self.cookies
        response = http.get(url, cookies=cookies, **kwargs)
        # The cookie may have expired, so refresh it once
        if response.status_code == 401:
            response.close()
            self._refresh_cookies(stale=cookies)
            response = http.get(url, cookies=self.cookies, **kwargs)
        return response

    def _api(self, folder, kind, select):
        # Single quotes are escaped by doubling them in OData string literals
        escaped_folder = quote(folder.replace("'", "''"))
        url = f"{self.site_url}/_api/web/GetFolderByServerRelativeUrl('{escaped_folder}')/{kind}?$select={select}"
        response = self.get(url, headers=API_HEADERS)
        response.raise_for_status()
        return response.json().get("value", [])

    def list_files(self, folder):
        return self._api(folder, "Files", "Name,Length,TimeLastModified,ServerRelativeUrl")

    def list_folders(self, folder):
        return self._api(folder, "Folders", "Name,ServerRelativeUrl")

    def walk(self, folder):
        """Yield (folder_path, files) for folder and every folder below it."""
        yield folder, self.list_files(folder)
        for subfolder in self.list_folders(folder):
            yield from self.walk(subfolder["ServerRelativeUrl"])

    def file_url(self, server_relative_url):
        host = urlparse(self.site_url)
        return f"{host.scheme}://{host.netloc}{quote(server_relative_url)}?download=1"

    def download_file(self, url, part_name):
        with self.get(url, stream=True) as r:
            r.raise_for_status()
            with open(part_name, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
