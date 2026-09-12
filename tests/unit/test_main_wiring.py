from pathlib import Path

from tgdl.config import Settings
from tgdl.main import build_worker_config, session_paths


def _settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, api_id=1, api_hash="h", bot_token="t", owner_id=1,
                    data_dir=tmp_path / "data", download_dir=tmp_path / "dl", concurrency=4)


def test_session_paths_under_data_dir(tmp_path: Path) -> None:
    user, bot = session_paths(_settings(tmp_path))
    assert user == str(tmp_path / "data" / "user")
    assert bot == str(tmp_path / "data" / "bot")


def test_build_worker_config(tmp_path: Path) -> None:
    config = build_worker_config(_settings(tmp_path))
    assert config.download_dir == tmp_path / "dl"
    assert config.concurrency == 4
