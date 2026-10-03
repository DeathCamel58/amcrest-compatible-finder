import main
from util.restore_history import add_url_history, restore


def test_moved_file_keeps_old_url(monkeypatch):
    monkeypatch.setattr(main, "RUN_DATE", "2026-01-01")
    listing = {"vendor": "Rhino", "source": "s"}
    main.record_listing_url(listing, "http://x/dvr/a.zip")
    assert "url_history" not in listing
    listing.update(first_seen="2026-01-01", last_seen="2026-01-01")  # as merge_listing sets them
    monkeypatch.setattr(main, "RUN_DATE", "2026-02-01")
    main.record_listing_url(listing, "http://x/dvr/Dahua/a.zip")
    assert listing["url"] == "http://x/dvr/Dahua/a.zip"
    assert listing["url_history"] == [
        {"url": "http://x/dvr/a.zip", "first_seen": "2026-01-01", "last_seen": "2026-01-01"},
        {"url": "http://x/dvr/Dahua/a.zip", "first_seen": "2026-02-01", "last_seen": "2026-02-01"},
    ]


def test_url_history_from_a_listing_made_before_it_existed(monkeypatch):
    monkeypatch.setattr(main, "RUN_DATE", "2026-03-01")
    listing = {"url": "http://old/a.bin", "first_seen": "2025-01-01", "last_seen": "2025-06-01"}
    main.record_listing_url(listing, "http://new/a.bin")
    assert listing["url_history"][0] == {"url": "http://old/a.bin", "first_seen": "2025-01-01",
                                         "last_seen": "2025-06-01"}


def test_merge_listing_records_url_history(monkeypatch):
    monkeypatch.setattr(main, "RUN_DATE", "2026-01-01")
    firmware = {"vendor": "Amcrest", "source": "page", "firmware_notes": "", "firmware_latest": "https://s3/IP4M/a.bin"}
    listings = main.merge_listing([], firmware, "firmware_latest", ["IP4M"], "a.bin")
    listings = main.merge_listing(listings, {**firmware, "firmware_latest": "https://s3/IP5M/a.bin"},
                                  "firmware_latest", ["IP5M"], "a.bin")
    assert len(listings) == 1
    assert [item["url"] for item in listings[0]["url_history"]] == ["https://s3/IP4M/a.bin", "https://s3/IP5M/a.bin"]


def test_add_url_history_keeps_current_url():
    listing = {"url": "http://new/a.bin", "first_seen": "2026-01-01", "last_seen": "2026-02-01"}
    add_url_history(listing, "http://old/a.bin", "2024-05-01", "2025-01-01")
    assert listing["url"] == "http://new/a.bin"
    assert [item["url"] for item in listing["url_history"]] == ["http://old/a.bin", "http://new/a.bin"]


def test_restore_from_history():
    cameras = {
        "a.bin": {"url": "http://rhino/dvr/Dahua/a.bin", "vendors": ["Rhino"], "listings": [
            {"vendor": "Rhino", "source": "page", "url": "http://rhino/dvr/Dahua/a.bin", "kind": "vendor",
             "camera_name": [], "notes": []}]},
        "ClickHere": {"url": "ClickHere", "camera_name": ["X"], "notes": []},
    }
    versions = [
        ("2024-08-15", {"a.bin": {"url": "http://rhino/dvr/a.bin", "vendors": ["Rhino"], "md5": "a" * 32}}),
        ("2026-10-02", {"a.bin": {"listings": [{"vendor": "Rhino", "source": "page", "url": "http://rhino/dvr/a2.bin",
                                                "first_seen": "2025-01-01", "last_seen": "2026-10-02"}]},
                        "software.exe": {"url": "http://x/software.exe", "vendors": ["Dahua"]}}),
    ]
    removed = {}
    counts, unplaced = restore(cameras, versions, removed, files={"a.bin"})
    listing = cameras["a.bin"]["listings"][0]
    assert listing["url"] == "http://rhino/dvr/Dahua/a.bin"
    assert {item["url"] for item in listing["url_history"]} == {
        "http://rhino/dvr/a.bin", "http://rhino/dvr/a2.bin", "http://rhino/dvr/Dahua/a.bin"}
    assert listing["first_seen"] == "2024-08-15"
    assert cameras["a.bin"]["md5"] == "a" * 32
    assert "ClickHere" not in cameras and removed["ClickHere"]["removed_reason"].startswith("placeholder")
    assert removed["software.exe"]["vendors"] == ["Dahua"]
    assert unplaced == []


def test_placeholder_checksums_are_not_restored():
    cameras = {"a.bin": {"url": "http://x/a.bin", "listings": []}}
    restore(cameras, [("2025-01-01", {"a.bin": {"url": "http://x/a.bin", "md5": "0"}})], {}, files={"a.bin"})
    assert "md5" not in cameras["a.bin"]
