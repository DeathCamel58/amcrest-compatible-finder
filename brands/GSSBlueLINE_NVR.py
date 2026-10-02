from util import gss

name = "GSS - Blue|LINE NVR"
vendor = "GSS"

firmware_site = "https://gogss.com/firmware-category/network-recorders-blueline/"


def get_firmwares():
    return gss.get_firmwares(firmware_site)
