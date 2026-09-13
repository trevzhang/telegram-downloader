"""日志：控制台 + 按天轮转文件。"""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
BACKUP_DAYS = 14
LOG_DIR_NAME = "logs"
LOG_FILE_NAME = "tgdl.log"
# 该记录器在服务器/代理回收空闲连接时打 "Server closed the connection" 告警，Telethon 会自动重连，属于噪音
TELETHON_CONNECTION_LOGGER = "telethon.network.connection.connection"


def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = TimedRotatingFileHandler(
        log_dir / LOG_FILE_NAME, when="midnight", backupCount=BACKUP_DAYS, encoding="utf-8"
    )
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, handlers=[logging.StreamHandler(), file_handler])
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger(TELETHON_CONNECTION_LOGGER).setLevel(logging.ERROR)
