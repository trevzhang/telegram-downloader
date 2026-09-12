import pytest

from tgdl.link_parser import LinkParseError, parse_link
from tgdl.models import ChannelRef


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://t.me/somechan", ChannelRef(username="somechan")),
        ("http://t.me/somechan/", ChannelRef(username="somechan")),
        ("t.me/somechan/123", ChannelRef(username="somechan", message_id=123)),
        ("https://t.me/somechan/123?single", ChannelRef(username="somechan", message_id=123)),
        ("https://telegram.me/some_chan", ChannelRef(username="some_chan")),
        ("@somechan", ChannelRef(username="somechan")),
        ("https://t.me/c/1234567890/55", ChannelRef(channel_id=1234567890, message_id=55)),
        ("https://t.me/c/1234567890", ChannelRef(channel_id=1234567890)),
        ("https://t.me/+AbCdEf_-123", ChannelRef(invite_hash="AbCdEf_-123")),
        ("https://t.me/joinchat/AbCdEf", ChannelRef(invite_hash="AbCdEf")),
    ],
)
def test_parse_valid_links(raw: str, expected: ChannelRef) -> None:
    assert parse_link(raw) == expected


@pytest.mark.parametrize("raw", ["", "hello", "https://example.com/x", "https://t.me/", "https://t.me/ab"])
def test_parse_invalid_links(raw: str) -> None:
    with pytest.raises(LinkParseError):
        parse_link(raw)
