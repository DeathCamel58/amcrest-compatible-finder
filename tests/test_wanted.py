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
