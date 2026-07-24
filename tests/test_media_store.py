import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from momo_camera_hub.media import MediaNotFoundError, MediaStore

NOW = datetime(2026, 7, 24, 12, 30, 15, tzinfo=UTC)


def test_allocate_snapshot_uses_uuid_and_utc_date(tmp_path: Path) -> None:
    store = MediaStore(tmp_path)

    item = store.allocate("snapshot", NOW)

    assert item.kind == "snapshot"
    assert item.content_path.parent == tmp_path / "snapshots" / "2026-07-24"
    assert item.content_path.name.startswith("snapshot_20260724T123015_")
    assert item.content_path.suffix == ".jpg"
    assert ".." not in str(item.content_path)


def test_finalize_writes_atomic_metadata_and_lists_newest_first(tmp_path: Path) -> None:
    store = MediaStore(tmp_path)
    old = store.allocate("snapshot", NOW)
    old.content_path.parent.mkdir(parents=True)
    old.content_path.write_bytes(b"jpeg")
    store.finalize(old, size_bytes=4)
    new = store.allocate("recording", NOW.replace(minute=31))
    new.content_path.parent.mkdir(parents=True)
    new.content_path.write_bytes(b"mp4")
    store.finalize(new, size_bytes=3, duration_sec=12.5)

    page = store.list_media(limit=10)

    assert [item.id for item in page.items] == [new.id, old.id]
    payload = json.loads(new.metadata_path.read_text(encoding="utf-8"))
    assert payload["duration_sec"] == 12.5
    assert not list(tmp_path.rglob("*.tmp"))


def test_media_id_never_resolves_arbitrary_path(tmp_path: Path) -> None:
    store = MediaStore(tmp_path)

    with pytest.raises(MediaNotFoundError):
        store.get("../../etc/passwd")


def test_list_filters_by_type_date_and_cursor(tmp_path: Path) -> None:
    store = MediaStore(tmp_path)
    created = []
    for offset, kind in enumerate(("snapshot", "recording", "snapshot")):
        item = store.allocate(kind, NOW.replace(second=NOW.second + offset))
        item.content_path.parent.mkdir(parents=True, exist_ok=True)
        item.content_path.write_bytes(kind.encode())
        store.finalize(item, size_bytes=len(kind))
        created.append(item)

    first = store.list_media(kind="snapshot", date="2026-07-24", limit=1)
    second = store.list_media(kind="snapshot", date="2026-07-24", limit=1, cursor=first.next_cursor)

    assert [item.id for item in first.items] == [created[2].id]
    assert [item.id for item in second.items] == [created[0].id]
