import json

import pytest

from util import analysis_store as store
from util.hwid_lines import decode_line, with_lines

LEXUS = "IPC-HFW1430SN-Lexus:01:02:02:23:21:00:01:00:00:00:00:2D0:00:00:00:00:00:01:00:00:200"


def test_21_field_line():
    line = decode_line(LEXUS)
    assert line == {
        "model": "IPC-HFW1430SN-Lexus", "raw": LEXUS, "layout": 1, "fieldCount": 21,
        "decoded": {
            "category": {"code": "02", "name": "IP camera"},
            "formFactor": {"code": "02", "name": "Bullet"},
            "sensor": {"code": "23", "name": "OV4689"},
            "soc": {"code": "21", "name": "Ambarella S3L (S3LM)"},
            "videoChannels": 1, "audioIn": False, "audioOut": False, "microSD": False, "cpuMhz": [720],
            "wifi": False, "videoOut": {"code": "00", "name": "None"}, "ramMiB": 512,
        },
    }


def test_22_field_line():
    raw = "ARA46-N-W2-868:01:04:04:00:76:00:00:00:00:00:00:3E8:00:03:00:00:08:00:00:00:80:FF"
    decoded = decode_line(raw)["decoded"]
    assert decode_line(raw)["fieldCount"] == 22
    assert decoded["category"] == {"code": "04", "name": "Access control / intercom"}
    # The form factor is only known for IP cameras, and Wi-Fi code 03 isn't certain enough to show
    assert "formFactor" not in decoded and "wifi" not in decoded
    assert decoded["cpuMhz"] == [1000] and decoded["ramMiB"] == 128


def test_23_field_line_with_a_cpu_list():
    raw = "IPC-PFM8xx:01:02:10:CF:2D:00:04:10:01:01:04:708_4B0_4B0:00:00:00:00:00:00:00:00:1000:FF:01_02"
    line = decode_line(raw)
    assert line["fieldCount"] == 23
    assert line["decoded"]["cpuMhz"] == [1800, 1200, 1200]
    assert line["decoded"]["sensor"] == {"code": "CF", "name": "SC223A"}
    assert line["decoded"]["soc"] == {"code": "2D", "name": "Hi3559AV100"}
    assert line["decoded"]["formFactor"] == {"code": "10", "name": "Multi-sensor / panoramic"}
    assert line["decoded"]["ramMiB"] == 4096 and line["decoded"]["videoChannels"] == 4


def test_lower_case_and_long_codes():
    decoded = decode_line("IPC-X:01:02:00:f0:300:00:01:00:01:01:01:384_384:00:02:00:0c:00:00:00:00:000")["decoded"]
    assert decoded["sensor"] == {"code": "F0", "name": "SC430AI (4-lane)"}
    assert decoded["soc"] == {"code": "300", "name": "SSC325"}
    assert decoded["cpuMhz"] == [900, 900]
    assert decoded["audioIn"] is True and decoded["microSD"] is True and decoded["wifi"] is True
    assert decoded["videoOut"] == {"code": "0C", "name": "HDMI"}
    # Sub-category 00 is "not set", and RAM 0 says nothing
    assert "formFactor" not in decoded and "ramMiB" not in decoded


def test_unknown_codes_keep_their_key():
    decoded = decode_line("IPC-X:01:02:99:98:7F:00:01:00:07:00:04:1B8:00:03:00:05:00:00:00:00:80")["decoded"]
    assert decoded["sensor"] == {"code": "98", "name": None}
    assert decoded["soc"] == {"code": "7F", "name": None}
    assert decoded["formFactor"] == {"code": "99", "name": None}
    assert decoded["videoOut"] == {"code": "05", "name": None}
    # Audio input 07 (probably several inputs) and Wi-Fi 03 are guesses, so they're left out
    assert "audioIn" not in decoded and "wifi" not in decoded


def test_switch_line_isnt_decoded():
    raw = "TS-WBC5-90AC-01P:02:04:06:01:100:101:00:00:09:00:00:00:00:00:00:00:00:00:00:00:00"
    assert decode_line(raw) == {"model": "TS-WBC5-90AC-01P", "raw": raw, "layout": None, "fieldCount": 21,
                                "decoded": {}}


def test_keyboard_line_isnt_decoded():
    raw = "NKB1000:91:92:00:0a:00:00:00:04:00:00:00:00:00:00:00:08"
    assert decode_line(raw) == {"model": "NKB1000", "raw": raw, "layout": None, "fieldCount": 16, "decoded": {}}


def test_short_old_line_leaves_missing_fields_out():
    decoded = decode_line("IPC-HFW4233MK-AS-X1-S4:01:02:02:4A:1A:00:01:10:01:01:04:258:08:00:00:04:00")["decoded"]
    assert decoded["sensor"] == {"code": "4A", "name": "IMX290"}
    assert decoded["videoOut"] == {"code": "04", "name": "Analog (CVBS)"}
    assert "ramMiB" not in decoded


def test_bare_board_id():
    assert decode_line("080000000530002d") == {"model": "080000000530002d", "raw": "080000000530002d",
                                               "layout": None, "fieldCount": 0, "decoded": {}}


@pytest.mark.parametrize("raw", ["", ":", "IPC-X:01", "IPC-X:01:zz:-1:0x10: :1_0:::::___:", "IPC-X:01:02::::::",
                                 "IPC-X:01:02:02:23:21:00:01:00:00:00:00:x_2D0_-5:00:00:00:00:00:01:00:00:ram",
                                 "IPC-X:XX:02:02", "a" * 1000 + ":" * 500])
def test_garbage_doesnt_throw(raw):
    line = decode_line(raw)
    assert line["raw"] == raw
    for value in line["decoded"].values():
        assert value is not None


def test_garbage_fields_are_skipped():
    decoded = decode_line("IPC-X:01:zz:02:-1:0x10:00:1_0:00:00:00:00:x_2D0_-5:00:00:00:00:00:01:00:00:ram")["decoded"]
    assert decoded == {"audioIn": False, "audioOut": False, "microSD": False, "cpuMhz": [720], "wifi": False,
                       "videoOut": {"code": "00", "name": "None"}}


def test_non_text_entries_are_skipped():
    sources = [{"source": "hwid", "raw": [["IPC-X", "1.00"], None, LEXUS]}]
    assert [line["raw"] for line in with_lines(sources)[0]["lines"]] == [LEXUS]


def test_raw_same_as_is_resolved_and_install_is_skipped():
    sources = [
        {"source": "hwid", "member": "hwid", "raw": [LEXUS]},
        {"source": "install", "member": "Install", "raw": [["IPC-HFW1430SN-Lexus", "1.00"]]},
        {"source": "check_img", "member": "check.img", "raw_same_as": 0},
        {"source": "folder", "member": "x_check.img", "raw_same_as": 7},  # points nowhere: no lines, no error
    ]
    result = with_lines(sources)
    assert "lines" not in result[1] and "lines" not in result[3]
    assert result[2]["lines"] == result[0]["lines"] == [decode_line(LEXUS)]
    assert "raw" not in result[2] and result[2]["raw_same_as"] == 0
    # Decoding again (e.g. after a table change) replaces the lines rather than adding to them
    assert with_lines(result) == result


def test_detail_files_get_lines_and_schema_version():
    full = {"status": "ok", "hardware_ids": ["IPC-HFW1430SN-Lexus"], "hardware_sources": [
        {"source": "hwid", "member": "hwid", "firmware": None, "ids": ["IPC-HFW1430SN-Lexus"], "raw": [LEXUS],
         "counted": True},
        {"source": "check_img", "member": "check.img", "firmware": None, "ids": ["IPC-HFW1430SN-Lexus"],
         "raw": [LEXUS], "counted": True}], "packages": []}
    index, detail, layouts = store.split_result(full, "c" * 64)
    assert detail["schemaVersion"] == store.DETAIL_SCHEMA_VERSION
    assert detail["hardware_sources"][1]["raw_same_as"] == 0
    assert detail["hardware_sources"][1]["lines"] == [decode_line(LEXUS)]
    store.write_detail(detail, layouts)
    # The full result doesn't carry the derived lines, so saving it again stores the same file
    assert store.join_result(index)["hardware_sources"] == full["hardware_sources"]


def test_upgrade_details_rewrites_old_files_once():
    old = {"sha256": "a" * 64, "packages": [], "hardware_sources": [
        {"source": "hwid", "member": "hwid", "firmware": None, "ids": ["IPC-HFW1430SN-Lexus"], "raw": [LEXUS],
         "counted": True},
        {"source": "check_img", "member": "check.img", "firmware": None, "ids": ["IPC-HFW1430SN-Lexus"],
         "raw_same_as": 0, "counted": True}]}
    store.write_detail(old, {})
    assert store.upgrade_details() == (1, 2)
    with open(store.detail_path("a" * 64)) as f:
        upgraded = json.load(f)
    assert upgraded["schemaVersion"] == store.DETAIL_SCHEMA_VERSION
    assert upgraded["hardware_sources"][1]["lines"] == [decode_line(LEXUS)]
    assert store.upgrade_details() == (0, 2)
