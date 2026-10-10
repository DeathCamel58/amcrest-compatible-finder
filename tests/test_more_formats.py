"""Formats the reader learned in extractor version 6: Install files with comments, DHI7 headers, Android OTA packages,
rar / 7z archives, metadata files published on their own (DH Vision), and encrypted / microcontroller firmwares."""
import json
import os
import shutil
import struct
import subprocess

import pytest

from conftest import make_zip_bytes, to_dh_variant
from test_firmware_metadata import noise, squashfs, uimage
from util import firmware_metadata as fm
from util import firmware_processing
from util.firmware_platform import detect_platform
from util.firmware_processing import analyze_firmware, read_firmware


@pytest.fixture
def no_binwalk(monkeypatch):
    """Fail the test if anything falls through to binwalk."""
    def fail(path):
        raise AssertionError(f"binwalk shouldn't be needed for {path}")
    monkeypatch.setattr(firmware_processing, "extract_firmware", fail)


ITC_INSTALL = b'''{
    "Devices" : [
        ["V2.625.001W000.0.R.210112","1.00"],
        ["ITC237-PW6M-LZF","1.00"],
        ["ITC215-PW6M-LZF","2.00"]
    ],
    "Version" : { "SoftwareVersion" : "V2.625.001W000.0.R.210112" },
    "Commands" : [
        "burn kernel.img kernel",
        //"burn mcu-Xmoderm1K3.bin.img mcu-Xmoderm1K3",
        "burn download-x.squashfs.img download"
    ]
}
'''


def test_install_with_comments_and_a_version_among_the_devices(write_file):
    install = fm.parse_install(ITC_INSTALL)
    assert install['software_version'] == "V2.625.001W000.0.R.210112"
    assert install['burns'] == {"kernel.img": "kernel", "download-x.squashfs.img": "download"}
    data = to_dh_variant(make_zip_bytes({"Install": ITC_INSTALL, "kernel.img": uimage("kernel", squashfs())}))
    result = read_firmware(write_file("itc.bin", data))
    # The software version stays in raw, but isn't a device
    assert result['hardware_ids'] == ["ITC215-PW6M-LZF", "ITC237-PW6M-LZF"]
    assert result['hardware_sources'][0]['raw'][0] == ["V2.625.001W000.0.R.210112", "1.00"]


def test_comments_inside_strings_are_kept():
    assert fm.parse_json_object(b'{"url": "http://x//y", /* note */ "a": 1 // end\n}') == {"url": "http://x//y", "a": 1}


def dhi7(device="NKB5000", images=(("kernel", "/mnt/dom_bin/bzImage"), ("rootfs", "/mnt/dom_bin/svr_image.gz"))):
    def field(text, size):
        return text.encode().ljust(size, b"\0")
    header = (b"DHI7" + field("V3.212.0000001.2", 16) + field("MultiLang", 16) + field("dahua", 16)
              + field(device, 16) + field("MultiLang", 16))
    offset = 0x54 + 0x128 * len(images) + 0x40
    for name, path in images:
        header += field("V1.000.0000.0", 16) + field(name, 16) + struct.pack("<II", offset, 1000) + field(path, 0x100)
        offset += 1000
    return header + b"\0" * 0x40 + noise(2000)


def test_dhi7_header():
    header = fm.parse_dhi7_header(dhi7())
    assert header['device'] == "NKB5000" and header['vendor'] == "dahua" and header['variant'] == "MultiLang"
    assert [(i['name'], i['path']) for i in header['images']] == [("kernel", "/mnt/dom_bin/bzImage"),
                                                                ("rootfs", "/mnt/dom_bin/svr_image.gz")]
    assert header['images'][1]['offset'] == header['images'][0]['offset'] + 1000
    assert fm.parse_dhi7_header(b"DH\x03\x04" + b"\0" * 400) is None


def test_dhi7_file_and_dhi7_in_a_zip(write_file, no_binwalk):
    result = analyze_firmware(write_file("General_NKB5000_MultiLang_V3.212.0000001.2.R.210611.BIN", dhi7()))
    assert result['status'] == "ok" and result['hardware_ids'] == ["NKB5000"]
    assert result['package_format'] == "dhi7"
    [source] = result['hardware_sources']
    assert source['source'] == "dhi7" and source['counted'] and source['vendor'] == "dahua"
    [package] = result['packages']
    assert package['container_header']['device'] == "NKB5000"
    assert package['architecture'] == "x86"
    assert package['software_version'] == "V3.212.0000001.2"

    zipped = read_firmware(write_file("nvr.zip", make_zip_bytes({"NVR6XX-4KS2.BIN": dhi7("NVR6XX-4KS2")})))
    assert zipped['hardware_ids'] == ["NVR6XX-4KS2"]


ANDROID_METADATA = (b"ota-required-cache=0\nota-type=BLOCK\n"
                    b"post-build=OPM8.190505.001/V4.400.0000001.2.R.20230308:userdebug/test-keys\n"
                    b"post-timestamp=1678255654\npre-device=px30_evb\n")


def test_android_ota_nested_in_a_zip(write_file, no_binwalk):
    ota = make_zip_bytes({"META-INF/com/android/metadata": ANDROID_METADATA,
                          "META-INF/com/google/android/updater-script": b'getprop("ro.product.device") == "px30_evb"',
                          "boot.img": b"ANDROID!" + b"\0" * 100})
    result = analyze_firmware(write_file("DH_VTHXAX1_MultiLang_SIP_V4.400.0000001.2.R.20230308.zip",
                                         make_zip_bytes({"update.bin": ota})))
    assert result['status'] == "ok" and result['hardware_ids'] == ["px30_evb"]
    [source] = result['hardware_sources']
    assert source['source'] == "android_ota" and source['firmware'] == "update.bin"
    [package] = result['packages']
    assert package['android_ota'] == {"devices": ["px30_evb"], "ota_type": "BLOCK",
                                      "post_build": "OPM8.190505.001/V4.400.0000001.2.R.20230308:userdebug/test-keys"}
    assert package['soc']['name'] == "PX30" and package['soc']['vendor'] == "Rockchip"


def test_android_device_ranks_below_install():
    sources = [{'source': 'android_ota', 'member': 'META-INF/com/android/metadata', 'firmware': None, 'ids': ['b']},
               {'source': 'install', 'member': 'META-INF/com/android/Install', 'firmware': None, 'ids': ['a']}]
    fm.mark_counted_sources(sources)
    assert [s['counted'] for s in sources] == [False, True]


def dh_firmware():
    return to_dh_variant(make_zip_bytes({"hwid": b'{"hwid": ["NVR2X-S:01:02"]}',
                                         "kernel.img": uimage("kernel", squashfs())}))


@pytest.mark.skipif(not shutil.which("7z"), reason="7z isn't installed")
def test_7z_archive_is_unpacked(write_file, tmp_path, no_binwalk):
    inner = write_file("in/DH_NVR2X-S2_Eng_P_V3.205.0000000.0.R.171013.bin", dh_firmware())
    archive = str(tmp_path / "DH_NVR2X-S2.7z")
    subprocess.run(["7z", "a", "-bd", archive, inner], check=True, capture_output=True)
    result = analyze_firmware(archive)
    assert result['status'] == "ok" and result['hardware_ids'] == ["NVR2X-S"]
    assert result['package_format'] == "7z"
    assert not [p for p in os.listdir("tmp") if p.startswith("7z_")]  # unpacked copy cleaned up


def test_broken_rar_fails_cleanly(write_file):
    path = write_file("broken.rar", b"Rar!\x1a\x07\x00" + noise(500))
    if not shutil.which("unrar"):
        pytest.skip("unrar isn't installed")
    with pytest.raises(firmware_processing.ExtractionError):
        firmware_processing.extract_if_zip(path)


def test_metadata_file_published_on_its_own(write_file, no_binwalk):
    assert fm.metadata_member_name("DHVision_General_IPC-HX5(4)XXX_V2.420.0000.0.R.20140419_check.img") == "check.img"
    assert fm.metadata_member_name("Install") == "Install"
    assert fm.metadata_member_name("romfs-x.cramfs.img") is None
    check = uimage("check") + b'{"DefaultLanguage": "English", "hwid": ["IPC-HF5200:01:02:01"]}'
    result = analyze_firmware(write_file("DHVision_General_IPC-HX5(4)XXX_V2.420.0000.0.R.20140419_check.img", check))
    assert result['status'] == "ok" and result['hardware_ids'] == ["IPC-HF5200"]


@pytest.mark.parametrize("head, expected", [
    (b"SS\x01\x01" + noise(4000), ("encrypted", "SS")),
    (b"SE" + noise(4000), ("encrypted", "SE")),
    (b"SE" + b"\0" * 4000, None),  # "SE" alone isn't enough
    (b"DHPD" + noise(4000), ("encrypted", "DHPD")),
    (b"DH-UPMCU" + noise(400), ("mcu", "DH-UPMCU")),
    (struct.pack("<II", 0x20002e20, 0x08000121) + noise(400), ("mcu", "cortex_m")),
    (struct.pack("<II", 0x20002e20, 0x08000120) + noise(400), None),  # reset handler not Thumb
    (b":100000000C9434000C9446000C9446000C9446006A\n:10001000", ("mcu", "intel_hex")),
    (uimage("kernel", squashfs()), None),
    (b"DH\x03\x04" + b"\0" * 100, None),
])
def test_unreadable_format(head, expected):
    assert fm.unreadable_format(head) == expected


def test_encrypted_and_mcu_skip_unpacking(write_file, monkeypatch):
    unpacked = []
    def binwalk(path):
        unpacked.append(os.path.basename(path))
        raise firmware_processing.ExtractionError("binwalk found nothing to extract")
    monkeypatch.setattr(firmware_processing, "extract_firmware", binwalk)

    result = analyze_firmware(write_file("General_NVR4x-EI_MultiLang_V4.005.0000001.2.R.250613.bin",
                                         b"SS\x01\x01" + noise(8000)))
    assert (result['status'], result['detected_format'], result['hardware_ids']) == ("encrypted", "SS", [])

    # A zip holding an encrypted firmware is encrypted; one holding only MCU images is mcu
    zipped = analyze_firmware(write_file("NVR.zip", make_zip_bytes({"NVR.bin": b"SS\x01\x01" + noise(8000),
                                                                      "README.txt": b"Upgrade notes"})))
    assert (zipped['status'], zipped['detected_format']) == ("encrypted", "SS")
    mcu = make_zip_bytes({"VTO-MCU_All.bin": struct.pack("<II", 0x200018c8, 0x080000d5) + noise(400),
                          "VTO-MCU_App.bin": b"DH-UPMCU" + noise(400)})
    result = analyze_firmware(write_file("General_VTO6531H-MCU_V1.001.0000000.0.R.210908.zip", mcu))
    assert result['status'] == "mcu" and result['detected_format'] in ("cortex_m", "DH-UPMCU")
    assert unpacked == ["README.txt"]  # only the file that isn't encrypted or MCU code


def test_container_whose_images_are_all_encrypted(write_file, monkeypatch):
    monkeypatch.setattr(firmware_processing, "extract_firmware",
                        lambda path: (_ for _ in ()).throw(firmware_processing.ExtractionError("nothing")))
    data = to_dh_variant(make_zip_bytes({
        "romfs-x.squashfs.img": uimage("romfs", noise(5000, 2), start=0x270000, end=0x3c0000),
        "user-x.squashfs.img": uimage("user", noise(5000, 3), start=0x3c0000, end=0x500000),
    }))
    result = analyze_firmware(write_file("DH_NVR5X-EI2_MultiLang_V5.001.0000000.1.R.260310.Plain.bin", data))
    assert (result['status'], result['detected_format']) == ("encrypted", "images")


def test_platform_detection(write_file):
    content = b"\x12\x34\x56\x78" + b"\0" * 60
    assert detect_platform(write_file("dh_sd-mao-themis_eng_p_stream3_ivs_v2.422.0000.3.r.20161130.zip",
                                      content)) == "dahua"
    assert detect_platform(write_file("enc.bin", b"SS\x01\x01" + noise(100))) == "dahua"
    path = write_file("Intelbras_VIP_E3220.zip", make_zip_bytes({
        "intelbras_ipc-hx32xxx_prtengspn_n_v2.212.ib00.0.r.20141114.rar": b"Rar!\x1a\x07\x00" + noise(100)}))
    assert detect_platform(path) == "dahua"
    assert detect_platform(write_file("DVR16S_V150206V150206.sw", content)) == "unknown"


def test_url_folder():
    url = ("https://web.archive.org/web/20160227161708id_/http://www.dh-vision.com:80/main/firmware/IPC/"
           "HX5%284%29XXX/General_IPC-HX5%284%29XXX_V2.420/check.img")
    other_capture = ("https://web.archive.org/web/20190419183632id_/http://dh-vision.com/main/firmware/IPC/"
                     "HX5%284%29XXX/General_IPC-HX5%284%29XXX_V2.420/kernel.img")
    assert firmware_processing.url_folder(url) == "dh-vision.com/main/firmware/IPC/HX5(4)XXX/General_IPC-HX5(4)XXX_V2.420"
    assert firmware_processing.url_folder(url) == firmware_processing.url_folder(other_capture)
    assert firmware_processing.url_folder(None) is None


def test_files_published_unpacked_inherit_their_folders_ids(write_file):
    folder = "http://dh-vision.com/main/firmware/IPC/X/General_IPC-X_V2.420"
    files = {
        "DHVision_X_check.img": uimage("check") + b'{"hwid": ["IPC-HF5200:01:02"]}',
        "DHVision_X_kernel.img": uimage("kernel", squashfs(), start=0x100000, end=0x300000),
        "DHVision_X_web-x.cramfs.img": uimage("web", squashfs(), start=0x300000, end=0x400000),
    }
    cameras = {}
    for name, data in files.items():
        write_file(f"firmware/{name}", data)
        cameras[name] = {"url": f"{folder}/{name.removeprefix('DHVision_X_')}",
                         "file_hashes": {"sha256": f"{len(cameras):02x}" * 32}}
    cameras["Other_kernel.img"] = {"url": "http://elsewhere.example/fw/kernel.img",
                                   "file_hashes": {"sha256": "ff" * 32}}
    write_file("firmware/Other_kernel.img", files["DHVision_X_kernel.img"])
    for name in cameras:
        firmware_processing.process_firmware_threaded(name, f"firmware/{name}",
                                                      sha256=cameras[name]["file_hashes"]["sha256"])
    firmware_processing.flush_results()

    assert firmware_processing.inherit_folder_results(cameras) == 2
    with open("firmware_compatible.json") as f:
        index = json.load(f)
    assert index["DHVision_X_kernel.img"]["status"] == "ok"
    assert index["DHVision_X_kernel.img"]["hardware_ids"] == ["IPC-HF5200"]
    assert index["Other_kernel.img"]["status"] != "ok"  # a different folder
    with open(index["DHVision_X_kernel.img"]["detail"]) as f:
        detail = json.load(f)
    assert detail["hardware_sources"] == [{"source": "folder", "member": "DHVision_X_check.img", "firmware": None,
                                           "ids": ["IPC-HF5200"], "counted": True}]
    assert detail["packages"]  # its own package (the kernel image) is kept
    # Running it again changes nothing
    assert firmware_processing.inherit_folder_results(cameras) == 0
