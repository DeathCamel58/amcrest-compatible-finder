from util import gss

name = "GSS - Red|LINE NVR"
vendor = "GSS"

firmware_site = "https://gogss.com/firmware-category/network-recorders/"


def get_firmwares():
    return gss.get_firmwares(firmware_site)
