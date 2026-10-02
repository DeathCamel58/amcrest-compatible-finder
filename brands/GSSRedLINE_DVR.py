from util import gss

name = "GSS - Red|LINE DVR"
vendor = "GSS"

firmware_site = "https://gogss.com/firmware-category/analog-recorders/"


def get_firmwares():
    return gss.get_firmwares(firmware_site)
