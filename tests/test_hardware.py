import pytest

from util.hardware import classify_hardware_ids, is_family_name, split_model_names


@pytest.mark.parametrize("name", ["HCVR5x04-S2", "XVR7x16-I2", "DVRxx04LE-AS", "IPC-HX3XXX", "SD6XXX",
                                  "NVR4X-4KS2/L", "NVR5X-4K", "IPC-HX5X3X", "IPC-HX1X3X", "IPC-HX3(2)XXX"])
def test_family_names(name):
    assert is_family_name(name)


@pytest.mark.parametrize("name", ["IPC-HDW8441X-3D", "ASI7213X", "CA-HZ2023XA-A", "IPC-HDBW5631E-Z-X-0735",
                                  "IPC-HDW2431T-AS-S2", "NVR5208-4KS2", "IPC-TPC124X-AI-S2", "PTZ3E10X-T180"])
def test_real_models_with_x_are_not_families(name):
    assert not is_family_name(name)


def test_classify_boards_vs_models():
    result = classify_hardware_ids([
        "HCVR5x04-S2", "IPC-HX3XXX", "NVR4X-4KS2/L", "IPC-HX5X3X",
        "IPC-HDW8441X-3D", "ASI7213X", "CA-HZ2023XA-A", "IPC-HDBW5631E-Z-X-0735",
    ])
    assert result["boards"] == sorted(["HCVR5x04-S2", "IPC-HX3XXX", "NVR4X-4KS2/L", "IPC-HX5X3X"])
    assert result["models"] == sorted(["IPC-HDW8441X-3D", "ASI7213X", "CA-HZ2023XA-A", "IPC-HDBW5631E-Z-X-0735"])
    assert result["hwids"] == []
    assert result["ignored"] == []


def test_general_prefix_is_folded_and_deduplicated():
    result = classify_hardware_ids(["General_HCVR5x08-S2", "HCVR5x08-S2", "General_IPC-HDW2431T-AS-S2"])
    assert result["boards"] == ["HCVR5x08-S2"]
    assert result["models"] == ["IPC-HDW2431T-AS-S2"]


def test_hex_hwids():
    result = classify_hardware_ids(["0800425346118427", "ABCDEF0123456789"])
    assert result["hwids"] == ["0800425346118427", "ABCDEF0123456789"]
    assert result["models"] == []


def test_vendor_tokens_and_empty_ignored():
    result = classify_hardware_ids(["DAHUA", "Group", "general", "", "IPC-HFW2431S-S2"])
    assert result["ignored"] == sorted(["DAHUA", "Group", "general", ""])
    assert result["models"] == ["IPC-HFW2431S-S2"]


def test_firmware_file_names_ignored():
    name = "DH_IPC-HX5X3X-Rhea_MultiLang_PN_Stream3_V2.800.0000000.0.R.220101.bin"
    result = classify_hardware_ids([name])
    assert result["ignored"] == [name]
    assert result["boards"] == []


def test_result_has_all_groups_and_is_sorted():
    result = classify_hardware_ids(["ZZZ1234", "AAA1234"])
    assert set(result) == {"models", "boards", "hwids", "ignored"}
    assert result["models"] == ["AAA1234", "ZZZ1234"]


# --- split_model_names -------------------------------------------------------------------------------------------

def test_firmware_like_names_dropped():
    names = ["DH_IPC-HX5X3X_MultiLang_PN_V2.800.0000000.0.R.220101.bin", "Firmware.zip", "IPC_Build_20220101",
             "IPC-HDW2431T-AS-S2"]
    models, series, notes = split_model_names(names, "whatever.bin")
    assert models == ["IPC-HDW2431T-AS-S2"]
    assert series == [] and notes == []


def test_gss_model_equal_to_file_name_kept():
    models, series, notes = split_model_names(["IPC-144M"], "IPC-144M.bin")
    assert models == ["IPC-144M"]


def test_series_names():
    models, series, notes = split_model_names(["IPC-HFW2431 Series", "IPC-HX3XXX", "NVR5208-4KS2"], "x.bin")
    assert series == ["IPC-HFW2431 Series", "IPC-HX3XXX"]
    assert models == ["NVR5208-4KS2"]


@pytest.mark.parametrize("description", ["Full Color firmware", "32 Channel Firmware (Older than 2020)",
                                         "Updated Voice File", "Special"])
def test_descriptions_become_notes(description):
    models, series, notes = split_model_names([description], "x.bin")
    assert models == [] and series == []
    assert notes == [description]


def test_model_with_bracketed_description():
    models, series, notes = split_model_names(["IPC-TPC124X-AI-S2 (S2 version Produced in Feb.2024)"], "x.bin")
    assert models == ["IPC-TPC124X-AI-S2"]
    assert notes == ["S2 version Produced in Feb.2024"]
    assert series == []


def test_model_followed_by_description():
    models, series, notes = split_model_names(["PTZ3E10X-T180 Vehicle automatic tracking firmware"], "x.bin")
    assert models == ["PTZ3E10X-T180"]
    assert notes == ["Vehicle automatic tracking firmware"]


def test_bracketed_non_description_kept_whole():
    # The bracketed part isn't a description, so the whole name stays a model
    models, series, notes = split_model_names(["IPC-HFW1230S (2.8mm)"], "x.bin")
    assert models == ["IPC-HFW1230S (2.8mm)"]
    assert notes == []


def test_split_model_names_no_repeats_after_removing_descriptions():
    from util.hardware import split_model_names
    models, series, notes = split_model_names(
        ["PTZ3E10X-T180 Vehicle automatic tracking firmware", "PTZ3E10X-T180 Ship tracking firmware",
         "ptz3e10x-t180"], "x.bin")
    assert models == ["PTZ3E10X-T180"]
    assert len(notes) == 2
