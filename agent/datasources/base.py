"""Connector 基类与注册表。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


class DataSourceError(RuntimeError):
    """数据源连接错误(网络/鉴权/参数)。"""


@dataclass
class DataSourceItem:
    """统一的数据源条目;字段在所有 connector 中保持一致。"""

    id: str
    title: str
    source: str          # "zotero" | "obsidian" | "notion" | "institutional"
    url: str = ""
    snippet: str = ""
    authors: List[str] = field(default_factory=list)
    year: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "title": self.title, "source": self.source,
            "url": self.url, "snippet": self.snippet,
            "authors": list(self.authors), "year": self.year,
            "extra": dict(self.extra),
        }


@dataclass
class FetchResult:
    target: str
    body: str
    format: str = "markdown"
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"target": self.target, "body": self.body,
                "format": self.format, "meta": dict(self.meta)}


class DataSource:
    """所有外部数据源 connector 的基类。"""

    id: str = "base"
    name: str = "数据源"
    blurb: str = ""
    auth_kind: str = "none"   # "none" | "api_key" | "oauth" | "file_path"
    config_keys: List[str] = []  # 期望用户在 settings 里提供的字段

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = dict(config or {})

    # ---- 4 个统一接口 ----
    def list_targets(self) -> List[Dict[str, Any]]:  # type: ignore[override]
        """可发现的目标清单(库、Vault、Notion 库 等)。"""
        return []

    def search(self, query: str, limit: int = 10) -> List[DataSourceItem]:
        return []

    def fetch(self, target: str) -> FetchResult:
        raise DataSourceError(f"{self.name} 暂不支持 fetch")

    def health(self) -> Dict[str, Any]:
        """轻量 ping;返回 ``{ok, latency_ms, error?, info}``。"""
        return {"ok": True, "latency_ms": 0, "info": "无网络调用"}

    # ---- 工具 ----
    def require_config(self, *keys: str) -> None:
        missing = [k for k in keys if not self.config.get(k)]
        if missing:
            raise DataSourceError(
                f"{self.name} 缺少必要配置: {', '.join(missing)};"
                f" 可在「设置 → 外部数据源」中填写。")

    @staticmethod
    def env_or_config(config: Dict[str, Any], key: str,
                      env_var: Optional[str] = None) -> Optional[str]:
        if config.get(key):
            return str(config[key])
        env = env_var or key.upper()
        return os.environ.get(env) or None


# ============================== 注册表 ==============================

_REGISTRY: Dict[str, type] = {}


def register(cls: type) -> type:
    """connector 类装饰器:自动加入注册表。"""
    if not issubclass(cls, DataSource):
        raise TypeError(f"{cls.__name__} 必须继承 DataSource")
    _REGISTRY[cls.id] = cls
    return cls


def list_connectors() -> List[Dict[str, Any]]:
    """返回所有可用 connector 的元信息(供 UI/测试)。"""
    out = []
    for cid, cls in _REGISTRY.items():
        out.append({
            "id": cid, "name": cls.name, "blurb": cls.blurb,
            "auth_kind": cls.auth_kind, "config_keys": list(cls.config_keys),
        })
    return out


def get_connector(connector_id: str,
                  config: Optional[Dict[str, Any]] = None) -> DataSource:
    cls = _REGISTRY.get(connector_id)
    if cls is None:
        raise DataSourceError(f"未注册的数据源: {connector_id}")
    return cls(config=config)
