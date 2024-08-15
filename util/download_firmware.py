import requests
import os
from requests import HTTPError


def download_firmware(url):
    file_name = f'firmware/{url.split("/")[-1].split("?")[0]}'
    print(f"Downloading URL: {url}")

    try:
        if not os.path.exists(file_name):
            with requests.get(url, stream=True) as r:
                r.raise_for_status()
                with open(file_name, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            return file_name
    except HTTPError as http_err:
        print(f'\tHTTP error occurred: {http_err}')
    except Exception as err:
        print(f'\tAn error occurred: {err}')

    return None
