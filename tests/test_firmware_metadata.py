import gzip
import io
import os
import struct
import zipfile
import zlib

import pytest

from conftest import make_zip_bytes, to_dh_variant
from util import firmware_metadata as fm
from util import firmware_processing
from util.firmware_processing import needs_processing, read_firmware

BUILT = 1600000000  # 2020-09-13


def uimage(name, payload=b"", start=0, end=0, arch=2, compression=0, image_type=5, timestamp=BUILT):
    header = struct.pack(">IIIIIIIBBBB", 0x27051956, 0, timestamp, len(payload), start, end, 0, 5, arch, image_type,
                         compression) + name.encode().ljust(32, b"\0")
    return header + payload


def squashfs(size=5000):
    return b"hsqs" + os.urandom(size)


def fdt(compatible, model=None, child_compatible="arm,cortex-a7"):
    strings = b"compatible\0model\0"
    offsets = {"compatible": 0, "model": 11}

    def prop(name, value):
        data = struct.pack(">III", 3, len(value), offsets[name]) + value
        return data + b"\0" * (-len(data) % 4)

    body = struct.pack(">I", 1) + b"\0\0\0\0"
    body += prop("compatible", b"\0".join(c.encode() for c in compatible) + b"\0")
    if model:
        body += prop("model", model.encode() + b"\0")
    body += struct.pack(">I", 1) + b"cpu\0" + prop("compatible", child_compatible.encode() + b"\0")
    body += struct.pack(">II", 2, 2) + struct.pack(">I", 9)
    struct_offset = 40
    strings_offset = struct_offset + len(body)
    total = strings_offset + len(strings)
    header = struct.pack(">10I", 0xd00dfeed, total, struct_offset, strings_offset, 40, 17, 16, 0, len(strings),
                         len(body))
    return header + body + strings


def cramfs(files):
    """A minimal cramfs image holding {name: content} in its root directory."""
    entries, data_parts = [], []
    names = list(files)
    # Directory entries follow the superblock and root inode
    dir_entries_size = sum(12 + len(name.encode()) + (-len(name.encode()) % 4) for name in names)
    data_offset = 76 + dir_entries_size
    for name in names:
        content = files[name]
        blocks = [content[i:i + 4096] for i in range(0, len(content), 4096)] or [b""]
        compressed = [zlib.compress(block) for block in blocks]
        pointers, position = [], data_offset + 4 * len(blocks)
        for block in compressed:
            position += len(block)
            pointers.append(position)
        encoded = name.encode() + b"\0" * (-len(name.encode()) % 4)
        inode = struct.pack("<III", 0o100644, len(content), (len(encoded) // 4) | ((data_offset // 4) << 6))
        entries.append(inode + encoded)
        part = struct.pack(f"<{len(pointers)}I", *pointers) + b"".join(compressed)
        part += b"\0" * (-len(part) % 4)
        data_parts.append(part)
        data_offset += len(part)
    root = struct.pack("<III", 0o040755, dir_entries_size, (76 // 4) << 6)
    superblock = struct.pack("<IIII", 0x28cd3d45, data_offset, 0, 0) + b"Compressed ROMFS" + b"\0" * 32
    return superblock + root + b"".join(entries) + b"".join(data_parts)


PARTITION_TABLE = """# Version=3
#  name     cs  offset              size                mask_flags fs_flags fs_type mount_cmd backup_off
  MinBoot,  0, 0x0000000000000000, 0x0000000000040000, RW, , , , 0xffffffffffffffff
  U-Boot,   0, 0x0000000000080000, 0x0000000000040000, RW, , , , 0x0000000000040000
  hwid,     0, 0x00000000000c0000, 0x0000000000020000, RW, , , , 0xffffffffffffffff
  Kernel,   0, 0x00000000000f0000, 0x0000000000180000, RW, , , , 0xffffffffffffffff
  romfs,    0, 0x0000000000270000, 0x0000000000150000, RW, R, squashfs, , 0xffffffffffffffff
  config,   0, 0x00000000003c0000, 0x0000000000040000, RW, RW, jffs2, "mount /dev/mtdblock5 /mnt/mtd", 0xffffffffffffffff
"""

INSTALL_LUA = """
-- Flash partitions
--[[
  0xa0000000 - 0xa0040000  256K  armboot   u-boot
]]
local Installer = {};
Installer.UpgradeSecurityVersion = "V2.0";
function Installer:checkSecurityBaselineVersion()
	local defVersion = "V1.4";
	local newVersion = "V1.4";
	return true;
end
function Installer:checkHardware()
	if(board.name ~= "NVR4X-4KS2") then return false end
	if(vendor.Name ~= 'NVR4X-4KS2' and vendor.Name ~= 'DAHUA') then return false end
end
local flashPartions =
{
	boot		= { baseAddr = 0x00000000  , endAddr = 0x00300000 },	--// 3M boot
	env		    = { baseAddr = 0x00300000  , endAddr = 0x00500000 },	--// 2M env
	uImage		= { baseAddr = 0x00500000  , endAddr = 0x00f00000 },
--	old		= { baseAddr = 0x00f00000  , endAddr = 0x01000000 },
	rootfs		= { baseAddr = 0x00f00000  , endAddr = 0x04500000 },
}
function Installer:run()
	self:updatePart(flashPartions["boot"],	"u-boot.bin.img");
	self:updatePart(flashPartions["uImage"],	"uImage.img");
	self:updatePart(flashPartions["rootfs"],	"romfs-x.squashfs.img");
	--self:updatePart(flashPartions["env"],		"env.img");
end
"""


# --- pure parsers ------------------------------------------------------------------------------------------------

def test_uimage_header_holds_partition_range():
    header = fm.parse_uimage_header(uimage("kernel", b"x" * 10, start=0x1e00000, end=0x2100000, compression=1))
    assert header == {"name": "kernel", "size": 10, "start": 0x1e00000, "end": 0x2100000, "arch": "arm",
                      "type": "firmware", "compression": "gzip", "built": "2020-09-13"}
    assert fm.parse_uimage_header(b"\0" * 64) is None
    assert fm.parse_uimage_header(uimage("x", timestamp=0))["built"] is None


@pytest.mark.parametrize("data, kind", [
    (b"hsqs" + b"\0" * 100, "squashfs"),
    (gzip.compress(b"hello"), "gzip"),
    (b"\xd0\x0d\xfe\xed" + b"\0" * 100, "fdt"),
    (struct.pack("<4I", 0xea000006, 0xea000005, 0, 0), "arm_code"),
    (os.urandom(4096), None),
])
def test_payload_kind(data, kind):
    if kind is None and fm.payload_kind(data):
        pytest.skip("random bytes happened to match a magic")
    assert fm.payload_kind(data) == kind


def test_fdt_root_properties_ignore_children():
    blob = fdt(["sstar,infinity6c", "sstar,infinity"], model="INFINITY6C SSC027D-S01A")
    assert fm.fdt_root_properties(blob) == {"compatible": ["sstar,infinity6c", "sstar,infinity"],
                                            "model": "INFINITY6C SSC027D-S01A"}
    assert fm.fdt_root_properties(b"nope") == {}


@pytest.mark.parametrize("value, expected", [
    ("hi3516cv500", ("Hi3516CV500", "HiSilicon")),
    ("Hi3798CV200_20150730_001", ("Hi3798CV200", "HiSilicon")),
    ("3521boot", ("Hi3521", "HiSilicon")),
    ("ss528V100", ("SS528V100", "HiSilicon")),
    ("ss626v100_lite", ("SS626V100", "HiSilicon")),
    ("sstar,infinity6c", ("Infinity6C", "SigmaStar")),
    ("SSC335", ("SSC335", "SigmaStar")),
    ("nt98631boot", ("NT98631", "Novatek")),
    ("5x16FW98336Tboot", ("NT98336", "Novatek")),
    ("RK3588", ("RK3588", "Rockchip")),
    ("ambarella,s3l", ("S3L", "Ambarella")),
    ("novatek,na51089", (None, "Novatek")),
    ("SIGMASTAR", (None, "SigmaStar")),
    ("NVR4X-S2", None),
    ("hi3798cv2dmd", None),
])
def test_normalize_soc(value, expected):
    assert fm.normalize_soc(value) == expected


def test_choose_soc_prefers_strongest_and_stays_conservative():
    def evidence(rank, source, value):
        name, vendor = fm.normalize_soc(value)
        return {"source": source, "member": "m", "value": value, "_name": name, "_vendor": vendor, "_rank": rank}

    soc = fm.choose_soc([evidence(1, "device_tree", "novatek,na51089"), evidence(2, "bootloader", "nt98560")])
    assert (soc["name"], soc["vendor"]) == ("NT98560", "Novatek")
    assert soc["evidence"][0] == {"source": "device_tree", "member": "m", "value": "novatek,na51089"}
    # Two different chips named equally strongly: only the vendor is certain
    soc = fm.choose_soc([evidence(2, "bootloader", "hi3516cv500"), evidence(2, "bootloader", "hi3516dv300")])
    assert (soc["name"], soc["vendor"]) == (None, "HiSilicon")
    assert soc["candidates"] == ["Hi3516CV500", "Hi3516DV300"]
    assert fm.choose_soc([]) is None


def test_parse_install():
    install = fm.parse_install(b'{"Commands": ["burn kernel.img kernel", "burn romfs-x.squashfs.img rootfs"],'
                               b' "Devices": [["IPC-HX3XXX", "1.00"]], "Vendor": "General",'
                               b' "Version": {"SoftwareVersion": "V2.800.0.R"}, "MarketArea": ["Oversea"]}\n'
                               b'//IPC_RestoreDefault\n')
    assert install["devices"] == [["IPC-HX3XXX", "1.00"]]
    assert install["vendor"] == "General"
    assert install["burns"] == {"kernel.img": "kernel", "romfs-x.squashfs.img": "rootfs"}
    assert install["software_version"] == "V2.800.0.R"
    assert install["market_area"] == ["Oversea"]


def test_parse_install_lua():
    lua = fm.parse_install_lua(INSTALL_LUA.encode("gb2312"))
    assert lua["ids"] == ["NVR4X-4KS2", "NVR4X-4KS2", "DAHUA"]
    assert lua["board_names"] == ["NVR4X-4KS2"]
    assert lua["vendor_names"] == ["NVR4X-4KS2", "DAHUA"]
    assert [p["name"] for p in lua["partitions"]] == ["boot", "env", "uImage", "rootfs"]
    assert lua["partitions"][3] == {"name": "rootfs", "start": 0xf00000, "end": 0x4500000}
    assert lua["updates"] == {"u-boot.bin.img": "boot", "uImage.img": "uImage", "romfs-x.squashfs.img": "rootfs"}
    assert lua["security_baseline"] == "V1.4"
    assert lua["upgrade_security_version"] == "V2.0"


def test_parse_check_img():
    check = fm.parse_check_img(uimage("check") + b'{"DefaultLanguage": "English", "DefaultVideoStandard": "PAL",'
                               b' "SupportLanguages": "English,French", "hwid": ["IPC-A:01:02"],'
                               b' "SecurityBaselineVersion": "V2.3,", "HardwareSecurityVersionMajor": "1",'
                               b' "FlashSize": "16M", "FlashType": "SPI"}')
    assert check["supported_languages"] == ["English", "French"]
    assert check["security_baseline"] == "V2.3"
    assert check["security"] == {"SecurityBaselineVersion": "V2.3,", "HardwareSecurityVersionMajor": "1"}
    assert (check["flash_size"], check["flash_type"]) == ("16M", "SPI")


def test_partition_table_and_cramfs():
    rows = fm.parse_partition_table(PARTITION_TABLE)
    assert [r["name"] for r in rows] == ["MinBoot", "U-Boot", "hwid", "Kernel", "romfs", "config"]
    assert rows[1] == {"name": "U-Boot", "start": 0x80000, "end": 0xc0000, "backup_start": 0x40000}
    assert rows[4] == {"name": "romfs", "start": 0x270000, "end": 0x3c0000, "filesystem": "squashfs",
                       "read_only": True}
    files = fm.cramfs_files(cramfs({"partitionV2.txt": PARTITION_TABLE.encode(), "big.txt": b"y" * 9000}))
    assert files["partitionV2.txt"] == PARTITION_TABLE.encode()
    assert files["big.txt"] == b"y" * 9000
    assert fm.choose_partition_table(files)[0] == "partitionV2.txt"
    name, table, variants = fm.choose_partition_table({"partitionV2_A.txt": b"", "partitionV2_B.txt": b""})
    assert (name, table, variants) == (None, [], ["partitionV2_A.txt", "partitionV2_B.txt"])


def test_kernel_and_bootloader_versions():
    kernel = gzip.compress(b"\0" * 100 + b"Linux version 4.9.37 (root@build) #1 SMP" + b"\0" * 100)
    assert fm.find_kernel_version(b"zImage stub" + kernel)[0] == "4.9.37"
    assert fm.find_bootloader_version(b"..U-Boot 2019.04-svn13580 (Dec 21 2023).") == "U-Boot 2019.04-svn13580"


# --- whole packages ----------------------------------------------------------------------------------------------

def ipc_firmware(encrypted=False):
    def content():
        return os.urandom(5000) if encrypted else squashfs()

    check = uimage("check") + (b'{"DefaultLanguage": "English", "DefaultVideoStandard": "NTSC",'
                               b' "SupportLanguages": "English,Spanish", "SecurityBaselineVersion": "V2.4,",'
                               b' "hwid": ["IPC-HFW2541SP-S:01:02:04:C8"]}')
    kernel = uimage("kernel", gzip.compress(b"Linux version 4.9.37 (x)"), start=0xf0000, end=0x270000)
    boot = uimage("boot", struct.pack("<4I", 0xea000006, 0xea000005, 0, 0) + b"U-Boot 2019.04 hi3516cv500 ",
                  start=0x80000, end=0xc0000)
    return to_dh_variant(make_zip_bytes({
        "Install": b'{"Commands": ["burn kernel.img kernel", "burn romfs-x.squashfs.img rootfs",'
                   b' "burn dhboot.bin.img bootloader"], "Devices": [["IPC-HX3XXX", "1.00"]], "Vendor": "Amcrest"}',
        "check.img": check,
        "hwid": b'{"hwid": ["IPC-HFW2541SP-S:01:02:04:C8"]}',
        "dhboot.bin.img": boot,
        "kernel.img": kernel,
        "romfs-x.squashfs.img": uimage("romfs", content(), start=0x270000, end=0x3c0000,
                                       timestamp=BUILT + 86400 * 3),
        "partition-x.cramfs.img": uimage("partition", cramfs({"partitionV2.txt": PARTITION_TABLE.encode()}),
                                         start=0xe0000, end=0xf0000),
        "sign.img": os.urandom(128),
    }))


def test_ipc_package(write_file):
    result = read_firmware(write_file("ipc.bin", ipc_firmware()))
    # Install's board family is listed but doesn't count: the exact hwid list in the same folder does
    assert result["hardware_ids"] == ["IPC-HFW2541SP-S"]
    counted = {s["source"]: s["counted"] for s in result["hardware_sources"]}
    assert counted["install"] is False and counted["hwid"] is True
    assert [(s["source"], s["member"], s["firmware"]) for s in result["hardware_sources"]] == [
        ("install", "Install", None), ("check_img", "check.img", None), ("hwid", "hwid", None)]
    assert result["hardware_sources"][1]["raw"] == ["IPC-HFW2541SP-S:01:02:04:C8"]
    assert result["hardware_sources"][0]["vendor"] == "Amcrest"
    assert result["package_format"] == "dh"

    [package] = result["packages"]
    assert package["soc"]["name"] == "Hi3516CV500" and package["soc"]["vendor"] == "HiSilicon"
    assert package["architecture"] == "arm"
    assert package["partition_source"] == "partition_table"
    assert package["partition_table"] == "partitionV2.txt"
    by_name = {p["name"]: p for p in package["partitions"]}
    assert list(by_name) == ["MinBoot", "U-Boot", "hwid", "Kernel", "romfs", "config"]
    assert by_name["Kernel"]["image"] == "kernel.img" and by_name["Kernel"]["upgraded"] is True
    assert by_name["romfs"]["filesystem"] == "squashfs" and by_name["romfs"]["read_only"] is True
    assert by_name["U-Boot"]["image"] == "dhboot.bin.img" and by_name["U-Boot"]["backup_start"] == "0x40000"
    assert by_name["config"] == {"name": "config", "image": None, "start": "0x3c0000", "end": "0x400000",
                                 "size": 0x40000, "image_size": None, "image_name": None, "image_type": None,
                                 "filesystem": "jffs2", "compression": None, "built": None, "upgraded": False,
                                 "read_only": False}
    assert package["flash_size"] == 0x400000
    assert package["build_dates"] == {"earliest": "2020-09-13", "latest": "2020-09-16"}
    assert package["kernel_version"] == "4.9.37"
    assert package["bootloader_version"] == "U-Boot 2019.04"
    assert package["oem_vendor"] == "Amcrest"
    assert package["security"]["signed"] is True
    assert package["security"]["encrypted"] is False
    assert package["security"]["security_baseline"] == "V2.4"
    assert package["locale"] == {"default_language": "English", "video_standard": "NTSC",
                                 "supported_languages": ["English", "Spanish"]}


def test_encrypted_images(write_file):
    result = read_firmware(write_file("enc.bin", ipc_firmware(encrypted=True)))
    # The kernel is readable (gzip), so the package as a whole isn't certainly encrypted
    assert result["packages"][0]["security"]["encrypted"] is None
    data = to_dh_variant(make_zip_bytes({
        "hwid": b'{"hwid": ["IPC-A:01"]}',
        "romfs-x.squashfs.img": uimage("romfs", os.urandom(5000), start=0x270000, end=0x3c0000),
        "user-x.squashfs.img": uimage("user", os.urandom(5000), start=0x3c0000, end=0x500000),
    }))
    assert read_firmware(write_file("enc2.bin", data))["packages"][0]["security"]["encrypted"] is True


def test_recorder_package_from_install_lua(write_file):
    data = to_dh_variant(make_zip_bytes({
        "Install.lua": INSTALL_LUA.encode("gb2312"),
        "u-boot.bin.img": uimage("NVR4X-S2", b"U-Boot 2016.11 Hi3798CV200_DDR3", start=0xa0000000, end=0xa0300000,
                                 compression=0),
        "uImage.img": uimage("NVR4X-S2", b"\0" * 64, start=0xa0500000, end=0xa0f00000),
        "romfs-x.squashfs.img": uimage("NVR4X-S2", squashfs(), start=0xa0f00000, end=0xa4500000),
        "web-x.squashfs.img": uimage("NVR4X-S2", squashfs(), start=0xa4500000, end=0xa4f00000),
    }))
    result = read_firmware(write_file("nvr.bin", data))
    # The Install.lua comparisons list the hardware, so the u-boot board name isn't used
    assert result["hardware_ids"] == ["DAHUA", "NVR4X-4KS2"]
    assert [s["source"] for s in result["hardware_sources"]] == ["install_lua"]
    [package] = result["packages"]
    assert package["partition_source"] == "install_lua"
    assert [(p["name"], p["image"], p["start"], p["upgraded"]) for p in package["partitions"]] == [
        ("boot", "u-boot.bin.img", "0x0", True), ("env", None, "0x300000", False),
        ("uImage", "uImage.img", "0x500000", True), ("rootfs", "romfs-x.squashfs.img", "0xf00000", True)]
    assert package["unmapped_images"] == ["web-x.squashfs.img"]
    assert package["flash_size"] == 0x4500000
    assert package["soc"]["name"] == "Hi3798CV200"
    assert package["oem_vendor_checks"] == ["NVR4X-4KS2", "DAHUA"]
    assert package["security"]["security_baseline"] == "V1.4"
    assert package["security"]["upgrade_security_version"] == "V2.0"


def test_uboot_board_name_and_chip_name(write_file):
    data = to_dh_variant(make_zip_bytes({
        "u-boot.bin.img": uimage("NVR4X-4KS2/L", b"\0" * 100, start=0xa0000000, end=0xa0100000),
        "romfs-x.squashfs.img": uimage("3535romfs", squashfs(), start=0xa0100000, end=0xa1000000),
    }))
    result = read_firmware(write_file("nvr.bin", data))
    assert result["hardware_ids"] == ["NVR4X-4KS2/L"]
    assert result["hardware_sources"] == [{"source": "uboot", "member": "u-boot.bin.img", "firmware": None,
                                           "ids": ["NVR4X-4KS2/L"], "counted": True}]
    package = result["packages"][0]
    assert package["soc"]["name"] == "Hi3535"
    assert package["partition_source"] == "image_headers"
    assert package["flash_size"] is None


def test_device_tree_soc(write_file):
    data = to_dh_variant(make_zip_bytes({
        "hwid": b'{"hwid": ["IPC-A:01"]}',
        "dhdtb.bin.img": uimage("dtb", fdt(["sstar,infinity6c"], model="INFINITY6C SSC027D-S01A"), start=0x1c00000,
                                end=0x1d00000),
    }))
    soc = read_firmware(write_file("dt.bin", data))["packages"][0]["soc"]
    assert (soc["name"], soc["vendor"]) == ("Infinity6C", "SigmaStar")
    assert soc["evidence"][0] == {"source": "device_tree", "member": "dhdtb.bin.img", "value": "sstar,infinity6c"}


def stored_zip_bytes(members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_bundle_has_a_package_per_firmware(write_file):
    first = to_dh_variant(make_zip_bytes({"hwid": b'{"hwid": ["IPC-A:01"]}',
                                          "kernel.img": uimage("kernel", b"\0" * 64, start=0x10000, end=0x20000)}))
    second = to_dh_variant(make_zip_bytes({"hwid": b'{"hwid": ["IPC-B:01"]}',
                                           "kernel.img": uimage("kernel", b"\0" * 64, start=0x30000, end=0x40000)}))
    result = read_firmware(write_file("bundle.zip", stored_zip_bytes({"a.bin": first, "b.bin": second,
                                                                       "README.txt": b"hi"})))
    assert result["package_format"] == "bundle"
    assert result["hardware_ids"] == ["IPC-A", "IPC-B"]
    assert [(s["firmware"], s["ids"]) for s in result["hardware_sources"]] == [("a.bin", ["IPC-A"]),
                                                                               ("b.bin", ["IPC-B"])]
    assert [(p["firmware"], p["package_format"], p["partitions"][0]["start"]) for p in result["packages"]] == [
        ("a.bin", "dh", "0x10000"), ("b.bin", "dh", "0x30000")]


def test_wrapper_zip_is_not_a_bundle(write_file):
    inner = to_dh_variant(make_zip_bytes({"hwid": b'{"hwid": ["IPC-A:01"]}'}))
    result = read_firmware(write_file("w.zip", make_zip_bytes({"fw.bin": inner, "README.txt": b"hi"})))
    assert result["package_format"] == "zip"
    assert [p["firmware"] for p in result["packages"]] == ["fw.bin"]


def test_process_saves_new_fields(write_file, monkeypatch):
    saved = {}
    monkeypatch.setattr(firmware_processing, "save_result", lambda name, result: saved.update({name: result}))
    path = write_file("ipc.bin", ipc_firmware())
    result = firmware_processing.process_firmware_threaded("ipc.bin", path)
    assert saved["ipc.bin"] is result
    assert result["status"] == "ok" and result["extractor_version"] == 5
    assert result["package_format"] == "dh"
    assert result["hardware_sources"] and result["packages"][0]["partitions"]


@pytest.mark.parametrize("result, platform, expected", [
    ({"status": "ok", "extractor_version": 4}, "dahua", True),
    ({"status": "ok", "extractor_version": 5}, "dahua", False),
    ({"status": "not_dahua", "extractor_version": 4}, "hikvision", False),
    ({"status": "no_ids", "extractor_version": 4}, "dahua", True),
    ({"status": "extract_failed", "extractor_version": 5, "attempts": 3}, "dahua", False),
])
def test_needs_processing(result, platform, expected):
    assert needs_processing(result, platform) is expected


def test_zip_with_data_descriptors_is_read_from_its_directory(write_file):
    """Archivers write data descriptors (sizes after the data), which the local-header walk can't follow."""
    inner = to_dh_variant(make_zip_bytes({"hwid": b'{"hwid": ["IPC-A:01"]}'}))
    class Unseekable:
        """zipfile writes data descriptors when it can't seek back to fill in the sizes"""
        def __init__(self):
            self.buffer = io.BytesIO()

        def write(self, data):
            return self.buffer.write(data)

        def flush(self):
            pass

        def tell(self):
            raise OSError("not seekable")

    stream = Unseekable()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("__MACOSX/._fw.bin", b"resource fork")
        archive.writestr("fw.bin", inner)
    data = stream.buffer.getvalue()
    assert struct.unpack("<H", data[data.index(b"PK\x03\x04", 10) + 6:][:2])[0] & 0x08
    result = read_firmware(write_file("dd.zip", data))
    assert result["hardware_ids"] == ["IPC-A"]
    assert [p["firmware"] for p in result["packages"]] == ["fw.bin"]


def test_bare_uimage_file_becomes_a_package(write_file, monkeypatch):
    monkeypatch.setattr(firmware_processing, "analyze_slowly",
                        lambda path: firmware_processing.analysis([], "no_ids"))
    path = write_file("NVR4xxx_u-boot.bin.img", uimage("3535boot", b"U-Boot 2010.06 hi3535 ", start=0, end=0x40000))
    result = firmware_processing.analyze_firmware(path)
    assert result["status"] == "no_ids"
    [package] = result["packages"]
    assert package["soc"]["name"] == "Hi3535"
    assert package["bootloader_version"] == "U-Boot 2010.06"
    assert package["partitions"][0]["end"] == "0x40000"


def test_install_counts_when_no_hwid_list(write_file):
    data = to_dh_variant(make_zip_bytes({"Install": b'{"Devices": [["IPC-HX3XXX", "1.00"]], "Vendor": "General"}'}))
    result = read_firmware(write_file("old.bin", data))
    assert result["hardware_ids"] == ["IPC-HX3XXX"]
    assert result["hardware_sources"][0]["counted"] is True
