import pytest

from tgdl.bot.commands import HELP_TEXT, CommandError, parse_cancel, parse_download, split_command


def test_split_command_strips_bot_suffix_and_keeps_rest_verbatim() -> None:
    assert split_command("/Download@my_bot https://t.me/x 1 0  caption == r'.*a  b.*'") == (
        "/download",
        "https://t.me/x 1 0  caption == r'.*a  b.*'",
    )
    assert split_command("/tasks") == ("/tasks", "")


def test_split_command_treats_bare_link_as_download() -> None:
    assert split_command("https://t.me/somechannel/123") == ("/download", "https://t.me/somechannel/123")


def test_split_command_restores_em_dash() -> None:
    assert split_command("/dl https://t.me/x —regex a")[1] == "https://t.me/x --regex a"


def test_split_command_rejects_non_command() -> None:
    with pytest.raises(CommandError):
        split_command("hello")


def test_parse_download_whole_chat() -> None:
    spec = parse_download("https://t.me/somechannel 1 0")
    assert (spec.id_from, spec.id_to, spec.filter_expr) == (1, None, None)


def test_parse_download_range_and_filter() -> None:
    spec = parse_download("https://t.me/somechannel 100 200 media_type == 'video' and file_size > 10MB")
    assert (spec.id_from, spec.id_to) == (100, 200)
    assert spec.filter_expr == "media_type == 'video' and file_size > 10MB"


def test_parse_download_single_message_link() -> None:
    spec = parse_download("https://t.me/c/1234567890/50")
    assert spec.link.message_id == 50 and spec.id_from is None and spec.id_to is None


def test_parse_download_message_link_with_filter_starts_from_that_message() -> None:
    spec = parse_download("https://t.me/c/1234567890/50 caption == r'.*#饼干姐姐.*' and message_date >= 2026-05-10")
    assert spec.id_from == 50 and spec.id_to is None and spec.filter_expr is not None


def test_parse_download_message_link_with_explicit_range_uses_range() -> None:
    spec = parse_download("https://t.me/c/1234567890/50 1 0")
    assert (spec.id_from, spec.id_to) == (1, None)


def test_parse_download_filter_without_range() -> None:
    spec = parse_download("https://t.me/somechannel file_extension == r'(mp4|mkv)'")
    assert spec.id_from is None and spec.filter_expr == "file_extension == r'(mp4|mkv)'"


@pytest.mark.parametrize(
    "rest, hint",
    [
        ("", "用法"),
        ("nope", "链接"),
        ("https://t.me/somechannel 10", "同时给出"),
        ("https://t.me/somechannel 0 0", "≥ 1"),
        ("https://t.me/somechannel 20 10", "起始序号"),
        ("https://t.me/somechannel 1 0 caption == 1", "表达式"),
        ("https://t.me/somechannel 1 0 --regex a", "表达式"),
    ],
)
def test_parse_download_errors(rest: str, hint: str) -> None:
    with pytest.raises(CommandError, match=hint):
        parse_download(rest)


def test_parse_cancel() -> None:
    assert parse_cancel("3") == 3 and parse_cancel("#3") == 3
    for bad in ("", "x", "##3", "3 4", "²"):
        with pytest.raises(CommandError):
            parse_cancel(bad)


def test_help_mentions_download_and_filter() -> None:
    assert "/download" in HELP_TEXT and "r'.*关键词.*'" in HELP_TEXT
