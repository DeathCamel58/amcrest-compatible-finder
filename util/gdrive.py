from concurrent.futures import ThreadPoolExecutor

from bs4 import BeautifulSoup
from requests import HTTPError

from util import http

# Helpers for public ("anyone with the link") Google Drive folders


def list_folder(folder_id):
    """Return (folders, files) in a public Drive folder. Each entry is a dict with id, name and modified."""
    page = http.get(f"https://drive.google.com/embeddedfolderview?id={folder_id}")
    page.raise_for_status()
    soup = BeautifulSoup(page.content, "html.parser")

    folders = []
    files = []
    for entry in soup.select(".flip-entry"):
        link = entry.find("a")
        title = entry.select_one(".flip-entry-title")
        if link is None or title is None:
            continue

        modified = entry.select_one(".flip-entry-last-modified")
        item = {
            "name": title.get_text(strip=True),
            "modified": modified.get_text(strip=True) if modified else None,
        }

        href = link["href"]
        if "/folders/" in href:
            item["id"] = href.split("/folders/")[1].split("?")[0]
            folders.append(item)
        elif "/file/d/" in href:
            item["id"] = href.split("/file/d/")[1].split("/")[0]
            files.append(item)

    return folders, files


def walk(folder_id, should_enter=None, workers=8):
    """
    Recursively list every file under a public Drive folder, walking folders concurrently.
    should_enter(path) can skip folders, where path is the list of folder names from the root.
    Returns a list of file dicts with an added "path" (the folder names leading to the file).
    """
    results = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = [pool.submit(_list_with_path, folder_id, [])]
        while pending:
            future = pending.pop(0)
            path, folders, files = future.result()

            for file in files:
                results.append({**file, "path": path})

            for folder in folders:
                folder_path = path + [folder["name"]]
                if should_enter is None or should_enter(folder_path):
                    pending.append(pool.submit(_list_with_path, folder["id"], folder_path))

    return results


def _list_with_path(folder_id, path):
    try:
        folders, files = list_folder(folder_id)
    except Exception as err:
        print(f"\tFailed to list Drive folder {'/'.join(path) or folder_id}: {err}")
        folders, files = [], []
    return path, folders, files


def download_url(file_id):
    return f"https://drive.usercontent.google.com/download?id={file_id}&export=download"


def download(url, part_name):
    """Download a public Drive file to part_name, handling the large-file virus scan confirmation page."""
    response = _open_download(url)
    try:
        with open(part_name, "wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
    finally:
        response.close()


def _open_download(url):
    response = http.get(url, stream=True)
    response.raise_for_status()

    if not response.headers.get("Content-Type", "").startswith("text/html"):
        return response

    # Files too large for Drive to virus scan return an HTML form that has to be resubmitted to get the file
    soup = BeautifulSoup(response.content, "html.parser")
    response.close()

    form = soup.find("form", id="download-form") or soup.find("form")
    if form is None:
        # Usually a quota exceeded or access denied page; treat as a permanent failure
        title = soup.title.get_text(strip=True) if soup.title else "unknown page"
        raise HTTPError(f"Google Drive returned an HTML page instead of the file: {title}")

    params = {field["name"]: field.get("value", "") for field in form.find_all("input") if field.get("name")}
    action = form.get("action") or "https://drive.usercontent.google.com/download"

    response = http.get(action, params=params, stream=True)
    response.raise_for_status()
    if response.headers.get("Content-Type", "").startswith("text/html"):
        response.close()
        raise HTTPError("Google Drive still returned an HTML page after confirming the download")

    return response

