"""Zotero Web API v3 — 读取用户文献库。

文档: https://www.zotero.org/support/dev/web_api/v3/start
只读;需要用户在 connector config 里提供:
  - ``api_key``: Zotero Web API key (Settings → Feeds/API)
  - ``user_id`` 或 ``group_id``: 数字 ID
  - ``library_type``: "user" (默认) 或 "group"
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .base import DataSource, DataSourceError, DataSourceItem, FetchResult, register


@register
class ZoteroConnector(DataSource):
    id = "zotero"
    name = "Zotero 个人/小组文献库"
    blurb = "通过 Zotero Web API 检索并读取用户或小组公开/私有文献库。"
    auth_kind = "api_key"
    config_keys = ["api_key", "user_id", "library_type"]

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self.api_key = self.env_or_config(self.config, "api_key",
                                          "ZOTERO_API_KEY")
        self.user_id = self.env_or_config(self.config, "user_id",
                                          "ZOTERO_USER_ID")
        self.library_type = (self.config.get("library_type") or "user").strip()
        self.base_url = f"https://api.zotero.org/{self.library_type}s"

    def _headers(self) -> Dict[str, str]:
        if not self.api_key:
            return {}
        return {"Zotero-API-Key": self.api_key,
                "Zotero-API-Version": "3"}

    def _request(self, path: str, params: Optional[Dict[str, Any]] = None,
                 timeout: float = 10.0) -> Dict[str, Any]:
        if not self.api_key or not self.user_id:
            self.require_config("api_key", "user_id")
        url = f"{self.base_url}/{self.user_id}{path}"
        if params:
            url = f"{url}?{urlencode({k: v for k, v in params.items() if v})}"
        req = Request(url, headers=self._headers())
        try:
            with urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as err:
            raise DataSourceError(
                f"Zotero API 返回 {err.code}: {err.reason}") from err
        except URLError as err:
            raise DataSourceError(f"Zotero 连接失败: {err.reason}") from err

    def list_targets(self) -> List[Dict[str, Any]]:
        """列出顶层 collection 作为可发现目标。"""
        if not self.api_key or not self.user_id:
            return []
        try:
            data = self._request("/collections")
        except DataSourceError:
            return []
        items = []
        for col in (data or [])[:50]:
            items.append({
                "id": f"collection:{col.get('key')}",
                "title": col.get("data", {}).get("name", "(未命名)"),
                "kind": "collection",
            })
        return items

    def search(self, query: str, limit: int = 10) -> List[DataSourceItem]:
        if not query:
            return []
        items = self._request("/items/top",
                              params={"q": query, "limit": max(1, min(50, limit)),
                                      "format": "json"}) or []
        results: List[DataSourceItem] = []
        for raw in items[:limit]:
            data = raw.get("data", {}) or {}
            title = data.get("title") or "(无标题)"
            key = raw.get("key", "")
            url = data.get("url") or (raw.get("links", {})
                                      .get("alternate", {})
                                      .get("href", ""))
            authors = []
            for creator in (data.get("creators") or []):
                name = " ".join(
                    filter(None, [creator.get("firstName"),
                                  creator.get("lastName")])).strip()
                if name:
                    authors.append(name)
            results.append(DataSourceItem(
                id=f"zotero:{key}", title=title, source="zotero",
                url=url, snippet=(data.get("abstractNote") or "")[:280],
                authors=authors, year=str(data.get("date", "")).split("-")[0]
                if data.get("date") else None,
                extra={"item_type": data.get("itemType", "")},
            ))
        return results

    def fetch(self, target: str) -> FetchResult:
        if not target.startswith("zotero:"):
            raise DataSourceError("Zotero target 必须以 'zotero:' 开头")
        key = target.split(":", 1)[1]
        item = self._request(f"/items/{key}")
        data = (item or {}).get("data", {}) or {}
        title = data.get("title", "(无标题)")
        body_lines = [f"# {title}", ""]
        for k in ("abstractNote", "publicationTitle", "date", "DOI", "url"):
            v = data.get(k) or ""
            if v:
                body_lines.append(f"- **{k}**: {v}")
        notes = data.get("note") or ""
        if notes:
            body_lines.append("", "## 笔记", notes)
        return FetchResult(target=target, body="\n".join(body_lines),
                           format="markdown", meta={"source": "zotero"})

    def health(self) -> Dict[str, Any]:
        started = time.monotonic()
        if not self.api_key or not self.user_id:
            return {"ok": False, "latency_ms": 0,
                    "error": "缺少 api_key / user_id"}
        try:
            self._request("/items/top", params={"limit": 1})
            return {"ok": True,
                    "latency_ms": round((time.monotonic() - started) * 1000, 1)}
        except DataSourceError as err:
            return {"ok": False, "latency_ms": round(
                (time.monotonic() - started) * 1000, 1), "error": str(err)}
