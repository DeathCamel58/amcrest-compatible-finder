import io
import os
import sys
import zipfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path, monkeypatch):
    """Run every test from an empty temp directory, so nothing can touch the real firmware/ folder or JSONs."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def make_zip_bytes(members):
    """A zip (as bytes) holding {name: content}."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def to_dh_variant(data):
    """Dahua's .bin firmwares: local file headers use "DH" instead of "PK"; the central directory and end record
    keep "PK"."""
    return data.replace(b"PK\x03\x04", b"DH\x03\x04")


@pytest.fixture
def write_file(tmp_path):
    def write(name, data):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)
    return write
