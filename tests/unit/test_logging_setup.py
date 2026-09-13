import logging
from pathlib import Path

from tgdl.logging_setup import TELETHON_CONNECTION_LOGGER, setup_logging


def test_setup_logging_silences_reconnect_warnings_only(tmp_path: Path) -> None:
    setup_logging(tmp_path)
    assert logging.getLogger("telethon").level == logging.WARNING
    assert logging.getLogger(TELETHON_CONNECTION_LOGGER).level == logging.ERROR
    assert logging.getLogger("telethon.client").getEffectiveLevel() == logging.WARNING
