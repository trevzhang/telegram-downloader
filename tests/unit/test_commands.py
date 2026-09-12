import pytest

from tgdl.bot.commands import CommandError, parse_cancel, parse_dl, split_command
from tgdl.models import MediaKind


def test_split_command_strips_bot_suffix() -> None:
    assert split_command("/dl@my_bot https://t.me/x --regex a") == ("/dl", ["https://t.me/x", "--regex", "a"])


def test_split_command_handles_quotes() -> None:
    assert split_command('/dl https://t.me/x --regex "a b"') == ("/dl", ["https://t.me/x", "--regex", "a b"])


def test_split_command_rejects_non_command() -> None:
    with pytest.raises(CommandError):
        split_command("hello")


def test_parse_dl_minimal() -> None:
    spec = parse_dl(["https://t.me/chan"])
    assert spec.link.username == "chan"
    assert spec.kind is MediaKind.ALL
    assert spec.regex is None


def test_parse_dl_full_date_range() -> None:
    spec = parse_dl(["https://t.me/chan", "--regex", "4k", "--from", "2026-01-01", "--to", "2026-02-01", "--type", "video"])
    assert spec.regex == "4k"
    assert spec.date_from is not None and spec.date_to is not None
    assert spec.date_from < spec.date_to
    assert spec.kind is MediaKind.VIDEO


def test_parse_dl_ids() -> None:
    spec = parse_dl(["https://t.me/chan", "--ids", "100-500"])
    assert (spec.id_from, spec.id_to) == (100, 500)


def test_parse_dl_bad_ids_format() -> None:
    with pytest.raises(CommandError, match="--ids 格式"):
        parse_dl(["https://t.me/chan", "--ids", "abc"])


def test_parse_dl_conflicting_ranges() -> None:
    with pytest.raises(CommandError, match="不能同时使用"):
        parse_dl(["https://t.me/chan", "--ids", "1-2", "--from", "2026-01-01"])


def test_parse_dl_bad_link() -> None:
    with pytest.raises(CommandError, match="无法识别的链接"):
        parse_dl(["not-a-link"])


def test_parse_dl_unknown_option() -> None:
    with pytest.raises(CommandError):
        parse_dl(["https://t.me/chan", "--bogus"])


def test_parse_dl_missing_link() -> None:
    with pytest.raises(CommandError):
        parse_dl([])


def test_parse_cancel() -> None:
    assert parse_cancel(["#3"]) == 3
    assert parse_cancel(["3"]) == 3
    with pytest.raises(CommandError, match="用法"):
        parse_cancel([])


def test_parse_dl_bad_type_choice() -> None:
    with pytest.raises(CommandError, match="--type"):
        parse_dl(["https://t.me/chan", "--type", "audio"])


def test_parse_dl_option_missing_value() -> None:
    with pytest.raises(CommandError, match="--regex"):
        parse_dl(["https://t.me/chan", "--regex"])


def test_split_command_keeps_unquoted_backslash() -> None:
    assert split_command("/dl https://t.me/x --regex ep\\d+") == ("/dl", ["https://t.me/x", "--regex", "ep\\d+"])


def test_split_command_unbalanced_quote() -> None:
    with pytest.raises(CommandError, match="引号不匹配"):
        split_command('/dl https://t.me/x --regex "abc')


@pytest.mark.parametrize("arg", ["²", "##3"])
def test_parse_cancel_rejects_non_ascii_or_double_hash(arg: str) -> None:
    with pytest.raises(CommandError, match="用法"):
        parse_cancel([arg])


def test_parse_dl_ids_rejects_non_ascii_digits() -> None:
    with pytest.raises(CommandError, match="--ids 格式"):
        parse_dl(["https://t.me/chan", "--ids", "٣-٥"])


@pytest.mark.parametrize("ids", ["0-5", "1-2147483648"])
def test_parse_dl_ids_out_of_bounds(ids: str) -> None:
    with pytest.raises(CommandError, match="1 到 2147483647"):
        parse_dl(["https://t.me/chan", "--ids", ids])


def test_parse_dl_ids_too_many_digits() -> None:
    with pytest.raises(CommandError):
        parse_dl(["https://t.me/chan", "--ids", "1-99999999999"])


def test_parse_dl_rejects_abbreviated_option() -> None:
    with pytest.raises(CommandError):
        parse_dl(["https://t.me/chan", "--fr", "2026-01-01"])


def test_parse_dl_empty_regex_is_none() -> None:
    assert parse_dl(["https://t.me/chan", "--regex", ""]).regex is None


def test_parse_dl_regex_starting_with_dash_via_equals() -> None:
    assert parse_dl(["https://t.me/chan", "--regex=-x"]).regex == "-x"
