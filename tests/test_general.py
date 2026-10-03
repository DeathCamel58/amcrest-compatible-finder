import pytest

from util.general import merge_unique_text, normalize_firmware_url


def test_merge_unique_text_whitespace_case_and_placeholders():
    result = merge_unique_text(["IPC-HDW2431T-AS-S2", "  Full   Color\nfirmware ", "fullcolor firmware", "N/A", "",
                                None, 5, "ipc-hdw2431t-as-s2"])
    assert result == ["IPC-HDW2431T-AS-S2", "Full Color firmware"]


def test_merge_unique_text_prefers_variant_with_most_spaces():
    assert merge_unique_text(["SupportsH.265", "Supports H.265"]) == ["Supports H.265"]
    assert merge_unique_text(["Supports H.265", "SupportsH.265"]) == ["Supports H.265"]


def test_merge_unique_text_custom_ignored():
    assert merge_unique_text(["-", "a"], ignored=("-",)) == ["a"]


@pytest.mark.parametrize("url, expected", [
    ("s3://bucket-name/path/to/FW+V2.800+(1).bin", "https://bucket-name.s3.amazonaws.com/path/to/FW%2BV2.800%2B(1).bin"),
    ("N/A", None),
    ("", None),
    (None, None),
    ("ftp://example.com/a.bin", None),
    ("  https://example.com/a+b.bin  ", "https://example.com/a+b.bin"),
    ("http://example.com/a.bin", "http://example.com/a.bin"),
])
def test_normalize_firmware_url(url, expected):
    assert normalize_firmware_url(url) == expected
