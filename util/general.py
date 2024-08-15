def get_href_if_exists(item):
    href = None

    links = item.find_all("a")
    if len(links) > 0:
        href = links[0]["href"]

    return href
