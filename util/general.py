def get_href_if_exists(item):
    href = None

    links = item.find_all("a")
    if len(links) > 0:
        href = links[0]["href"]

    return href


def normalize_firmware_url(url):
    # Vendors sometimes list placeholders like "N/A" or raw S3 paths instead of download links
    if not url:
        return None

    url = url.strip()
    if url.startswith("s3://"):
        bucket, _, key = url[len("s3://"):].partition("/")
        # A "+" in a raw S3 key is literal, but S3 reads "+" in an https path as a space
        return f"https://{bucket}.s3.amazonaws.com/{key.replace('+', '%2B')}"
    if not url.startswith(("http://", "https://")):
        return None

    return url


def merge_unique_text(values, ignored=("", "N/A")):
    """Collapse whitespace, drop placeholders and de-duplicate, treating values that only differ in whitespace or
    case as the same (older scrapes ran "<br>"-separated text together). The variant with the most spaces wins."""
    merged = {}
    for value in values:
        if not isinstance(value, str):
            continue
        value = " ".join(value.split())
        if value in ignored:
            continue
        key = "".join(value.split()).casefold()
        if key not in merged or value.count(" ") > merged[key].count(" "):
            merged[key] = value
    return list(merged.values())
