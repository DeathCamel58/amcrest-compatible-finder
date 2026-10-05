import time

from brands import DahuaChina, GlobalDS, Lupus


# Global DS

def test_globalds_models_from_folders():
    path = ["VTO - Kapuegységek", "VTO3211_P1_2_4 (Ha régi VT-s eszközöd van, kövesd a sorrendet!!!)"]
    assert GlobalDS.get_models(path) == ["VTO3211_P1_2_4"]
    assert GlobalDS.get_models(["VTH5422HB;VTH5422HW"]) == ["VTH5422HB", "VTH5422HW"]
    assert GlobalDS.get_models(["XVR5-ös széria", "NVR - ARC3000H (IoT)", "magyarositas"]) == ["XVR5"]


def test_globalds_firmware_filter():
    assert GlobalDS.is_firmware("2_VTO3211D_Eng_P_SIP_V4.300.0000000.4.R.20190306.bin")
    assert GlobalDS.is_firmware("NVR608-XX-4KS2.zip")
    assert not GlobalDS.is_firmware("romfs-x.squashfs.img")
    assert not GlobalDS.is_firmware("General_SmartPSS_V2.003.0000004.0.R.20210427.zip")
    assert not GlobalDS.is_firmware("Leírás.pdf")


# Dahua China

def item(**extra):
    return {"id": "201", "rootParentCategoryId": 18, "categoryName": "球机",
            "attachmentName": "DH_SD-Eos_Chn_PN_Stream3_V2.812.0000017.1.R.240704.bin",
            "publicDownloadUrl": "https://supportfile.dahuatech.com/x.bin?auth_key=1791242446-0-0-abc",
            "content": "此版本适用于以下型号：DH-SD-6A8230UB-HNI,DH-SD-6A9230UB-HNI", "updateTime": "2024-08-09",
            "fileMd5": "DAFF0DB367F8DAFF0DB367F8DAFF0DB3", **extra}


def test_china_item_filter():
    assert DahuaChina.is_firmware_item(item())
    assert not DahuaChina.is_firmware_item(item(rootParentCategoryId=17))
    assert not DahuaChina.is_firmware_item(item(attachmentName="General_DSS7000_Chn_Basic_Bugfix_V3.11.008.R.20210830.zip"))
    assert not DahuaChina.is_firmware_item(item(attachmentName="DH_HCVR4x08-V7产品配套插件.7z"))
    assert not DahuaChina.is_firmware_item(item(attachmentName="update.zip"))


def test_china_firmware_fields():
    firmware = DahuaChina.to_firmware(item())
    assert firmware["camera_name"] == ["DH-SD-6A8230UB-HNI", "DH-SD-6A9230UB-HNI"]
    assert firmware["firmware_version"] == "V2.812.0000017.1.R"
    assert firmware["release_date"] == "2024-08-09"
    assert firmware["md5"] == "daff0db367f8daff0db367f8daff0db3"
    assert "Chinese-language" in firmware["firmware_notes"]
    # A malformed posting date falls back to the build date in the name
    assert DahuaChina.to_firmware(item(updateTime="2024/8/9"))["release_date"] == "2024-07-04"


def test_china_expired_link_is_refreshed(monkeypatch):
    now = time.time()
    old = f"https://supportfile.dahuatech.com/x.bin?auth_key={int(now) + 10}-0-0-abc"
    new = f"https://supportfile.dahuatech.com/x.bin?auth_key={int(now) + 14400}-0-0-def"
    monkeypatch.setattr(DahuaChina, "item_ids", {old: "201"})
    monkeypatch.setattr(DahuaChina, "get_items", lambda: [item(publicDownloadUrl=new)])
    assert DahuaChina.link_expiry(old) == int(now) + 10
    assert DahuaChina.fresh_url(old) == new
    # A link with time left is used as it is, without asking the API
    monkeypatch.setattr(DahuaChina, "get_items", lambda: (_ for _ in ()).throw(AssertionError("refreshed")))
    assert DahuaChina.fresh_url(new) == new


# Lupus

PAGE = """
<div class="lpsAccordion--title">LUPUS - LE281 PoE</div>
<a href="https://www.lupus-electronics.de/shop/documents/Handbuch_LE 281_V1.1.zip">x</a>
<a href="https://www.lupus-electronics.de/shop/documents/Software_Windows_LupusIPFinder_v1.0.13.zip">x</a>
<a href="https://www.lupus-electronics.de/shop/documents/smartvision_win.zip">x</a>
<a href="https://www.lupus-electronics.de/shop/documents/Firmware_LE281_V2.800.1005002.3.T.200629.bin">x</a>
<a href="https://www.lupus-electronics.de/shop/documents/le2xx_v3_GerEng_PN_Stream3_V3.120.1005000.2.R.241218.bin">x</a>
<div class="lpsAccordion--title">LUPUS - LE232</div>
<a href="https://www.lupus-electronics.de/shop/documents/le2xx_v3_GerEng_PN_Stream3_V3.120.1005000.2.R.241218.bin">x</a>
<div class="lpsAccordion--title">LUPUS - LE820</div>
<a href="https://www.lupus-electronics.de/shop/documents/LE 820.zip">x</a>
<a href="https://www.lupus-electronics.de/shop/documents/XT_Smarthome_V1.0.zip">x</a>
"""


def test_lupus_page():
    links = Lupus.parse_page(PAGE)
    base = "https://www.lupus-electronics.de/shop/documents/"
    assert links == {
        base + "Firmware_LE281_V2.800.1005002.3.T.200629.bin": ["LE281 PoE"],
        base + "le2xx_v3_GerEng_PN_Stream3_V3.120.1005000.2.R.241218.bin": ["LE281 PoE", "LE232"],
        base + "LE 820.zip": ["LE820"],
    }
