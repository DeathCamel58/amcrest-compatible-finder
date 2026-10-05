from util import wanted as w

HAVE = {"file_hashes": {"sha256": "a" * 64}}


def test_name_matches_ignore_extension_case_and_copy_prefix():
    data = {}
    w.add(data, name="DH_XVR5x04-S2_EngSpnFrn_NP_V3.218.0000002.1.R.171229.bin", source="dataset")
    w.add(data, name="General_VTOXXX_Eng_P_16M_V3.120.0000.0.R.20180613.zip", source="forum")
    cameras = {"1dh_xvr5x04-s2_engspnfrn_np_v3.218.0000002.1.r.171229.zip": dict(HAVE)}
    found, missing = w.refresh(data, cameras, today="2026-10-05")
    assert (found, missing) == (1, 1)
    entry = data[w.entry_key(name="DH_XVR5x04-S2_EngSpnFrn_NP_V3.218.0000002.1.R.171229.bin")]
    assert entry["found"] == {"file": "1dh_xvr5x04-s2_engspnfrn_np_v3.218.0000002.1.r.171229.zip", "date": "2026-10-05"}


def test_version_with_models():
    data = {}
    w.add(data, version="V2.800.0000000.25.R", models=["IPC-HX5XXX-Volt"], source="ipcamtalk")
    other = {"firmware_version": "V2.800.0000000.25.R", "camera_name": ["IPC-HX2XXX"], **HAVE}
    assert w.refresh(data, {"other.bin": other}) == (0, 1)
    volt = {"firmware_version": "V2.800.0000000.25.R", "camera_name": [], **HAVE}
    assert w.refresh(data, {"DH_IPC-HX5XXX-Volt_MultiLang_V2.800.0000000.25.R.230425.bin": volt}) == (1, 0)


def test_listed_but_not_downloaded_is_still_wanted():
    data = {}
    w.add(data, name="KX-A.bin", source="KBVision listing")
    assert w.refresh(data, {"KX-A.bin": {"downloadable": False}}) == (0, 1)


def test_references_are_merged():
    data = {}
    key = w.add(data, name="a.bin", source="dataset", url="https://x")
    assert w.add(data, name="A.zip", source="forum") == key
    w.add(data, name="a.bin", source="dataset", url="https://x")
    assert [r["source"] for r in data[key]["references"]] == ["dataset", "forum"]


def test_double_extensions_build_and_download_suffixes():
    assert w.name_key("1DH_X_V2.R.20180920.63540 (1).bin.zip") == w.name_key("DH_X_V2.R.20180920.bin")
    assert w.name_key("X.bin.zip") == w.name_key("X")
    # A version's own last number isn't a build suffix
    assert w.name_key("General_A_V4.300.0000000.1.R.190305.bin") == "general_a_v4.300.0000000.1.r.190305"


def test_found_by_the_firmware_inside_a_zip():
    data = {}
    w.add(data, name="DH_IPC-Inner_V2.800.0000000.1.R.200101.bin", source="dataset")
    cameras = {"Vendor_Wrapper_Release.zip": dict(HAVE)}
    assert w.refresh(data, cameras) == (0, 1)
    inner = {"DH_IPC-Inner_V2.800.0000000.1.R.200101.bin": "Vendor_Wrapper_Release.zip"}
    assert w.refresh(data, cameras, inner_names=inner) == (1, 0)


def test_rekey_merges_entries_that_now_match():
    old = {"name:a.20180920.63540": {"name": "a.20180920.63540.zip", "version": None, "models": [],
                                     "references": [{"source": "x"}], "found": None},
           "name:a.20180920": {"name": "a.20180920.bin", "version": None, "models": [],
                               "references": [{"source": "y"}], "found": None}}
    merged = w.rekey(old)
    assert list(merged) == ["name:a.20180920"]
    assert [r["source"] for r in merged["name:a.20180920"]["references"]] == ["x", "y"]
