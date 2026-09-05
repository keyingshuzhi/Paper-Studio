"""Paper Studio 外部数据源连接器(Read-Only)。

每个 connector 提供 3 个统一方法:
- ``list_targets()``  → 可发现的资源(文献库、Notion 库、机构库、Vault 等)
- ``search(query, limit)`` → 元数据检索
- ``fetch(target)`` → 读全文(由 LLM 通过 MCP 调用)

本期为「只读、不联动 Library」的轻集成;通过 MCP Client 暴露为 Tool。
"""

from .base import (
    DataSource,
    DataSourceError,
    DataSourceItem,
    FetchResult,
    list_connectors,
    get_connector,
)
from .zotero import ZoteroConnector
from .obsidian import ObsidianConnector
from .notion import NotionConnector
from .institutional import InstitutionalConnector

__all__ = [
    "DataSource", "DataSourceError", "DataSourceItem", "FetchResult",
    "list_connectors", "get_connector",
    "ZoteroConnector", "ObsidianConnector", "NotionConnector",
    "InstitutionalConnector",
]
