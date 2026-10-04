from util import archive


def record(**extra):
    return {"file_name": "DH_IPC-X_V2.bin", "platform": "dahua", "vendors": ["Amcrest"], "camera_name": ["IP4M"],
            "hardware": {}, "aliases": [], **extra}


def test_new_items_get_rights_and_collection():
    metadata = archive.get_metadata(record())
    assert metadata["collection"] == "opensource_media"
    assert metadata["mediatype"] == "data"
    assert "Dahua" in metadata["rights"] and "Amcrest" in metadata["rights"]
    assert "No license is granted" in metadata["rights"]


def test_hikvision_rights_name_hikvision():
    assert "Hikvision" in archive.get_metadata(record(platform="hikvision"))["rights"]


def test_refresh_never_changes_collection_or_mediatype(monkeypatch):
    sent = {}

    class Response:
        status_code = 200
        text = ""

    class Item:
        def modify_metadata(self, metadata):
            sent.update(metadata)
            return Response()

    monkeypatch.setattr(archive.internetarchive, "get_item", lambda identifier: Item())
    monkeypatch.setattr(archive, "upload_record", lambda identifier, record: None)
    monkeypatch.setattr(archive, "get_record", lambda *args: record())
    archive.refresh_archive("DH_IPC-X_V2.bin", {}, {}, {}, "0" * 32, 10)
    assert "rights" in sent
    assert "collection" not in sent and "mediatype" not in sent


def test_metadata_version_is_part_of_the_record_hash(monkeypatch):
    before = archive.get_record_hash(record())
    monkeypatch.setattr(archive, "METADATA_VERSION", archive.METADATA_VERSION + 1)
    assert archive.get_record_hash(record()) != before
