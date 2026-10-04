import json
import os

from util import analysis_store as store

ROWS = [{"name": "boot", "start": "0x0", "end": "0x40000", "size": 262144, "image": "u-boot.bin.img",
         "image_size": 1000, "upgraded": True},
        {"name": "config", "start": "0x40000", "end": "0x80000", "size": 262144, "upgraded": False}]


def result(rows=ROWS, firmware=None):
    raw = ["IPC-A:01:02", "IPC-B:01:02"]
    return {
        "status": "ok", "hardware_ids": ["IPC-A", "IPC-B"], "hardware": {"models": ["IPC-A", "IPC-B"]},
        "extractor_version": 5, "package_format": "dh",
        "hardware_sources": [
            {"source": "hwid", "member": "hwid", "firmware": firmware, "ids": ["IPC-A", "IPC-B"], "raw": raw,
             "counted": True},
            {"source": "check_img", "member": "check.img", "firmware": firmware, "ids": ["IPC-A", "IPC-B"],
             "raw": list(raw), "counted": True},
        ],
        "packages": [{"firmware": firmware, "soc": {"name": "Hi3516CV500", "vendor": "HiSilicon"},
                      "partitions": [dict(row) for row in rows]}],
    }


def test_split_and_join_round_trip():
    full = result()
    index, detail, layouts = store.split_result(full, "c" * 64)
    assert index["detail"] == "data/firmware/cc/cccccccccccccccc.json"
    assert index["socs"] == [{"name": "Hi3516CV500", "vendor": "HiSilicon"}]
    assert "hardware_sources" not in index
    # The check.img list is the same as the hwid file's, so it's stored once
    assert "raw" not in detail["hardware_sources"][1] and detail["hardware_sources"][1]["raw_same_as"] == 0
    store.write_detail(detail, layouts)
    joined = store.join_result(index)
    assert joined["hardware_sources"] == full["hardware_sources"]
    assert joined["packages"] == full["packages"]
    assert {k: v for k, v in joined.items() if k not in ("hardware_sources", "packages")} == \
        {k: v for k, v in full.items() if k not in ("hardware_sources", "packages")}


def test_firmwares_with_the_same_layout_share_one_file():
    other = [dict(row, image_size=2000, built="2024-01-01") for row in ROWS]
    for sha, rows in (("a" * 64, ROWS), ("b" * 64, other)):
        index, detail, layouts = store.split_result(result(rows), sha)
        store.write_detail(detail, layouts)
    assert len(store.stored_files(store.LAYOUT_DIR)) == 1
    assert len(store.stored_files(store.DETAIL_DIR)) == 2
    layout = next(iter(store.stored_files(store.LAYOUT_DIR)))
    assert json.load(open(layout))["partitions"] == [{k: r[k] for k in store.LAYOUT_FIELDS if k in r} for r in ROWS]


def test_results_without_detail_have_no_detail_file():
    index, detail, layouts = store.split_result({"status": "not_dahua", "hardware_ids": []}, "d" * 64)
    assert detail is None and "detail" not in index
    index, detail, _ = store.split_result(result(), None)  # no hash known
    assert detail is None


def test_remove_unreferenced():
    index_a, detail_a, layouts_a = store.split_result(result(), "a" * 64)
    index_b, detail_b, layouts_b = store.split_result(result([dict(ROWS[0])]), "b" * 64)
    store.write_detail(detail_a, layouts_a)
    store.write_detail(detail_b, layouts_b)
    # b's file was replaced by a complete copy with different content: nothing refers to b's detail anymore
    assert store.remove_unreferenced({"a.bin": index_a, "a-copy.bin": dict(index_a, status="duplicate")}) == 2
    assert store.stored_files(store.DETAIL_DIR) == {index_a["detail"]}
    assert os.path.exists(index_a["detail"])


def test_same_member_in_two_inner_firmwares_with_the_same_name():
    source = {"source": "install", "member": "Install", "firmware": "a.bin", "ids": ["X"], "raw": [["X", "1.00"]],
              "counted": True}
    other = dict(source, raw=[["Y", "1.00"]], ids=["Y"])
    sources = [source, dict(source), other, dict(other)]
    assert store.expand_sources(store.dedupe_sources(sources)) == sources


def test_analysed_result_without_detail_keeps_empty_lists():
    full = {"status": "no_ids", "hardware_ids": [], "package_format": None, "hardware_sources": [], "packages": []}
    index, detail, _ = store.split_result(full, "e" * 64)
    assert detail is None
    assert store.join_result(index) == full
