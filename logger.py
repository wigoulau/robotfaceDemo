"""
统一日志模块
提供带时间戳、级别分层的日志输出。所有模块通过 get_logger(name) 获取。

用法:
    from logger import get_logger
    log = get_logger(__name__)
    log.info("消息")
    log.warning("警告")
    log.error("错误")
    log.debug("调试")
"""

import logging
import sys

# 全局配置标志
_configured = False

# 日志格式: 时:分:秒.毫秒 [级别] (模块) 消息
_FORMAT = "%(asctime)s.%(msecs)03d [%(levelname)-5s] (%(name)s) %(message)s"
_DATE_FMT = "%H:%M:%S"


def _setup_root():
    """配置根 logger（仅首次调用生效）"""
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # 控制台 handler（INFO及以上）
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG)
    console.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATE_FMT))
    root.addHandler(console)

    # 抑制第三方库的冗余日志
    for noisy in ("PIL", "matplotlib", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name):
    """
    获取指定名称的 logger。
    name: 通常传 __name__ 即可。
    """
    _setup_root()
    return logging.getLogger(name)


def enable_debug(name=None):
    """
    开启 DEBUG 级别输出（可指定模块名，不指定则全部开启）。
    """
    _setup_root()
    if name:
        logging.getLogger(name).setLevel(logging.DEBUG)
    else:
        logging.getLogger().handlers[0].setLevel(logging.DEBUG)


def add_file_handler(filepath, level=logging.DEBUG):
    """追加文件日志输出"""
    _setup_root()
    fh = logging.FileHandler(filepath, encoding="utf-8")
    fh.setLevel(level)
    fh.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATE_FMT))
    logging.getLogger().addHandler(fh)
