from cachy_migrator.core import (
    Destination,
    canonical_json,
    classify_user_data,
    plan_copy_operations,
    plan_hash,
)


def test_plan_hash_is_stable():
    a = {"b": 1, "a": 2}
    b = {"a": 2, "b": 1}
    assert canonical_json(a) == canonical_json(b)
    assert plan_hash(a) == plan_hash(b)


def test_classify_user_data_excludes_system_artifacts(tmp_path):
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Documents" / "resume.pdf").write_bytes(b"x" * 10)
    (tmp_path / "Windows").mkdir()
    (tmp_path / "Windows" / "system32.dll").write_bytes(b"x" * 10)

    items = classify_user_data([tmp_path])
    by_name = {item.relative_path: item for item in items}

    assert by_name["Documents"].category == "documents"
    assert by_name["Documents"].bytes == 10
    assert by_name["Windows"].excluded is True
    assert by_name["Windows"].reason == "system or OS artifact"


def test_plan_copy_operations_places_largest_items_by_capacity():
    small = Destination(path="/mnt/small", free_bytes=20, usable_bytes=20)
    large = Destination(path="/mnt/large", free_bytes=100, usable_bytes=100)
    items = [
        _item("/src/videos", "Videos", "videos", 80),
        _item("/src/docs", "Documents", "documents", 15),
    ]

    operations, warnings = plan_copy_operations(items, [large, small])

    assert warnings == []
    assert operations[0]["destination"].startswith("/mnt/large/")
    assert operations[1]["destination"].startswith("/mnt/small/")


def test_plan_copy_operations_warns_when_capacity_is_insufficient():
    destination = Destination(path="/mnt/dest", free_bytes=10, usable_bytes=10)
    operations, warnings = plan_copy_operations([_item("/src/big", "Big", "other", 11)], [destination])

    assert operations == []
    assert "No destination has enough usable free space" in warnings[0]


def _item(source, relative_path, category, size):
    from cachy_migrator.core import UserDataItem

    return UserDataItem(
        source=source,
        relative_path=relative_path,
        category=category,
        bytes=size,
        files=1,
    )
