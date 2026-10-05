import pytest
from requests import HTTPError

from brands import (BitsEcuador, IPCamTalkDropbox, IPPhoneForum, IndiaFix, MediaFireFolder, Santronic, Sveistrup,
                    TVCMexico, Winco)
from util import mediafire


class FakeResponse:
    def __init__(self, text="", json_data=None, status_code=200):
        self.text = text
        self._json = json_data
        self.status_code = status_code

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise HTTPError(str(self.status_code))


# util/mediafire.py

def test_mediafire_file_links_decode_names_and_dedupe():
    html = ('<a href="http://www.mediafire.com/file/0hpgaz0scfhpxba/General_VTO2000A-2_V3.120.1026000.0.R.20180211.zip">x</a>'
            '<a href="https://www.mediafire.com/file/0hpgaz0scfhpxba/General_VTO2000A-2_V3.120.1026000.0.R.20180211.zip/file">'
            '<a href="https://www.mediafire.com/file/91u99yb191z8oe8/DH_IPC-HX5%25284%2529XXX_V2.420.0000.11.R.20150907.bin/file">')
    links = mediafire.file_links(html)
    assert [(key, name) for key, name, _ in links] == [
        ("0hpgaz0scfhpxba", "General_VTO2000A-2_V3.120.1026000.0.R.20180211.zip"),
        ("91u99yb191z8oe8", "DH_IPC-HX5(4)XXX_V2.420.0000.11.R.20150907.bin"),
    ]


def test_mediafire_folder_listing(monkeypatch):
    data = {"response": {"folder_content": {"more_chunks": "no", "files": [
        {"quickkey": "abc", "filename": "DH_IPC-HX5(4)XXX_Eng_P_Stream3_IVS_V2.420.0000.11.R.20150907.bin",
         "size": "34571156", "hash": "9" * 64},
        {"quickkey": "def", "filename": "readme.pdf", "size": "10", "hash": "nothex"},
    ]}}}
    monkeypatch.setattr(mediafire.http, "get", lambda *a, **k: FakeResponse(json_data=data))
    files = mediafire.list_folder("ya1odj5eeubzq")
    assert files[0]["sha256"] == "9" * 64 and files[0]["size"] == 34571156
    assert files[1]["sha256"] is None
    firmwares = MediaFireFolder.to_firmwares(files)
    assert len(firmwares) == 1
    assert firmwares[0]["firmware_latest_file_name"] == files[0]["name"]
    assert firmwares[0]["sha256"] == "9" * 64


def test_mediafire_direct_link(monkeypatch):
    page = '<a aria-label="Download file" href="https://download1323.mediafire.com/abc/key/file.bin" id="downloadButton">'
    monkeypatch.setattr(mediafire.http, "get", lambda *a, **k: FakeResponse(text=page))
    assert mediafire.direct_link("https://www.mediafire.com/file/key/file") == \
        "https://download1323.mediafire.com/abc/key/file.bin"
    monkeypatch.setattr(mediafire.http, "get", lambda *a, **k: FakeResponse(text="<html>removed</html>"))
    with pytest.raises(HTTPError):
        mediafire.direct_link("https://www.mediafire.com/file/key/file")


def test_ip_phone_forum_keeps_firmware_links_only():
    html = ('<a href="https://www.mediafire.com/file/k1/General_VTO3211D_Eng_P_32M_V1.000.001200.0.R.20180509.zip/file">'
            '<a href="https://www.mediafire.com/file/k2/SmartPSS_Setup.exe/file">'
            '<a href="https://example.com/other">')
    assert IPPhoneForum.parse_thread(html) == [
        ("General_VTO3211D_Eng_P_32M_V1.000.001200.0.R.20180509.zip",
         "https://www.mediafire.com/file/k1/General_VTO3211D_Eng_P_32M_V1.000.001200.0.R.20180509.zip/file")]


# brands/IndiaFix.py

def test_indiafix_rows():
    page = """<table>
      <tr><td><p>DHI-NVR1104HS-S3/H</p></td><td><a href="https://drive.google.com/file/d/ID1/view?usp=share_link">Download</a></td></tr>
      <tr><td>DHI-NVR1108HS-S3/H</td><td><a href="https://drive.google.com/file/d/ID1/view">Download</a></td></tr>
      <tr><td>DHI-XVR5108HS-I3</td><td><a href="https://drive.google.com/file/d/ID-2_x/view">Download</a></td></tr>
    </table>"""
    assert IndiaFix.parse_post(page) == [("ID1", "DHI-NVR1104HS-S3/H"), ("ID-2_x", "DHI-XVR5108HS-I3")]


def test_indiafix_uses_cached_titles(monkeypatch, tmp_path):
    monkeypatch.setattr(IndiaFix, "TITLE_CACHE", str(tmp_path / "titles.json"))
    page = '<tr><td>DHI-XVR5108HS-I3</td><td><a href="https://drive.google.com/file/d/ID1/view">Download</a></td></tr>'
    monkeypatch.setattr(IndiaFix.http, "get", lambda url, **k: FakeResponse(
        text=page if "indiafix" in url else
        "<title>IndiaDahua_XVR5x08-I3_MultiLang_V4.001.001E000.0.R.220101.bin - Google Drive</title>"))
    monkeypatch.setattr(IndiaFix.time, "sleep", lambda s: None)
    firmwares = IndiaFix.get_firmwares()
    assert firmwares[0]["firmware_latest_file_name"] == "IndiaDahua_XVR5x08-I3_MultiLang_V4.001.001E000.0.R.220101.bin"
    assert firmwares[0]["camera_name"] == ["DHI-XVR5108HS-I3"]
    assert firmwares[0]["firmware_latest"] == "https://drive.usercontent.google.com/download?id=ID1&export=download"
    # Second run: the Drive page isn't loaded again
    monkeypatch.setattr(IndiaFix.http, "get", lambda url, **k: FakeResponse(text=page) if "indiafix" in url else 1 / 0)
    assert len(IndiaFix.get_firmwares()) == 1


# brands/Sveistrup.py, BitsEcuador.py, Santronic.py

def test_sveistrup_list_items():
    page = """<ul>
      <li>Nvr4xxx-EI -&nbsp;<a href="https://www.sveistrup.dk/filer/Teknik/Dahua/fw/NVR/NVR4xxx-EI/DH_NVR4x-EI_MultiLang_V4.005.0000001.1.R.250224.bin">V4.005.0000001.1.R - 24.02.2025</a></li>
      <li><a href="/filer/Teknik/Dahua/fw/IPC/DH_IPC-HX2X3X-Rhea_MultiLang_PN_Stream2_V2.800.0000013.0.R.191202.bin">DH_IPC-HX2X3X-Rhea</a></li>
      <li><a href="/kontakt">Kontakt</a></li>
    </ul>"""
    found = Sveistrup.parse_page(page, "https://www.sveistrup.dk/ipc")
    assert found[0] == ("https://www.sveistrup.dk/filer/Teknik/Dahua/fw/NVR/NVR4xxx-EI/"
                        "DH_NVR4x-EI_MultiLang_V4.005.0000001.1.R.250224.bin", "Nvr4xxx-EI", "2025-02-24")
    assert found[1][0].startswith("https://www.sveistrup.dk/filer/") and found[1][1] is None
    assert len(found) == 2


def test_bits_skips_xiongmai_and_software(monkeypatch):
    page = """<li>DVR21xx <a href="https://bits.com.ec/bits/wp-content/uploads/2017/09/DH_DVR21xx_SpnEng_NP_V3.200.0001.28.R.20170803.bin">DVR21xx</a></li>
      <a href="https://bits.com.ec/bits/wp-content/uploads/2015/04/SimpGeneral_General_AHB7004T_V4.02.R11.bin">x</a>
      <a href="https://bits.com.ec/bits/wp-content/uploads/2015/03/General_PSS_Eng_IS_V4.06.9.R.130402.zip">PSS</a>"""
    monkeypatch.setattr(BitsEcuador.http, "get", lambda *a, **k: FakeResponse(text=page))
    firmwares = BitsEcuador.get_firmwares()
    assert [f["firmware_latest"].rsplit("/", 1)[-1] for f in firmwares] == ["DH_DVR21xx_SpnEng_NP_V3.200.0001.28.R.20170803.bin"]
    assert firmwares[0]["camera_name"] == ["DVR21xx"]


def test_santronic_table_rows(monkeypatch):
    page = """<table>
      <tr><th>Firmware NVR</th><th>Version Neu</th><th>Datum</th><th>Download</th></tr>
      <tr><td>NVR4XXX-P-EI Serie</td><td>V4.1.2.R</td><td>21.06.2023</td>
          <td><a href="dlc/dahua/firmware/nvr/DH_NVR4x-EI_MultiLang_V4.001.0000000.2.R.230621.zip">dl</a></td></tr>
      <tr><td>NVR4XXX-HS-EI Serie</td><td>V4.1.2.R</td><td>21.06.2023</td>
          <td><a href="dlc/dahua/firmware/nvr/DH_NVR4x-EI_MultiLang_V4.001.0000000.2.R.230621.zip">dl</a></td></tr>
      <tr><td>ACTi</td><td></td><td></td><td><a href="dlc/acti/firmware/A1.bin">dl</a></td></tr>
    </table>"""
    monkeypatch.setattr(Santronic.http, "get", lambda *a, **k: FakeResponse(text=page))
    firmwares = Santronic.get_firmwares()
    assert len(firmwares) == 1
    assert firmwares[0]["firmware_latest"] == \
        "http://service.santronic.ch/dlc/dahua/firmware/nvr/DH_NVR4x-EI_MultiLang_V4.001.0000000.2.R.230621.zip"
    assert firmwares[0]["camera_name"] == ["NVR4XXX-P-EI Serie", "NVR4XXX-HS-EI Serie"]
    assert firmwares[0]["release_date"] == "2023-06-21"


# brands/Winco.py

def test_winco_names_and_urls():
    wire = Winco.to_wire("/product/Dahua/Firmware/BF3221/主程序")
    assert Winco.readable(wire) == "/product/Dahua/Firmware/BF3221/主程序"
    entries = [("/product/Dahua/Firmware", "Customer_NVR5XXX-4KS2_DH_MultiLang_ZD_V4.001.10BE001.0.R.200427.bin"),
               (wire, "DH_TPC-BF3221-7121_Eng_PN_V2.631.0000000.22.T.200313.bin"),
               ("/product/Dahua/Firmware", "1080875_General_NetSDK_Eng_Win32Win64_IS_V3.051.112I000.0.R.200413.7z")]
    firmwares = Winco.to_firmwares(entries)
    assert len(firmwares) == 2
    assert firmwares[0]["camera_name"] == []
    assert firmwares[1]["camera_name"] == ["BF3221"]
    assert firmwares[1]["firmware_latest"] == ("ftp://ftp.winco.com.hk/product/Dahua/Firmware/BF3221/"
                                               "%E4%B8%BB%E7%A8%8B%E5%BA%8F/DH_TPC-BF3221-7121_Eng_PN_V2.631.0000000.22.T.200313.bin")


# brands/IPCamTalkDropbox.py

def test_ipcamtalk_dropbox_filters():
    files = [{"path": "", "file_name": "Config Tools 1072a.zip", "size": 1, "url": "https://dropbox/a?dl=1"},
             {"path": "HCVR", "file_name": "General_HCVR7x16-S3_Eng_NP_V3.218.0000001.2.R.170808.bin", "size": 15441387,
              "url": "https://dropbox/b?dl=1"}]
    firmwares = IPCamTalkDropbox.to_firmwares(files)
    assert len(firmwares) == 1
    assert firmwares[0]["camera_name"] == ["HCVR"] and firmwares[0]["firmware_size"] == 15441387


# brands/TVCMexico.py

def test_tvc_parse_and_quote():
    page = """<table><tr><td>XVR4104CNS2</td><td>2017.12.29</td>
      <td><a href="http://soporte.tvc.mx/Ingenieria/DAHUA/FIRMWARE/XVR S2/XVR4104CS2/DH_XVR5x04-S2_EngSpnFrn_NP_V3.218.0000002.1.R.171229.zip"></a></td></tr>
      <tr><td>DSS</td><td><a href="https://soporte.tvc.mx/Ingenieria/DAHUA/SOFTWARE/DSS.rar"></a></td></tr></table>"""
    assert TVCMexico.parse_page(page) == [
        ("http://soporte.tvc.mx/Ingenieria/DAHUA/FIRMWARE/XVR%20S2/XVR4104CS2/"
         "DH_XVR5x04-S2_EngSpnFrn_NP_V3.218.0000002.1.R.171229.zip", "XVR4104CNS2")]


def test_tvc_dead_server_fails_fast(monkeypatch):
    monkeypatch.setattr(TVCMexico, "server_up", [False])
    monkeypatch.setattr(TVCMexico.http, "get", lambda *a, **k: 1 / 0)  # never contacted
    with pytest.raises(HTTPError):
        TVCMexico.download_file("http://soporte.tvc.mx/x.bin", "x.part")
