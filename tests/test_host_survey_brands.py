"""Offline tests for the brand modules changed after the host survey (no network access)."""
from types import SimpleNamespace

import pytest

from brands import ASM, Inaxsys_STORM, Optiview, Speco, Wayback, Zuum


# --- Optiview ----------------------------------------------------------------------------------------------------

def test_optiview_lists_every_root_and_page_link(monkeypatch):
    listings = {
        "http://support.optiviewusa.com/IPC/": [
            ("http://support.optiviewusa.com/IPC/IPxx_firmware/IP4MIAB-36_IPC-HX5X3X-Rhea_Eng_N_Stream3_"
             "V2.800.0000000.0.R.220101.bin", "http://support.optiviewusa.com/IPC/IPxx_firmware/"),
            ("http://support.optiviewusa.com/IPC/ConfigTool/General_ConfigTool.zip", None),
        ],
        "http://support.optiviewusa.com/firmware/": [
            # Same file under a different case: IIS isn't case-sensitive
            ("http://support.optiviewusa.com/ipc/ipxx_firmware/IP4MIAB-36_IPC-HX5X3X-Rhea_Eng_N_Stream3_"
             "V2.800.0000000.0.R.220101.bin".lower(), None),
            ("http://support.optiviewusa.com/firmware/NVR/VNVR16-4K_NVR5x-4KS2_Eng_V4.001.0000000.1.R.201231.bin",
             None),
        ],
    }
    monkeypatch.setattr(Optiview, "crawl_index", lambda url: listings.get(url, []))
    monkeypatch.setattr(Optiview, "get_linked_urls",
                        lambda: ["http://support.optiviewusa.com/Thermal_Kit/TK1_TPC-BF5X_Eng_V2.800.0000000.0.R.220101.bin"])

    firmwares = Optiview.get_firmwares()
    names = [firmware["camera_name"] for firmware in firmwares]
    assert sorted(names) == ["IP4MIAB-36", "TK1", "VNVR16-4K"]
    assert len(firmwares) == 3
    assert all(firmware["firmware_version"] for firmware in firmwares)


def test_optiview_skips_dead_page_links(monkeypatch):
    page = SimpleNamespace(status_code=200, content=b"""
        <a href="/firmware/HDVR4-T2_HC5x04-S3_Eng_PN_V3.218.0000001.2.R.170808.bin">fw</a>
        <a href="/firmware/gone_HCVR_Eng_V3.218.0000001.2.R.170808.bin">dead</a>
        <a href="/manuals/HDVR4.pdf">manual</a>
        <a href="/firmware/HDVR16-Q2.zip">no version</a>""")

    def fake_get(url, **kwargs):
        if url in Optiview.link_pages:
            return page
        return SimpleNamespace(status_code=404 if "gone_" in url else 200, close=lambda: None)

    monkeypatch.setattr(Optiview.http, "get", fake_get)
    assert Optiview.get_linked_urls() == [
        "http://support.optiviewusa.com/firmware/HDVR4-T2_HC5x04-S3_Eng_PN_V3.218.0000001.2.R.170808.bin"]


# --- ASM / XtendLan ----------------------------------------------------------------------------------------------

X = ASM.xtendlan_site


@pytest.mark.parametrize("path, expected", [
    ("DVR-470JE2/firmware/General_DVR0404LF-AS_3104H_Eng_P_V2.608.0000.7.R.20170312.zip", True),
    ("DVR-x70-J_JE_JE2_PG_PJ_PK_PM_PU/Firmware/DVR-470,870,1670PK/"
     "1.%20General_DVRx70PK_Eng_P_V2.616.0000.0.R.20130403.bin", True),
    # Language / web interface packs
    ("DVR-3270JD/CONFIG_General_CzeEng_P_ASM_WebCzeEng_ASM_N5_V2.608.0.R.111209.rar", False),
    ("NVR-1602K/Firmware/2.616/config_WebCzEng__V2.616.0.R.140516.bin", False),
    # Tools
    ("TFTP_Upgrade_Tools/General_DVR_Eng_V2.608.0000.1.R.20120215.zip", False),
    ("DVR-410AUTO/Utility/Proxy_server/General_ProxyServer_Eng_IS_V1.2.0.R.100928.rar", False),
    ("DVR-470/manual.pdf", False),
])
def test_xtendlan_filter(path, expected):
    assert ASM.is_xtendlan_firmware(X + path) is expected


@pytest.mark.parametrize("directory, models", [
    ("DVR-470JE2/firmware/TFTP", ["DVR-470JE2"]),
    ("DVR-H470PG,H870PG,H1670PG/firmware", ["DVR-H470PG", "H870PG", "H1670PG"]),
    ("DVR-x70-J_JE_JE2_PG_PJ_PK_PM_PU/Firmware/DVR-470,870,1670PK", ["DVR-470", "870", "1670PK"]),
    ("DVR-x70-J_JE_JE2_PG_PJ_PK_PM_PU/Firmware/DVR-470JE,870JE,1670JE/pouze_pro_1670JE", ["1670JE"]),
    ("NVR-1204,1208,1216DP/Firmware/2.616", ["NVR-1204", "1208", "1216DP"]),
    ("DVR-475EL", ["DVR-475EL"]),
])
def test_xtendlan_models(directory, models):
    assert ASM.get_xtendlan_models(directory) == models


def test_xtendlan_firmware_entries(monkeypatch):
    url = X + "DVR-470JE2/firmware/TFTP/General_DVR0404LF-AS_3104H_Eng_N_V2.608.0000.3.R.20121102.zip"
    monkeypatch.setattr(ASM, "crawl_index", lambda start: [
        (url, X + "DVR-470JE2/firmware/TFTP/"),
        (X + "DVR-3270JD/CONFIG_General_CzeEng_P_V2.608.0.R.111209.rar", X + "DVR-3270JD/"),
    ])
    monkeypatch.setattr(ASM, "get_mirror_firmwares", lambda *args, **kwargs: [{"camera_name": ["dahua tree"]}])

    firmwares = ASM.get_firmwares()
    assert len(firmwares) == 2
    assert "vendor" not in firmwares[0]  # the Dahua tree stays under the module's vendor
    xtendlan = firmwares[1]
    assert xtendlan["vendor"] == "XtendLan"
    assert xtendlan["source"] == "ASM (ftp.asm.cz) - XtendLan"
    assert xtendlan["camera_name"] == ["DVR-470JE2"]
    assert xtendlan["firmware_version"] == "V2.608.0000.3.R"
    assert xtendlan["firmware_notes"] == "Folder: DVR-470JE2/firmware/TFTP"


# --- Wayback -----------------------------------------------------------------------------------------------------

def source(prefix):
    entry = next(entry for entry in Wayback.SOURCES if entry[0] == prefix)
    return entry[1], entry[2], entry[3] if len(entry) > 3 else {}


@pytest.mark.parametrize("prefix, vendor", [
    ("dahuawiki.com/images/Firmware/", "Dahua"),
    ("rvigroup.ru/upload/files/", "RVI"),
    ("amcrest.com/downloads/", "Amcrest"),
    ("www.zuummedia.com/v/vspfiles/", "Zuum"),
    ("downloadstore.boschsecurity.com/FILES/", "Bosch"),
])
def test_wayback_new_sources(prefix, vendor):
    assert source(prefix)[0] == vendor


def test_wayback_rvi_dahua_names_only():
    assert source("rvigroup.ru/upload/files/")[1] is True


@pytest.mark.parametrize("path, expected", [
    ("/v/vspfiles/Software/C4 LSX4CH-4K V4.000.101U001.0.T.180918.zip", True),
    ("/v/vspfiles/Software/LSNVR8CHV2-4K_V4.001.0000000.1.R.201231.bin", True),
    ("/v/vspfiles/Software/D16TVI-A-V2_V4.1.2.6.R20180110.zip", False),
    ("/v/vspfiles/files/firmware/MAH4X4-ZUUM-V017-20131210.zip", False),
])
def test_wayback_zuum_include(path, expected):
    assert Wayback.source_matches(path, source("www.zuummedia.com/v/vspfiles/")[2]) is expected


@pytest.mark.parametrize("path, expected", [
    ("/FILES/DIVAR_AN_3000_5000_V3.200.0002.0.R.20170101.zip", True),
    ("/FILES/DIVAR_network_v3.4.0.R.20240306.bin", True),
    ("/FILES/DivarIP72xxAIO_ConfigFix_1.0.0.zip", False),
    ("/FILES/Bosch_Dewarping-1.7.4.zip", False),
    ("/FILES/BoschPluginSuite_Genetec_v2.1.0.768_pkg.zip", False),
])
def test_wayback_bosch_include(path, expected):
    assert Wayback.source_matches(path, source("downloadstore.boschsecurity.com/FILES/")[2]) is expected


# --- Zuum --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("url, expected", [
    (Zuum.known_folder, True),
    ("https://www.dropbox.com/scl/fo/i888ztms8musfusi2l33h/ALSnqx-hrqCHMwnYkXk9IhU?rlkey=p9i49mholh44cstdss7ku8rom&dl=1",
     False),
    ("https://www.dropbox.com/scl/fo/i888ztms8musfusi2l33h/ALSnqx-hrqCHMwnYkXk9IhU/Product%20Firmware?dl=0", False),
    ("https://uc123.dl.dropboxusercontent.com/zip_download_get/AbCdEf", False),
    ("https://www.dropbox.com/sh/9btmav0x8o0t6zw/AACp6FZGxtzykoxydr8YnbfTa?dl=0", False),
    ("https://www.zuummedia.com/downloads/", False),
])
def test_zuum_is_folder_link(url, expected):
    assert Zuum.is_folder_link(url) is expected


def redirects(chain):
    def fake_get(url, **kwargs):
        assert kwargs.get("allow_redirects") is False
        location = chain.get(url)
        return SimpleNamespace(status_code=302 if location else 200, headers={"Location": location} if location else {},
                               close=lambda: None)
    return fake_get


def test_zuum_resolve_follows_to_folder(monkeypatch):
    monkeypatch.setattr(Zuum.http, "get", redirects({
        Zuum.firmware_site: "https://www.dropbox.com/sh/9btmav0x8o0t6zw/AACp6FZGxtzykoxydr8YnbfTa?dl=0",
        "https://www.dropbox.com/sh/9btmav0x8o0t6zw/AACp6FZGxtzykoxydr8YnbfTa?dl=0": Zuum.known_folder,
    }))
    assert Zuum.resolve_folder_link(Zuum.firmware_site) == Zuum.known_folder


def test_zuum_resolve_stops_before_zip_download(monkeypatch):
    zip_url = "https://uc123.dl.dropboxusercontent.com/zip_download_get/AbCdEf"

    def fake_get(url, **kwargs):
        assert url != zip_url, "followed the redirect into the folder zip"
        return redirects({Zuum.firmware_site: "https://www.dropbox.com/sh/abc/def?dl=1",
                          "https://www.dropbox.com/sh/abc/def?dl=1": zip_url})(url, **kwargs)

    monkeypatch.setattr(Zuum.http, "get", fake_get)
    assert Zuum.resolve_folder_link(Zuum.firmware_site) is None


# --- Inaxsys -----------------------------------------------------------------------------------------------------

def test_inaxsys_keeps_packages_next_to_extracted_images(monkeypatch):
    files = [
        {"Name": "General_SD-Mao-Themis_Eng_N_Stream3_IVS_V2.600.0002.0.T.20171128.bin",
         "ServerRelativeUrl": "/x/a.bin", "TimeLastModified": "2018-01-01T00:00:00Z"},
        {"Name": "update.img", "ServerRelativeUrl": "/x/update.img", "TimeLastModified": "2018-01-01T00:00:00Z"},
        {"Name": "General_ConfigTool.zip", "ServerRelativeUrl": "/x/c.zip", "TimeLastModified": "2018-01-01T00:00:00Z"},
    ]
    monkeypatch.setattr(Inaxsys_STORM.share, "walk", lambda folder: [("PTZ/DO2PTZ4X", files)])
    monkeypatch.setattr(Inaxsys_STORM.share, "file_url", lambda path: "https://share" + path)

    firmwares = {}
    Inaxsys_STORM.add_firmwares(firmwares, "PTZ/DO2PTZ4X", ["DO2PTZ4X"])
    assert list(firmwares) == ["General_SD-Mao-Themis_Eng_N_Stream3_IVS_V2.600.0002.0.T.20171128.bin"]
    assert next(iter(firmwares.values()))["camera_name"] == ["DO2PTZ4X"]


# --- Speco -------------------------------------------------------------------------------------------------------

def test_speco_pages_whole_media_library(monkeypatch):
    pages = {
        1: [{"id": 1, "source_url": "https://specotech.com/wp-content/uploads/2020/05/O4P4X_V2.623.00SP004.0.R.200427.bin_.zip",
             "title": {"rendered": "O4P4X_V2.623.00SP004.0.R.200427.bin"}}],
        2: [{"id": 2, "source_url": "https://specotech.com/wp-content/uploads/2026/10/Line-List-Catalog.pdf",
             "title": {"rendered": "Catalog"}}],
        3: [{"id": 3, "source_url": "https://specotech.com/wp-content/uploads/2019/07/speco_o2p4x_v2.400.0007.0.r.1.bin_.zip",
             "title": {"rendered": "o2p4x"}},
            {"id": 1, "source_url": "https://specotech.com/wp-content/uploads/2020/05/O4P4X_V2.623.00SP004.0.R.200427.bin_.zip",
             "title": {"rendered": "O4P4X_V2.623.00SP004.0.R.200427.bin"}}],
    }
    requested = []

    def fake_get(url, params=None, **kwargs):
        assert "search" not in params
        requested.append(params["page"])
        return SimpleNamespace(status_code=200, json=lambda: pages[params["page"]], headers={"X-WP-TotalPages": "3"})

    monkeypatch.setattr(Speco.http, "get", fake_get)
    items = Speco.get_media_items()
    assert sorted(requested) == [1, 2, 3]
    assert sorted(item["id"] for item in items) == [1, 2, 3]

    firmwares = Speco.get_media_firmwares(set())
    # Only the Dahua-style name is kept
    assert [firmware["camera_name"] for firmware in firmwares] == [["O4P4X"]]
    assert firmwares[0]["firmware_version"] == "V2.623.00SP004.0.R"
    assert firmwares[0]["release_date"] == "2020-04-27"


@pytest.mark.parametrize("directory, models", [
    ("DVR-x70PG/firmware/stary", ["DVR-x70PG"]),
    ("NVR-1602K/Firmware/Onvif", ["NVR-1602K"]),
    ("XL-ICA-H662-Z4820, SC110, Z4822/Firmware", ["XL-ICA-H662-Z4820", "SC110", "Z4822"]),
])
def test_xtendlan_models_generic_folders(directory, models):
    assert ASM.get_xtendlan_models(directory) == models


def test_xtendlan_skips_tools():
    assert not ASM.is_xtendlan_firmware(
        X + "DVR-x70-J/Tools/DVR-470PG/HDD%20Download%20Tool/General_DiskCopy_Eng_TS_V1.13.0.R.120222.7z")
