"""轻量配置加载：读取项目根目录的 .env（不引入第三方依赖）。

优先级：进程环境变量 > .env 文件 > 默认值。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
# Electron 打包后代码位于只读 resources/，配置应保存在用户数据目录。
_CONFIG_DIR = Path(os.environ.get("PAPER_STUDIO_CONFIG_DIR") or _PROJECT_ROOT)


def _parse_dotenv(path: Path) -> Dict[str, str]:
    """解析 .env 文件（支持 KEY=VALUE 与注释/空行）。"""
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            values[key] = val
    return values


_cache: Optional[Dict[str, str]] = None


def load_dotenv() -> Dict[str, str]:
    """加载 .env（带缓存，只读一次）。"""
    global _cache
    if _cache is None:
        _cache = _parse_dotenv(_CONFIG_DIR / ".env")
    return _cache


def get(key: str, default: Optional[str] = None) -> Optional[str]:
    """读取配置：进程环境变量优先，其次 .env，最后默认值。"""
    return os.environ.get(key) or load_dotenv().get(key) or default


def get_int(key: str, default: int) -> int:
    val = get(key)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def get_bool(key: str, default: bool = False) -> bool:
    val = get(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")
