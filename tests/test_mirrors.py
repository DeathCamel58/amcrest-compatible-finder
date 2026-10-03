import json
from datetime import date, timedelta

import pytest

from brands import Eltrox
from util import oem_helpers
from util.mirror import NO_MODEL_FOLDER, get_models_from_path

ROOT = "https://mirror.example/fw/"


class FakeResponse:
    def __init__(self, status_code, body=""):
        self.status_code = status_code
        self.content = body.encode()


def index(*names):
    return "<html><body><a href='../'>../</a>" + "".join(f"<a href='{n}'>{n}</a> 01-Jan-2024 10:00 123\n"
                                                          for n in names) + "</body></html>"


class FakeHttp:
    """Serves pages from a dict; a value can be an exception to raise or an int status code."""
    def __init__(self, pages):
        self.pages = pages
        self.requests = []

    def get(self, url, **kwargs):
        self.requests.append(url)
        page = self.pages.get(url, 404)
        if isinstance(page, Exception):
            raise page
        if isinstance(page, int):
            return FakeResponse(page)
        return FakeResponse(200, page)


TREE = {
    ROOT: index("IPC/"),
    ROOT + "IPC/": index("IPC-HFW1230S/"),
    ROOT + "IPC/IPC-HFW1230S/": index("General_IPC_V2.800.0000000.0.R.220101.bin"),
}
FILE = (ROOT + "IPC/IPC-HFW1230S/General_IPC_V2.800.0000000.0.R.220101.bin", ROOT + "IPC/IPC-HFW1230S/")


@pytest.fixture
def fake_http(monkeypatch):
    def install(pages):
        fake = FakeHttp(pages)
        monkeypatch.setattr(oem_helpers.http, "get", fake.get)
        return fake
    return install


@pytest.fixture
def no_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr(oem_helpers.time, "sleep", sleeps.append)
    return sleeps


# --- folder names ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["Rejestratory/NVR5xxx-EI/Stare", "X/NVR5xxx-EI/starsze", "X/NVR5xxx-EI/nowe",
                                  "X/NVR5xxx-EI/archiwum", "X/NVR5xxx-EI/aktualne", "X/NVR5xxx-EI/Starszy-2.460-PL",
                                  "X/NVR5xxx-EI/V4.0-PL", "X/NVR5xxx-EI/5.0", "X/NVR5xxx-EI/S2"])
def test_polish_and_version_folders_are_generic(path):
    assert get_models_from_path(path) == ["NVR5xxx-EI"]


@pytest.mark.parametrize("folder", ["Mao-Rhea", "Eco-Savvy", "master", "Cauchy", "Сustomized"])
def test_no_model_folder(folder):
    assert get_models_from_path(f"IPC/HX5X3X-Rhea/{folder}", generic_folder=NO_MODEL_FOLDER) == ["HX5X3X-Rhea"]


@pytest.mark.parametrize("folder", ["IPC-XXBXX", "NVRXBXX-L", "VTOxxx", "HX5X3X-Rhea"])
def test_series_folders_are_kept(folder):
    assert get_models_from_path(f"IPC/{folder}", generic_folder=NO_MODEL_FOLDER) == [folder]


# --- Eltrox ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("folder, vendor", [("DAHUA", "Eltrox"), ("KENIK/", "Kenik"), ("easycam", "EasyCam"),
                                            ("NOTIS", "Notis"), ("ZEUS", "Zeus"), ("IRBIS", "Irbis"),
                                            ("KONIG", "Konig"), ("OTHER", "Eltrox")])
def test_eltrox_vendor(folder, vendor):
    assert Eltrox.get_vendor(folder) == vendor


def test_eltrox_generic_folder():
    assert get_models_from_path("NVR/KAMERY_2MPX", generic_folder=Eltrox.generic_folder) == []
    assert get_models_from_path("NVR/NVR4XXX-4KS2/STARE", generic_folder=Eltrox.generic_folder) == ["NVR4XXX-4KS2"]


# --- crawl_index: delay and failure cutoff -----------------------------------------------------------------------

def test_crawl_without_options_is_unchanged(fake_http, no_sleep):
    fake = fake_http(TREE)
    assert oem_helpers.crawl_index(ROOT) == [FILE]
    assert len(fake.requests) == 3
    assert no_sleep == []


def test_crawl_delay_between_requests(fake_http, no_sleep):
    fake_http(TREE)
    oem_helpers.crawl_index(ROOT, delay=1.5)
    # No wait before the first request
    assert no_sleep == [1.5, 1.5]


def test_crawl_stops_after_consecutive_failures(fake_http, no_sleep):
    pages = {ROOT: index(*[f"D{i}/" for i in range(10)])}
    pages.update({f"{ROOT}D{i}/": ConnectionError("timed out") for i in range(10)})
    fake = fake_http(pages)
    assert oem_helpers.crawl_index(ROOT, max_failures=3) == []
    assert len(fake.requests) == 1 + 3


def test_crawl_failure_count_resets_on_success(fake_http, no_sleep):
    pages = {ROOT: index("A/", "B/", "C/", "D/"), ROOT + "A/": 503, ROOT + "B/": index("x_V2.800.0000000.0.R.220101.bin"),
             ROOT + "C/": 503, ROOT + "D/": index("y_V2.800.0000000.0.R.220101.bin")}
    fake_http(pages)
    assert len(oem_helpers.crawl_index(ROOT, max_failures=2)) == 2


def test_crawl_404_is_not_a_host_failure(fake_http, no_sleep):
    pages = {ROOT: index("A/", "B/", "C/"), ROOT + "C/": index("y_V2.800.0000000.0.R.220101.bin")}
    fake_http(pages)
    assert len(oem_helpers.crawl_index(ROOT, max_failures=2)) == 1


# --- crawl_index: cache ------------------------------------------------------------------------------------------

def write_cache(path, fetched, entries):
    path.write_text(json.dumps({url: {"fetched": fetched, **listing} for url, listing in entries.items()}))


DEEP = ROOT + "IPC/IPC-HFW1230S/"
DEEP_LISTING = {"files": [[FILE[0], "01-Jan-2024 10:00 123"]], "directories": []}


def test_cache_written(fake_http, no_sleep, tmp_path):
    fake_http(TREE)
    cache_file = tmp_path / "sub" / "index.json"
    assert oem_helpers.crawl_index(ROOT, cache_file=str(cache_file)) == [FILE]
    cache = json.loads(cache_file.read_text())
    assert set(cache) == set(TREE)
    assert cache[DEEP]["fetched"] == date.today().isoformat()
    assert cache[DEEP]["files"] == [[FILE[0], "01-Jan-2024 10:00 123"]]
    assert not list(cache_file.parent.glob("*.tmp"))


def test_cache_hit_makes_no_request(fake_http, no_sleep, tmp_path):
    cache_file = tmp_path / "index.json"
    write_cache(cache_file, date.today().isoformat(), {DEEP: DEEP_LISTING})
    fake = fake_http({url: page for url, page in TREE.items() if url != DEEP})
    assert oem_helpers.crawl_index(ROOT, cache_file=str(cache_file)) == [FILE]
    # The start URL and top-level folders are always fetched; the deeper one comes from the cache
    assert fake.requests == [ROOT, ROOT + "IPC/"]


def test_top_levels_always_fetched(fake_http, no_sleep, tmp_path):
    cache_file = tmp_path / "index.json"
    write_cache(cache_file, date.today().isoformat(),
                {ROOT: {"files": [], "directories": []}, ROOT + "IPC/": {"files": [], "directories": []}})
    fake = fake_http(TREE)
    assert oem_helpers.crawl_index(ROOT, cache_file=str(cache_file)) == [FILE]
    assert fake.requests == [ROOT, ROOT + "IPC/", DEEP]


def test_stale_entry_is_refetched(fake_http, no_sleep, tmp_path):
    cache_file = tmp_path / "index.json"
    stale = (date.today() - timedelta(days=15)).isoformat()
    write_cache(cache_file, stale, {DEEP: {"files": [], "directories": []}})
    fake = fake_http(TREE)
    assert oem_helpers.crawl_index(ROOT, cache_file=str(cache_file), cache_days=14) == [FILE]
    assert DEEP in fake.requests
    assert json.loads(cache_file.read_text())[DEEP]["fetched"] == date.today().isoformat()


def test_failure_falls_back_to_cache(fake_http, no_sleep, tmp_path):
    cache_file = tmp_path / "index.json"
    stale = (date.today() - timedelta(days=100)).isoformat()
    write_cache(cache_file, stale, {DEEP: DEEP_LISTING})
    fake_http({**TREE, DEEP: ConnectionError("timed out")})
    assert oem_helpers.crawl_index(ROOT, cache_file=str(cache_file)) == [FILE]
    # The old listing is kept, not replaced
    assert json.loads(cache_file.read_text())[DEEP]["fetched"] == stale


def test_cutoff_uses_cache_for_the_rest(fake_http, no_sleep, tmp_path):
    cache_file = tmp_path / "index.json"
    stale = (date.today() - timedelta(days=100)).isoformat()
    deeper = {f"{ROOT}IPC/M{i}/": {"files": [[f"{ROOT}IPC/M{i}/f{i}_V2.800.0000000.0.R.220101.bin", None]],
                                   "directories": []} for i in range(5)}
    write_cache(cache_file, stale, deeper)
    pages = {ROOT: index("IPC/"), ROOT + "IPC/": index(*[f"M{i}/" for i in range(5)])}
    pages.update({url: ConnectionError("timed out") for url in deeper})
    fake = fake_http(pages)
    files = oem_helpers.crawl_index(ROOT, cache_file=str(cache_file), max_failures=2)
    assert len(files) == 5
    assert len(fake.requests) == 2 + 2


def test_offline_lists_only_from_cache(fake_http, no_sleep, tmp_path):
    cache_file = tmp_path / "index.json"
    write_cache(cache_file, "2020-01-01", {ROOT: {"files": [], "directories": [[ROOT + "IPC/", "IPC/"]]},
                                           ROOT + "IPC/": {"files": [], "directories": [[DEEP, "IPC-HFW1230S/"]]},
                                           DEEP: DEEP_LISTING})
    fake = fake_http(TREE)
    assert oem_helpers.crawl_index(ROOT, cache_file=str(cache_file), offline=True) == [FILE]
    assert fake.requests == []


def test_skip_directories_apply_to_cached_listings(fake_http, no_sleep, tmp_path):
    cache_file = tmp_path / "index.json"
    write_cache(cache_file, "2020-01-01", {ROOT: {"files": [], "directories": [[ROOT + "LAN/", "LAN/"]]},
                                           ROOT + "LAN/": {"files": [[ROOT + "LAN/x.bin", None]], "directories": []}})
    fake_http({})
    assert oem_helpers.crawl_index(ROOT, cache_file=str(cache_file), offline=True, skip_directories=("LAN",)) == []


def test_dahua_france_page():
    from brands import DahuaFrance
    page = """
    <div class="block"><a class="title" href="/upload/firmwares/DH_HCVR5104H_Eng_P_V3.200.0001.32.R.20171021.bin"
       target="_blank">DH_HCVR5104H_Eng_P_V3.200.0001.32.R.20171021.bin</a>
      <div class="wenben"><strong><a href="/upload/firmwares/DH_HCVR5104H_Eng_P_V3.200.0001.32.R.20171021.bin.pdf"
        target="_blank"><img src="/img/pdf.png" />&nbsp;Release note</a></strong>
        <p>Release date: 2017-10-21<br/>Checksum (md5): 5FEAE8930A4C4B91BE22F2ACE7FF983E</p></div></div>
    <div class="block"><a class="title" href="/upload/firmwares/DH_DPB18-AI-V2.003.0000000.0.R.200529.apk">x</a>
      <div class="wenben"><p>Release date: 2020-05-29</p></div></div>"""
    files = DahuaFrance.parse_page(page)
    url = "https://france.dahuatech.com/upload/firmwares/DH_HCVR5104H_Eng_P_V3.200.0001.32.R.20171021.bin"
    assert files[0] == (url, "2017-10-21", "5feae8930a4c4b91be22f2ace7ff983e", url + ".pdf")
    assert files[1][2] is None and files[1][3] is None
    assert DahuaFrance.get_model("DH_HCVR5x04-S2_Eng_P_V3.200.0004.13.R.20171021.bin") == "HCVR5x04-S2"
    assert DahuaFrance.get_model("DH_ASI72XXX_FrnEng_NP_V1.000.9999003.0.R.201126.bin") == "ASI72XXX"
