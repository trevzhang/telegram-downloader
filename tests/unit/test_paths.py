from datetime import UTC, datetime
from pathlib import Path

import pytest

from tgdl.models import MediaItem, MediaKind
from tgdl.paths import channel_dir_name, cleanup_parts, part_path, sanitize_filename, target_path


class _Entity:
    def __init__(self, id: int, title: str | None = None, username: str | None = None) -> None:
        self.id, self.title, self.username = id, title, username


def test_sanitize_replaces_invalid_chars() -> None:
    assert sanitize_filename('a/b:c*d?e"f<g>h|i.mp4') == "a_b_c_d_e_f_g_h_i.mp4"


def test_sanitize_strips_dots_and_spaces() -> None:
    assert sanitize_filename("  ..name.. ") == "name"


def test_sanitize_empty_becomes_file() -> None:
    assert sanitize_filename("///") == "file"


def test_sanitize_only_invalid_chars_and_dots_becomes_file() -> None:
    assert sanitize_filename("/ . /") == "file"


def test_sanitize_keeps_literal_underscores() -> None:
    assert sanitize_filename("___") == "___"
    assert sanitize_filename("_private.mp4") == "_private.mp4"


def test_sanitize_truncates_long_name_keeping_ext() -> None:
    name = sanitize_filename("x" * 300 + ".mp4")
    assert name.endswith(".mp4")
    assert len(name) <= 120


def test_channel_dir_name_prefers_title_then_username_then_id() -> None:
    assert channel_dir_name(_Entity(1, "众乐乐内部群 (满血版)", "user_name")) == "众乐乐内部群 (满血版)"
    assert channel_dir_name(_Entity(1, "My Chan/2")) == "My Chan_2"
    assert channel_dir_name(_Entity(1, None, "user_name")) == "user_name"
    assert channel_dir_name(_Entity(99)) == "99"


def test_target_path_layout() -> None:
    item = MediaItem(
        message_id=42, date=datetime(2026, 3, 5, tzinfo=UTC), kind=MediaKind.VIDEO, file_name="v.mp4", size=1
    )
    assert target_path(Path("dl"), "chan", item) == Path("dl/chan/2026_03/42_v.mp4")


def test_part_path() -> None:
    assert part_path(Path("a/b.mp4")) == Path("a/b.mp4.part")


def test_cleanup_parts_removes_only_part_files(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "x.mp4.part").write_bytes(b"1")
    (tmp_path / "y.jpg").write_bytes(b"1")
    assert cleanup_parts(tmp_path) == 1
    assert not (tmp_path / "sub" / "x.mp4.part").exists()
    assert (tmp_path / "y.jpg").exists()


def test_cleanup_parts_missing_root(tmp_path: Path) -> None:
    assert cleanup_parts(tmp_path / "nope") == 0


def test_sanitize_truncates_by_bytes_keeping_ext() -> None:
    for count in (100, 150):
        name = sanitize_filename("视" * count + ".mp4")
        assert name.endswith(".mp4")
        assert len(name.encode("utf-8")) <= 200
        assert len(name) <= 120


def test_sanitize_truncation_strips_trailing_dots_and_spaces() -> None:
    name = sanitize_filename("x" * 115 + " .." + "y" * 10 + ".mp4")
    assert name.endswith(".mp4")
    assert not name[:-4].endswith((" ", "."))


def test_sanitize_replaces_delete_char() -> None:
    assert sanitize_filename("a\x7fb.mp4") == "a_b.mp4"


def test_target_path_sanitizes_file_name() -> None:
    item = MediaItem(
        message_id=42, date=datetime(2026, 3, 5, tzinfo=UTC), kind=MediaKind.VIDEO, file_name="../evil.mp4", size=1
    )
    path = target_path(Path("dl"), "chan", item)
    assert ".." not in path.parts
    assert path.parent == Path("dl/chan/2026_03")
    assert path.name.startswith("42_") and path.name.endswith("evil.mp4")


def test_ensure_download_root_rejects_missing_dir(tmp_path: Path) -> None:
    from tgdl.paths import DownloadDirMissingError, ensure_download_root

    ensure_download_root(tmp_path)
    with pytest.raises(DownloadDirMissingError, match="NAS"):
        ensure_download_root(tmp_path / "gone")
    assert not (tmp_path / "gone").exists()
