from util import gss

name = "GSS - Blue|LINE DVR"
vendor = "GSS"

firmware_site = "https://gogss.com/firmware-category/analog-recorders-blueline/"


def get_firmwares():
    return gss.get_firmwares(firmware_site)
