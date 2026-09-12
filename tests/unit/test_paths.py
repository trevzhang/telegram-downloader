from datetime import datetime, timezone
from pathlib import Path

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


def test_channel_dir_name_prefers_username_then_title_then_id() -> None:
    assert channel_dir_name(_Entity(1, "标题", "user_name")) == "user_name"
    assert channel_dir_name(_Entity(1, "My Chan/2")) == "My Chan_2"
    assert channel_dir_name(_Entity(99)) == "99"


def test_target_path_layout() -> None:
    item = MediaItem(message_id=42, date=datetime(2026, 3, 5, tzinfo=timezone.utc),
                     kind=MediaKind.VIDEO, file_name="v.mp4", size=1)
    assert target_path(Path("dl"), "chan", item) == Path("dl/chan/2026-03/42_v.mp4")


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
