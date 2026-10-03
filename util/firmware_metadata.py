"""What a firmware says about itself: where its hardware IDs come from, the SoC it's built for, its flash partition
layout, security and locale settings, and kernel / boot loader versions.

Everything here works on bytes from members of a firmware package (a Dahua zip-style container, or the files
binwalk unpacked), through the small Member interface, so the same code serves the fast reader and the fallback.
Only headers and small members are read in full; big filesystems are only sniffed (their first few KB)."""
import json
import lzma
import math
import os
import re
import struct
import time
import zlib
from collections import Counter

# Small members read in full (metadata, partition tables, device trees)
MAX_SMALL_MEMBER = 4 * 1024 ** 2
# Boot loaders and kernels are read in full up to this size, and inflated up to MAX_INFLATED
MAX_CODE_MEMBER = 24 * 1024 ** 2
MAX_INFLATED = 48 * 1024 ** 2
# Bytes sniffed from every member: the uImage header plus enough payload to recognise it
SNIFF_SIZE = 64 + 4096

UIMAGE_MAGIC = b'\x27\x05\x19\x56'
METADATA_MEMBERS = ('check.img', 'hwid', 'Install', 'Install.lua')
UBOOT_MEMBER = 'u-boot.bin.img'

# u-boot image header codes (include/image.h)
# Only architectures Dahua devices plausibly use; other codes (old images sometimes carry junk) give None
ARCH_NAMES = {2: 'arm', 3: 'x86', 5: 'mips', 6: 'mips64', 7: 'powerpc', 22: 'arm64', 24: 'x86_64', 26: 'riscv'}
TYPE_NAMES = {1: 'standalone', 2: 'kernel', 3: 'ramdisk', 4: 'multi', 5: 'firmware', 6: 'script', 7: 'filesystem',
              8: 'flat_dt'}
COMPRESSION_NAMES = {0: 'none', 1: 'gzip', 2: 'bzip2', 3: 'lzma', 4: 'lzo', 5: 'lz4', 6: 'zstd'}

# Image names that name the chip or the boot loader rather than a board ("ss528V100", "RK3588", "nt98631boot");
# these aren't hardware IDs, but they are SoC evidence
CHIP_IMAGE_NAME = re.compile(r"^(hi|ss|ssr|rk|nt|ax|gk|sc|mc|fh)?\d{3,}[a-z0-9_]*$|boot$", re.IGNORECASE)

# Anything in check.img / Install / Install.lua that looks like a security, baseline or rollback setting
SECURITY_KEY = re.compile(r"security|rollback|baseline|min_?version|lowest_?version|downgrade", re.IGNORECASE)


class Member:
    """One file in a package. Subclasses provide head() and read()."""
    path = ''
    size = 0

    @property
    def name(self):
        return os.path.basename(self.path)

    def head(self, length=SNIFF_SIZE):
        raise NotImplementedError

    def read(self, limit=MAX_SMALL_MEMBER):
        """The whole member, or None if it's bigger than limit."""
        raise NotImplementedError


class FileMember(Member):
    def __init__(self, file_path, path):
        self.file_path = file_path
        self.path = path
        self.size = os.path.getsize(file_path)

    def head(self, length=SNIFF_SIZE):
        with open(self.file_path, 'rb') as f:
            return f.read(length)

    def read(self, limit=MAX_SMALL_MEMBER):
        if self.size > limit:
            return None
        with open(self.file_path, 'rb') as f:
            return f.read()


def members_in_directory(root, max_depth=1):
    """FileMembers for the files in root and its subfolders (binwalk's extraction folder), paths relative to root."""
    members = []
    root_depth = root.rstrip(os.sep).count(os.sep)
    for folder, folders, names in os.walk(root):
        if folder.count(os.sep) - root_depth >= max_depth:
            folders[:] = []
        for name in sorted(names):
            file_path = os.path.join(folder, name)
            if os.path.isfile(file_path) and not os.path.islink(file_path):
                members.append(FileMember(file_path, os.path.relpath(file_path, root)))
    return members


# --- uImage headers and payloads ---------------------------------------------------------------------------------

def parse_uimage_header(data):
    """The fields of a u-boot image header at the start of data, or None. Dahua stores each image's flash partition
    start and end in the load address and entry point fields."""
    if len(data) < 64 or data[:4] != UIMAGE_MAGIC:
        return None
    _, _, timestamp, size, load, entry, _, _, arch, image_type, compression = struct.unpack('>IIIIIIIBBBB', data[:32])
    name = data[32:64].split(b'\0', 1)[0].decode('ascii', 'replace').strip()
    return {
        'name': name,
        'size': size,
        'start': load,
        'end': entry,
        'arch': ARCH_NAMES.get(arch),
        'type': TYPE_NAMES.get(image_type, str(image_type)),
        'compression': COMPRESSION_NAMES.get(compression, str(compression)),
        # Header times before 2005 are unset clocks, not build dates
        'built': time.strftime('%Y-%m-%d', time.gmtime(timestamp)) if timestamp >= 1104537600 else None,
    }


def uimage_name(data):
    """The image name in the first uImage header in data (e.g. "NVR4X-4KS2/L"), or None."""
    position = data.find(UIMAGE_MAGIC)
    while 0 <= position <= len(data) - 64:
        name = data[position + 32:position + 64].split(b'\0', 1)[0].decode('ascii', 'replace').strip()
        if name and name.isprintable():
            return name
        position = data.find(UIMAGE_MAGIC, position + 1)
    return None


def payload_kind(data):
    """What an image payload is, from its magic bytes: a filesystem, a compressed stream, a kernel, a device tree,
    executable code... None when nothing matches (encrypted, or a format we don't know)."""
    if len(data) < 4:
        return None
    head = data[:8]
    if head[:4] in (b'hsqs', b'sqsh'):
        return 'squashfs'
    if head[:4] in (b'\x45\x3d\xcd\x28', b'\x28\xcd\x3d\x45'):
        return 'cramfs'
    if head[:4] == b'UBI#':
        return 'ubi'
    if head[:2] in (b'\x85\x19', b'\x19\x85'):
        return 'jffs2'
    if head[:8] == b'-rom1fs-':
        return 'romfs'
    if head[:2] == b'\x1f\x8b':
        return 'gzip'
    if head[:6] == b'\xfd7zXZ\x00':
        return 'xz'
    if head[:3] == b'\x5d\x00\x00':
        return 'lzma'
    if head[:3] == b'BZh':
        return 'bzip2'
    if head[:4] in (b'\x04\x22\x4d\x18', b'\x02\x21\x4c\x18'):
        return 'lz4'
    if head[:4] == b'\x28\xb5\x2f\xfd':
        return 'zstd'
    if head[:4] == b'\x89LZO':
        return 'lzo'
    if head[:4] == b'\xd0\x0d\xfe\xed':
        return 'fdt'
    if head[:4] == b'\x7fELF':
        return 'elf'
    if head[:4] == UIMAGE_MAGIC:
        return 'uimage'
    if head[:4] == b'PK\x03\x04':
        return 'zip'
    if len(data) >= 0x28 and data[0x24:0x28] == b'\x18\x28\x6f\x01':
        return 'zimage'
    if len(data) >= 0x3c and data[0x38:0x3c] == b'ARM\x64':
        return 'arm64_image'
    if len(data) >= 0x43a and data[0x438:0x43a] == b'\x53\xef':
        return 'ext4'
    # Boot loaders are raw code: an ARM branch (0xEA......) or "ldr pc" vector table, or an AArch64 branch
    words = struct.unpack('<4I', data[:16]) if len(data) >= 16 else ()
    if words and all((w >> 24) == 0xEA or (w & 0xFFFFF000) == 0xE59FF000 for w in words[:2]):
        return 'arm_code'
    if words and (words[0] >> 26) == 0x05:
        return 'arm64_code'
    if b'IPL_' in data[:16] or data[:4] == b'BUHD' or b'SIGMASTAR' in data[:256].upper():
        return 'boot_code'
    return None


def entropy(data):
    if not data:
        return 0.0
    counts = Counter(data)
    return -sum(c / len(data) * math.log2(c / len(data)) for c in counts.values())


def inflate(data, limit=MAX_INFLATED):
    """Decompress a gzip, xz or lzma stream at the start of data (bounded). None if it isn't one."""
    kind = payload_kind(data)
    try:
        if kind == 'gzip':
            return zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(data, limit)
        if kind in ('xz', 'lzma'):
            decompressor = lzma.LZMADecompressor(lzma.FORMAT_XZ if kind == 'xz' else lzma.FORMAT_ALONE)
            return decompressor.decompress(data, max_length=limit)
    except (zlib.error, lzma.LZMAError, EOFError, ValueError):
        return None
    return None


def image_payload(data, header=None):
    """An image's payload (after its uImage header), inflated when it's a gzip/xz/lzma stream."""
    payload = data[64:] if header else data
    inflated = inflate(payload)
    return inflated if inflated is not None else payload


# --- Device trees ------------------------------------------------------------------------------------------------

def fdt_root_properties(data):
    """The root node's string properties (compatible, model) of a flattened device tree. compatible is a list."""
    if len(data) < 40 or data[:4] != b'\xd0\x0d\xfe\xed':
        return {}
    total, struct_offset, strings_offset = struct.unpack('>III', data[4:16])
    properties = {}
    position = struct_offset
    depth = 0
    end = min(len(data), total)
    while position + 4 <= end:
        token = struct.unpack('>I', data[position:position + 4])[0]
        position += 4
        if token == 1:  # FDT_BEGIN_NODE
            name_end = data.index(b'\0', position)
            position = (name_end + 4) & ~3
            depth += 1
            if depth > 1:
                break  # the root's own properties come before its first child
        elif token == 3:  # FDT_PROP
            length, name_offset = struct.unpack('>II', data[position:position + 8])
            value = data[position + 8:position + 8 + length]
            position = (position + 8 + length + 3) & ~3
            name = data[strings_offset + name_offset:data.index(b'\0', strings_offset + name_offset)].decode(
                'ascii', 'replace')
            if name == 'compatible':
                properties['compatible'] = [v.decode('ascii', 'replace') for v in value.split(b'\0') if v]
            elif name == 'model':
                properties['model'] = value.rstrip(b'\0').decode('ascii', 'replace')
        elif token == 4:  # FDT_NOP
            continue
        else:  # FDT_END_NODE, FDT_END, or garbage
            break
    return properties


# --- SoC names ---------------------------------------------------------------------------------------------------

def _upper_suffix(prefix, rest):
    return prefix + rest.upper()


def _family_name(value):
    """SigmaStar family code names: "infinity6c" -> "Infinity6C"."""
    match = re.match(r'([a-z]+)(.*)', value.lower())
    return match.group(1).capitalize() + match.group(2).upper() if match else value


# (pattern, function(match) -> (name or None, vendor)). Ordered most specific first
SOC_PATTERNS = [
    (re.compile(r'(?<![a-z0-9])hi(3[5-7]\d{2}[a-z]{0,2}v\d{3})(?![a-z0-9])', re.I),
     lambda m: (_upper_suffix('Hi', m.group(1)), 'HiSilicon')),
    (re.compile(r'(?<![a-z0-9])hi(3[5-7]\d{2}[a-z]?)(?![a-z0-9])', re.I),
     lambda m: (_upper_suffix('Hi', m.group(1)), 'HiSilicon')),
    (re.compile(r'(?<![a-z0-9])ss(\d{3})v(\d{3})(?![0-9])', re.I),
     lambda m: (f"SS{m.group(1)}V{m.group(2)}", 'HiSilicon')),
    (re.compile(r'(?<![a-z0-9])ssc(\d{3}[a-z]{0,2})(?![a-z0-9])', re.I),
     lambda m: (_upper_suffix('SSC', m.group(1)), 'SigmaStar')),
    (re.compile(r'sstar,(infinity\d+[a-z]*|pioneer\d*[a-z]*|mercury\d*[a-z]*|ikayaki|souffle|ifado|iford)', re.I),
     lambda m: (_family_name(m.group(1)), 'SigmaStar')),
    # "5x16FW98336Tboot": Dahua names recorder images after the chip, prefixed by the board
    (re.compile(r'(?<![a-z])(?:nt|fw)(98\d{3})(?![0-9])', re.I),
     lambda m: (f"NT{m.group(1)}", 'Novatek')),
    # Novatek's internal codes (NA51089) don't say which NT part it is, so they only give the vendor
    (re.compile(r'novatek,(?:nvt-)?na5\d{4}', re.I),
     lambda m: (None, 'Novatek')),
    (re.compile(r'(?<![a-z0-9])(rk3\d{3}|rv1\d{3}|rk1\d{3})(?![0-9])', re.I),
     lambda m: (m.group(1).upper(), 'Rockchip')),
    (re.compile(r'(?<![a-z0-9])fh(8\d{3}[a-z]?(?:v\d{3})?)(?![a-z0-9])', re.I),
     lambda m: (_upper_suffix('FH', m.group(1)), 'Fullhan')),
    (re.compile(r'(?<![a-z0-9])gk(7\d{3}[a-z]?(?:v\d{3})?)(?![a-z0-9])', re.I),
     lambda m: (_upper_suffix('GK', m.group(1)), 'Goke')),
    (re.compile(r'ingenic,(t\d{2}[a-z]?)', re.I),
     lambda m: (m.group(1).upper(), 'Ingenic')),
    (re.compile(r'ambarella,(s[2-6][a-z]{1,2}m?|cv\d{1,2}[a-z0-9]*|h\d+)', re.I),
     lambda m: (m.group(1).upper(), 'Ambarella')),
    (re.compile(r'(?<![a-z0-9])(mc6\d{3}[a-z]?)(?![a-z0-9])', re.I),
     lambda m: (m.group(1).upper(), 'Molchip')),
    (re.compile(r'(?<![a-z0-9])(ax6\d{2}[a-z]?)(?![a-z0-9])', re.I),
     lambda m: (m.group(1).upper(), 'Axera')),
    (re.compile(r'hisilicon,(hi3\d{3}[a-z0-9]*)', re.I),
     lambda m: (_upper_suffix('Hi', m.group(1)[2:]), 'HiSilicon')),
    # Vendor only
    (re.compile(r'sigmastar|sstar,', re.I), lambda m: (None, 'SigmaStar')),
    (re.compile(r'ambarella', re.I), lambda m: (None, 'Ambarella')),
    (re.compile(r'hisilicon', re.I), lambda m: (None, 'HiSilicon')),
    (re.compile(r'novatek', re.I), lambda m: (None, 'Novatek')),
    (re.compile(r'rockchip', re.I), lambda m: (None, 'Rockchip')),
]

# Recorder image names: "3521boot", "3535boot", "3798romfs"
HISILICON_IMAGE_NAME = re.compile(r'^(3[5-7]\d{2}[a-z]?)(?:boot|romfs|kernel)?$', re.I)


def normalize_soc(value):
    """(name, vendor) for a string that names a SoC, or None. name can be None when only the vendor is known."""
    match = HISILICON_IMAGE_NAME.match(value.strip())
    if match:
        return f"Hi{match.group(1).upper()}", 'HiSilicon'
    for pattern, convert in SOC_PATTERNS:
        match = pattern.search(value)
        if match:
            return convert(match)
    return None


def soc_strings(data, limit=None):
    """Distinct SoC names (and vendor-only hits) found in binary data, as (name, vendor, matched text)."""
    if limit:
        data = data[:limit]
    text = data.decode('latin-1')
    found = {}
    for pattern, convert in SOC_PATTERNS:
        for match in pattern.finditer(text):
            name, vendor = convert(match)
            key = name or vendor
            found.setdefault(key, (name, vendor, match.group(0)))
    return list(found.values())


def choose_soc(evidence):
    """The SoC from evidence ordered strongest first: the first named chip, with its vendor; a vendor alone when
    no evidence names the chip. Conflicting chip names in the strongest evidence leave the name unknown."""
    if not evidence:
        return None
    named = [e for e in evidence if e.get('_name')]
    name = vendor = None
    candidates = []
    if named:
        best = named[0]
        same_rank = [e for e in named if e['_rank'] == best['_rank']]
        if len({e['_name'] for e in same_rank}) == 1:
            name, vendor = best['_name'], best['_vendor']
        else:
            # e.g. a boot loader built for several chips: say which, but don't pick one
            candidates = list(dict.fromkeys(e['_name'] for e in same_rank))
            if len({e['_vendor'] for e in same_rank}) == 1:
                vendor = best['_vendor']
    if not vendor:
        vendors = [e['_vendor'] for e in evidence if e.get('_vendor')]
        vendor = vendors[0] if vendors and len(set(vendors)) == 1 else None
    if not name and not vendor:
        return None
    soc = {
        'name': name,
        'vendor': vendor,
        'evidence': [{k: v for k, v in e.items() if not k.startswith('_')} for e in evidence],
    }
    if candidates:
        soc['candidates'] = candidates
    return soc


# --- check.img, hwid, Install, Install.lua -----------------------------------------------------------------------

def parse_json_object(data):
    """The first JSON object in data (bytes or str), tolerating headers before it and comments after it."""
    text = data.decode('utf-8', 'replace') if isinstance(data, bytes) else data
    start = text.find('{')
    if start < 0:
        return None
    try:
        value, _ = json.JSONDecoder(strict=False).raw_decode(text[start:])
        return value if isinstance(value, dict) else None
    except ValueError:
        pass
    # Older parser's fallback: the text between the last "{" and the next "}"
    inner = text[text.rfind('{') + 1:]
    inner = inner[:inner.find('}')] if '}' in inner else inner
    try:
        value = json.loads('{' + inner + '}', strict=False)
        return value if isinstance(value, dict) else None
    except ValueError:
        return None


def hwid_entries(data):
    value = parse_json_object(data) or {}
    entries = value.get('hwid')
    return [str(e) for e in entries if isinstance(e, str) and e.strip()] if isinstance(entries, list) else []


def ids_from_hwid(entries):
    return [entry.split(':')[0] for entry in entries if entry.split(':')[0]]


def parse_install(data):
    """Install (JSON + trailing comment): devices, vendor, burn commands (image -> partition) and other settings."""
    value = parse_json_object(data) or {}
    devices = [d for d in value.get('Devices') or [] if isinstance(d, list) and d and isinstance(d[0], str)]
    burns = {}
    for command in value.get('Commands') or []:
        parts = str(command).split()
        if len(parts) >= 3 and parts[0] == 'burn':
            burns[parts[1]] = parts[2]
    software_version = value.get('Version', {}).get('SoftwareVersion') if isinstance(value.get('Version'), dict) else None
    return {
        'devices': devices,
        'vendor': value.get('Vendor') if isinstance(value.get('Vendor'), str) else None,
        'burns': burns,
        'software_version': software_version,
        'market_area': value.get('MarketArea') if isinstance(value.get('MarketArea'), list) else None,
        'security': {k: v for k, v in value.items() if SECURITY_KEY.search(k)},
    }


def strip_lua_comments(text):
    text = re.sub(r'--\[(=*)\[.*?\]\1\]', '', text, flags=re.S)
    return re.sub(r'--[^\n]*', '', text)


INSTALL_LUA_ID = re.compile(r'(?:board.name|vendor.Name) +[~|=]= +["|\']')
LUA_PARTITION = re.compile(r'([A-Za-z_]\w*)\s*=\s*\{\s*baseAddr\s*=\s*(0x[0-9a-fA-F]+)\s*,\s*endAddr\s*=\s*(0x[0-9a-fA-F]+)\s*,?\s*\}')
LUA_UPDATE_PART = re.compile(r'updatePart\s*\(\s*flashPartions\s*(?:\[\s*["\'](\w+)["\']\s*\]|\.(\w+))\s*,\s*["\']([^"\']+)["\']')


def lua_table_block(text, name):
    """The text inside the first `name = { ... }` table, by brace matching."""
    match = re.search(re.escape(name) + r'\s*=\s*\{', text)
    if not match:
        return None
    depth = 0
    for position in range(match.end() - 1, len(text)):
        if text[position] == '{':
            depth += 1
        elif text[position] == '}':
            depth -= 1
            if depth == 0:
                return text[match.end():position]
    return None


def parse_install_lua(data):
    text = data.decode('gb2312', 'replace') if isinstance(data, bytes) else data
    # Hardware IDs: board.name / vendor.Name comparisons (as the original parser read them, comments included)
    ids, board_names, vendor_names = [], [], []
    for part in INSTALL_LUA_ID.split(text)[1:]:
        value = re.split(r'["|\']', part)[0]
        if value:
            ids.append(value)
    for match in re.finditer(r'(board.name|vendor.Name) +[~|=]= +["\']([^"\']+)["\']', strip_lua_comments(text)):
        (board_names if match.group(1).startswith('board') else vendor_names).append(match.group(2))

    code = strip_lua_comments(text)
    block = lua_table_block(code, 'flashPartions')
    partitions = []
    if block:
        for name, start, end in LUA_PARTITION.findall(block):
            partitions.append({'name': name, 'start': int(start, 16), 'end': int(end, 16)})
    updates = {}
    for table_key, attribute, image in LUA_UPDATE_PART.findall(code):
        updates[image] = table_key or attribute

    baseline = None
    function = re.search(r'function\s+Installer:checkSecurityBaselineVersion\s*\(\)(.*?)\nend', code, re.S)
    if function:
        match = re.search(r'newVersion\s*=\s*["\']([^"\']+)["\']', function.group(1))
        baseline = match.group(1) if match else None
    upgrade_security = re.search(r'UpgradeSecurityVersion\s*=\s*["\']([^"\']+)["\']', code)
    checks = [line.strip()[:200] for line in code.splitlines()
              if re.search(r'rollback|downgrade|min_?version|lowest_?version|AntiRollback', line, re.I)][:10]
    return {
        'ids': ids,
        'board_names': list(dict.fromkeys(board_names)),
        'vendor_names': list(dict.fromkeys(vendor_names)),
        'partitions': partitions,
        'updates': updates,
        'security_baseline': baseline,
        'upgrade_security_version': upgrade_security.group(1) if upgrade_security else None,
        'checks': checks,
    }


def parse_check_img(data):
    value = parse_json_object(data) or {}
    languages = value.get('SupportLanguages')
    if isinstance(languages, str):
        languages = [lang.strip() for lang in languages.split(',') if lang.strip()]
    elif not isinstance(languages, list):
        languages = None
    baseline = value.get('SecurityBaselineVersion')
    return {
        'hwid': [str(e) for e in value.get('hwid') or [] if isinstance(e, str)],
        'default_language': value.get('DefaultLanguage') if isinstance(value.get('DefaultLanguage'), str) else None,
        'video_standard': value.get('DefaultVideoStandard') if isinstance(value.get('DefaultVideoStandard'), str) else None,
        'supported_languages': languages or None,
        'security_baseline': baseline.strip().rstrip(',') if isinstance(baseline, str) and baseline.strip() else None,
        'flash_size': value.get('FlashSize') if isinstance(value.get('FlashSize'), str) else None,
        'flash_type': value.get('FlashType') if isinstance(value.get('FlashType'), str) else None,
        'security': {k: v for k, v in value.items() if SECURITY_KEY.search(k)},
    }


# --- cramfs partition tables -------------------------------------------------------------------------------------

def cramfs_files(data, max_file=1024 ** 2):
    """{path: content} for the regular files in a cramfs image (little-endian, zlib blocks, 4 KB pages)."""
    if len(data) < 76 or struct.unpack('<I', data[:4])[0] != 0x28cd3d45:
        return {}
    files = {}

    def inode(offset):
        a, b, c = struct.unpack('<III', data[offset:offset + 12])
        name_length = (c & 0x3f) * 4
        name = data[offset + 12:offset + 12 + name_length].rstrip(b'\0').decode('utf-8', 'replace')
        return a & 0xffff, b & 0xffffff, (c >> 6) * 4, name, offset + 12 + name_length

    def walk(offset, size, prefix, depth):
        end = offset + size
        while offset < end and depth < 8:
            mode, file_size, data_offset, name, offset = inode(offset)
            kind = mode & 0o170000
            if kind == 0o040000:
                walk(data_offset, file_size, f"{prefix}{name}/", depth + 1)
            elif kind == 0o100000 and file_size <= max_file:
                blocks = (file_size + 4095) // 4096
                pointers = struct.unpack(f'<{blocks}I', data[data_offset:data_offset + 4 * blocks])
                position = data_offset + 4 * blocks
                content = b''
                for pointer in pointers:
                    content += zlib.decompress(data[position:pointer]) if pointer > position else b'\0' * 4096
                    position = pointer
                files[prefix + name] = content[:file_size]

    try:
        mode, size, offset, _, _ = inode(64)
        walk(offset, size, '', 0)
    except (struct.error, zlib.error, ValueError, IndexError):
        pass
    return files


PARTITION_ROW = re.compile(
    r'^\s*([\w\-.]+)\s*,\s*(\d+)\s*,\s*(0x[0-9a-fA-F]+)\s*,\s*(0x[0-9a-fA-F]+)\s*(?:,\s*(\w*)\s*(?:,\s*(\w*)\s*'
    r'(?:,\s*(\w*)\s*)?)?)?')


def parse_partition_table(text):
    """Rows of a Dahua partition table (partitionV2.txt / partition.txt):
    name, cs, offset, size, mask_flags[, fs_flags, fs_type, mount_cmd, backup_off]."""
    rows = []
    for line in text.splitlines():
        if line.lstrip().startswith('#'):
            continue
        match = PARTITION_ROW.match(line)
        if not match:
            continue
        name, _, offset, size, mask_flags, fs_flags, fs_type = match.groups()
        start, length = int(offset, 16), int(size, 16)
        row = {'name': name, 'start': start, 'end': start + length}
        if fs_type:
            row['filesystem'] = fs_type.lower()
        if fs_flags:
            row['read_only'] = fs_flags.upper() == 'R'
        backup = re.findall(r'0x[0-9a-fA-F]+', line[match.end():])
        if backup and int(backup[-1], 16) not in (0xffffffffffffffff, 0xffffffff):
            row['backup_start'] = int(backup[-1], 16)
        rows.append(row)
    return rows


def choose_partition_table(files):
    """(file name, rows) of the table that applies to the firmware: partitionV2.txt, else partition.txt, else the
    only partition table there is. Several board-specific variants with no main one are ambiguous: (None, [], names)."""
    tables = {name: content for name, content in files.items()
              if re.match(r'(.*/)?partition[\w\-]*\.txt$', name, re.I)}
    for preferred in ('partitionV2.txt', 'partition.txt'):
        if preferred in tables:
            return preferred, parse_partition_table(tables[preferred].decode('utf-8', 'replace')), []
    if len(tables) == 1:
        name = next(iter(tables))
        return name, parse_partition_table(tables[name].decode('utf-8', 'replace')), []
    return None, [], sorted(tables)


# --- Kernel and boot loader versions -----------------------------------------------------------------------------

LINUX_VERSION = re.compile(rb'Linux version (\d+\.\d+(?:\.\d+)?[\w.+\-]*)')
UBOOT_VERSION = re.compile(rb'U-Boot (20\d{2}\.\d{2}(?:\.\d+)?[\w.\-]*|1\.\d+\.\d+[\w.\-]*)')


def find_kernel_version(payload):
    """("x.y.z", the kernel's text) from a kernel image: its "Linux version" banner, found directly or inside its
    compressed stream (zImage / uImage). (None, None) if there's no banner."""
    match = LINUX_VERSION.search(payload)
    if match:
        return match.group(1).decode('ascii', 'replace'), payload
    for magic in (b'\x1f\x8b\x08', b'\xfd7zXZ\x00', b'\x5d\x00\x00'):
        position, tries = payload.find(magic), 0
        while position >= 0 and tries < 3:
            inflated = inflate(payload[position:])
            if inflated:
                match = LINUX_VERSION.search(inflated)
                if match:
                    return match.group(1).decode('ascii', 'replace'), inflated
            position, tries = payload.find(magic, position + 1), tries + 1
    return None, None


def find_bootloader_version(payload):
    match = UBOOT_VERSION.search(payload)
    return f"U-Boot {match.group(1).decode('ascii', 'replace')}" if match else None


# --- Packages ----------------------------------------------------------------------------------------------------

BOOT_MEMBER = re.compile(r'(boot|ipl|loader)', re.I)
DTB_MEMBER = re.compile(r'(dtb|fdt)', re.I)
KERNEL_MEMBER = re.compile(r'^(kernel|uimage|zimage)', re.I)
PARTITION_MEMBER = re.compile(r'partition', re.I)
FILESYSTEM_IN_NAME = re.compile(r'\.(squashfs|cramfs|jffs2|ubifs|ubi|ext4|yaffs2|romfs)\b', re.I)
FILESYSTEM_KINDS = ('squashfs', 'cramfs', 'ubi', 'jffs2', 'romfs', 'ext4')
# Payload kinds that show an image isn't encrypted
KNOWN_PAYLOADS = FILESYSTEM_KINDS + ('gzip', 'xz', 'lzma', 'bzip2', 'lz4', 'zstd', 'lzo', 'fdt', 'elf', 'uimage', 'zip',
                                     'zimage', 'arm64_image', 'arm_code', 'arm64_code', 'boot_code')
# Recorders address flash memory-mapped in image headers, but from 0 in Install.lua
RECORDER_FLASH_BASE = 0xa0000000


def partition_name_from_member(name):
    stem = os.path.basename(name)
    for suffix in ('.img', '.bin', '.squashfs', '.cramfs', '.jffs2', '.ubifs', '.ext4'):
        if stem.lower().endswith(suffix):
            stem = stem[:-len(suffix)]
    stem = re.sub(r'\.(squashfs|cramfs|jffs2|ubifs|ext4)$', '', stem, flags=re.I)
    return re.sub(r'-x$', '', stem) or os.path.basename(name)


def hex_address(value):
    return f"0x{value:x}"


class PackageReader:
    """Collects everything one package (a container's members) says about itself."""

    def __init__(self, members, firmware=None, package_format='other'):
        self.members = members
        self.firmware = firmware
        self.package_format = package_format
        self.sources = []          # hardware_sources entries (without uboot)
        self.uboot = []            # hardware_sources entries for board-style u-boot names
        self.evidence = []         # SoC evidence, strongest first once sorted
        self.images = []
        self.install = None
        self.install_lua = None
        self.check = None
        self.table_name, self.table, self.table_variants = None, [], []
        self.kernel_version = self.bootloader_version = None
        self.payload_kinds = {}
        self.read()

    def add_evidence(self, rank, source, member, value):
        normalized = normalize_soc(value)
        if not normalized:
            return
        name, vendor = normalized
        for existing in self.evidence:
            if existing['source'] == source and existing['value'] == value:
                return
        self.evidence.append({'source': source, 'member': member, 'value': value, '_name': name, '_vendor': vendor,
                              '_rank': rank})

    def add_string_evidence(self, rank, source, member, hits):
        """Chip names found in code. Boot loaders carry driver strings for other vendors' chips ("rockchip"), so
        vendor-only hits count only when nothing names a chip and they all agree."""
        named = [hit for hit in hits if hit[0]]
        if not named and len({hit[1] for hit in hits}) != 1:
            return
        for _, _, text in named or hits:
            self.add_evidence(rank, source, member, text)

    def read(self):
        for member in self.members:
            try:
                self.read_member(member)
            except (OSError, ValueError, struct.error, zlib.error, lzma.LZMAError, EOFError, IndexError) as err:
                print(f"\tCouldn't read {member.path}: {err!r}")

    def read_member(self, member):
        name = member.name
        if name in METADATA_MEMBERS:
            data = member.read()
            if data is not None:
                self.read_metadata(member, name, data)
            if name != 'check.img':
                return
        if member.size < 64:
            return
        head = member.head()
        header = parse_uimage_header(head)
        payload_head = head[64:] if header else head
        kind = payload_kind(payload_head)
        self.payload_kinds[member.path] = (kind, entropy(payload_head[:4096]))

        if header:
            image = {
                'member': member.path,
                'header': header,
                'filesystem': kind if kind in FILESYSTEM_KINDS else (
                    FILESYSTEM_IN_NAME.search(name).group(1).lower() if FILESYSTEM_IN_NAME.search(name) else None),
            }
            self.images.append(image)
            if header['name']:
                if name == UBOOT_MEMBER and not CHIP_IMAGE_NAME.search(header['name']):
                    self.uboot.append({'source': 'uboot', 'member': member.path, 'firmware': self.firmware,
                                       'ids': [header['name']]})
                if CHIP_IMAGE_NAME.search(header['name']) or HISILICON_IMAGE_NAME.match(header['name']):
                    self.add_evidence(3, 'image_name', member.path, header['name'])

        if DTB_MEMBER.search(name) and member.size <= MAX_SMALL_MEMBER:
            data = member.read()
            if data:
                properties = fdt_root_properties(image_payload(data, header))
                for value in properties.get('compatible', []):
                    self.add_evidence(1, 'device_tree', member.path, value)
                if properties.get('model'):
                    # The model is free text ("INFINITY6C SSC027D-S01A"), so it ranks below compatible
                    self.add_evidence(1.5, 'device_tree', member.path, properties['model'])
        elif PARTITION_MEMBER.search(name) and member.size <= MAX_SMALL_MEMBER and not self.table:
            data = member.read()
            if data:
                files = cramfs_files(image_payload(data, header))
                table_name, table, variants = choose_partition_table(files)
                if table:
                    self.table_name, self.table = table_name, table
                elif variants:
                    self.table_variants = variants
        elif BOOT_MEMBER.search(name) and member.size <= MAX_CODE_MEMBER:
            data = member.read(MAX_CODE_MEMBER)
            if data:
                payload = image_payload(data, header)
                self.bootloader_version = self.bootloader_version or find_bootloader_version(payload)
                self.add_string_evidence(2, 'bootloader', member.path, soc_strings(payload))
        elif KERNEL_MEMBER.search(name) and member.size <= MAX_CODE_MEMBER and not self.kernel_version:
            data = member.read(MAX_CODE_MEMBER)
            if data:
                payload = data[64:] if header else data
                if header and header['compression'] in ('gzip', 'lzma'):
                    payload = inflate(payload) or payload
                self.kernel_version, text = find_kernel_version(payload)
                hits = [h for h in soc_strings(text[:MAX_INFLATED]) if h[0]] if text else []
                if len({h[0] for h in hits}) == 1:
                    self.add_evidence(4, 'kernel', member.path, hits[0][2])

    def read_metadata(self, member, name, data):
        firmware = self.firmware
        if name == 'hwid':
            entries = hwid_entries(data)
            if entries:
                self.sources.append({'source': 'hwid', 'member': member.path, 'firmware': firmware,
                                     'ids': unique(ids_from_hwid(entries)), 'raw': entries})
        elif name == 'check.img':
            self.check = parse_check_img(data)
            if self.check['hwid']:
                self.sources.append({'source': 'check_img', 'member': member.path, 'firmware': firmware,
                                     'ids': unique(ids_from_hwid(self.check['hwid'])), 'raw': self.check['hwid']})
        elif name == 'Install':
            self.install = parse_install(data)
            if self.install['devices']:
                entry = {'source': 'install', 'member': member.path, 'firmware': firmware,
                         'ids': unique(d[0] for d in self.install['devices']), 'raw': self.install['devices']}
                if self.install['vendor']:
                    entry['vendor'] = self.install['vendor']
                self.sources.append(entry)
        elif name == 'Install.lua':
            self.install_lua = parse_install_lua(data)
            if self.install_lua['ids']:
                self.sources.append({'source': 'install_lua', 'member': member.path, 'firmware': firmware,
                                     'ids': unique(self.install_lua['ids'])})

    # --- assembling the package ---

    def image_partition(self, image, name, start=None, end=None):
        header = image['header']
        start = header['start'] if start is None else start
        end = header['end'] if end is None else end
        return {
            'name': name,
            'image': image['member'],
            'start': hex_address(start),
            'end': hex_address(end),
            'size': end - start,
            'image_size': header['size'],
            'image_name': header['name'] or None,
            'image_type': header['type'],
            'filesystem': image['filesystem'],
            'compression': header['compression'],
            'built': header['built'],
            'upgraded': True,
        }

    def table_partition(self, row, image=None):
        if image:
            partition = self.image_partition(image, row['name'], row['start'], row['end'])
            partition['filesystem'] = partition['filesystem'] or row.get('filesystem')
        else:
            partition = {'name': row['name'], 'image': None, 'start': hex_address(row['start']),
                         'end': hex_address(row['end']), 'size': row['end'] - row['start'], 'image_size': None,
                         'image_name': None, 'image_type': None, 'filesystem': row.get('filesystem'),
                         'compression': None, 'built': None, 'upgraded': False}
        for key in ('read_only', 'backup_start'):
            if key in row:
                partition[key] = hex_address(row[key]) if key == 'backup_start' else row[key]
        return partition

    def flash_images(self):
        """Images with a usable partition range in their header (check.img and the like have 0-0)."""
        return [image for image in self.images if image['header']['end'] > image['header']['start']]

    def burn_targets(self):
        burns = dict(self.install['burns']) if self.install else {}
        if self.install_lua:
            burns.update(self.install_lua['updates'])
        return burns

    def partitions(self):
        images = self.flash_images()
        burns = self.burn_targets()
        by_name = {os.path.basename(image['member']): image for image in images}

        lua_table = self.install_lua['partitions'] if self.install_lua else []
        if len(lua_table) >= 2:
            source, rows = 'install_lua', lua_table
        elif len(self.table) >= 2:
            source, rows = 'partition_table', self.table
        else:
            source, rows = ('image_headers', None) if images else (None, None)

        if rows is None:
            partitions = [self.image_partition(image, burns.get(os.path.basename(image['member']))
                                               or partition_name_from_member(image['member'])) for image in images]
            return source, sorted(partitions, key=lambda p: int(p['start'], 16)), []

        assigned = {}
        for image_name, partition_name in burns.items():
            image = by_name.get(image_name)
            row = next((r for r in rows if r['name'].lower() == partition_name.lower()), None)
            if image and row and row['name'] not in assigned:
                assigned[row['name']] = image
        for image in images:
            if image in assigned.values():
                continue
            start = image['header']['start']
            for candidate in (start, start - RECORDER_FLASH_BASE):
                row = next((r for r in rows if r['start'] == candidate and r['name'] not in assigned), None)
                if row:
                    assigned[row['name']] = image
                    break
        partitions = [self.table_partition(row, assigned.get(row['name'])) for row in rows]
        unmapped = [image['member'] for image in images if image not in assigned.values()]
        return source, sorted(partitions, key=lambda p: int(p['start'], 16)), unmapped

    def security(self):
        signed = any(member.name == 'sign.img' for member in self.members)
        # Filesystem and kernel images: boot code and device trees often stay readable in encrypted firmwares
        content = {image['member'] for image in self.flash_images()
                   if not BOOT_MEMBER.search(os.path.basename(image['member']))
                   and not DTB_MEMBER.search(os.path.basename(image['member']))
                   and not PARTITION_MEMBER.search(os.path.basename(image['member']))
                   and os.path.basename(image['member']) not in METADATA_MEMBERS}
        checked = [kinds for path, kinds in self.payload_kinds.items() if path in content]
        if not checked:
            encrypted = None
        elif all(kind in KNOWN_PAYLOADS for kind, _ in checked):
            encrypted = False
        elif all(kind is None and ent > 7.0 for kind, ent in checked):
            encrypted = True
        else:
            encrypted = None
        checks = {}
        for source in (self.check, self.install):
            if source:
                checks.update(source['security'])
        lua = self.install_lua or {}
        baseline = lua.get('security_baseline') or (self.check or {}).get('security_baseline')
        result = {
            'security_baseline': baseline,
            'upgrade_security_version': lua.get('upgrade_security_version'),
            'signed': signed,
            'encrypted': encrypted,
        }
        if checks:
            result['checks'] = {k: v for k, v in checks.items()}
        if lua.get('checks'):
            result['script_checks'] = lua['checks']
        return result

    def has_content(self):
        return bool(self.sources or self.uboot or self.images or self.install or self.install_lua or self.check)

    def package(self):
        source, partitions, unmapped = self.partitions()
        self.evidence.sort(key=lambda e: e['_rank'])
        architectures = Counter(image['header']['arch'] for image in self.flash_images() or self.images
                                if image['header']['arch'])
        built = sorted(image['header']['built'] for image in self.images if image['header']['built'])
        oem_vendor = None
        if self.install and self.install['vendor']:
            oem_vendor = self.install['vendor']
        lua_vendors = [v for v in (self.install_lua or {}).get('vendor_names', []) if v]
        locale = None
        if self.check and (self.check['default_language'] or self.check['video_standard']
                           or self.check['supported_languages']):
            locale = {'default_language': self.check['default_language'],
                      'video_standard': self.check['video_standard'],
                      'supported_languages': self.check['supported_languages']}
        flash_size = None
        if source in ('install_lua', 'partition_table') and partitions:
            # Recorders' tables can be memory-mapped (0xa0000000-based) too
            lowest = min(int(p['start'], 16) for p in partitions)
            base = RECORDER_FLASH_BASE if lowest >= RECORDER_FLASH_BASE else 0
            flash_size = max(int(p['end'], 16) for p in partitions) - base
        package = {
            'firmware': self.firmware,
            'package_format': self.package_format,
            'architecture': architectures.most_common(1)[0][0] if architectures else None,
            'soc': choose_soc(self.evidence),
            'partition_source': source,
            'partitions': partitions,
            'flash_size': flash_size,
            'build_dates': {'earliest': built[0], 'latest': built[-1]} if built else None,
            'kernel_version': self.kernel_version,
            'bootloader_version': self.bootloader_version,
            'oem_vendor': oem_vendor,
            'security': self.security(),
            'locale': locale,
        }
        if source == 'partition_table':
            package['partition_table'] = self.table_name
        if self.table_variants:
            package['partition_table_variants'] = self.table_variants
        if unmapped:
            package['unmapped_images'] = unmapped
        if lua_vendors:
            package['oem_vendor_checks'] = lua_vendors
        if self.check and self.check.get('flash_type'):
            package['flash_type'] = self.check['flash_type']
        if self.check and self.check.get('flash_size'):
            package['flash_size_declared'] = self.check['flash_size']
        if self.install and self.install.get('software_version'):
            package['software_version'] = self.install['software_version']
        if self.install and self.install.get('market_area'):
            package['market_area'] = self.install['market_area']
        return package


def unique(values):
    return list(dict.fromkeys(v for v in values if v))


# Within one folder of one firmware, only the most specific list counts towards hardware_ids: an exact hwid list
# (e.g. IPC-HFW2541SP-S) beats Install's board family (IPC-HX3XXX, which Dahua puts in IPC-HX2XXX firmwares too),
# which beats Install.lua's board and vendor names
SOURCE_PRECEDENCE = (('hwid', 'check_img'), ('install',), ('install_lua',), ('uboot',))


def mark_counted_sources(sources):
    """Set "counted" on each hardware source: whether its IDs are in hardware_ids. All sources stay listed."""
    groups = {}
    for source in sources:
        groups.setdefault((source['firmware'], os.path.dirname(source['member'])), []).append(source)
    for group in groups.values():
        for kinds in SOURCE_PRECEDENCE:
            if any(source['ids'] for source in group if source['source'] in kinds):
                break
        else:
            kinds = ()
        for source in group:
            source['counted'] = source['source'] in kinds and bool(source['ids'])


def combine(readers, package_format):
    """The analysis fields from every package of a firmware: hardware_ids (union), hardware_sources and packages.
    u-boot board names only count when no package lists hardware any other way."""
    sources = [s for reader in readers for s in reader.sources]
    if not any(s['ids'] for s in sources):
        sources += [s for reader in readers for s in reader.uboot]
    mark_counted_sources(sources)
    hardware_ids = sorted({i for s in sources if s['counted'] for i in s['ids']})
    packages = [reader.package() for reader in readers if reader.has_content()]
    return {'hardware_ids': hardware_ids, 'hardware_sources': sources, 'packages': packages,
            'package_format': package_format}
