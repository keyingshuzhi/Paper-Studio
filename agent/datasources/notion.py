"""Notion API v1 — 读取用户授权的页面/数据库。

文档: https://developers.notion.com/reference
需要 Integration Token(Internal Integration Secret)。
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .base import DataSource, DataSourceError, DataSourceItem, FetchResult, register

NOTION_VERSION = "2022-06-28"
ENDPOINT = "https://api.notion.com/v1"


@register
class NotionConnector(DataSource):
    id = "notion"
    name = "Notion 知识库"
    blurb = "通过 Notion Integration 搜索并读取页面/数据库内容。"
    auth_kind = "api_key"
    config_keys = ["integration_token"]

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self.token = self.env_or_config(
            self.config, "integration_token", "NOTION_TOKEN")

    def _request(self, method: str, path: str,
                 body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.token:
            self.require_config("integration_token")
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }
        data = json.dumps(body).encode("utf-8") if body else None
        req = Request(f"{ENDPOINT}{path}", data=data, method=method,
                      headers=headers)
        try:
            with urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as err:
            try:
                msg = err.read().decode("utf-8", errors="ignore")
            except Exception:
                msg = err.reason
            raise DataSourceError(f"Notion API {err.code}: {msg}") from err
        except URLError as err:
            raise DataSourceError(f"Notion 连接失败: {err.reason}") from err

    def list_targets(self) -> List[Dict[str, Any]]:
        if not self.token:
            return []
        try:
            data = self._request("POST", "/search",
                                 body={"filter": {"property": "object",
                                                   "value": "database"},
                                       "page_size": 20})
        except DataSourceError:
            return []
        items = []
        for row in (data or {}).get("results", [])[:20]:
            title = self._extract_title(row)
            items.append({
                "id": f"notion-db:{row.get('id')}",
                "title": title or "(无标题数据库)",
                "kind": "database",
            })
        return items

    def search(self, query: str, limit: int = 10) -> List[DataSourceItem]:
        if not query:
            return []
        try:
            data = self._request("POST", "/search",
                                 body={"query": query,
                                       "page_size": max(1, min(50, limit))})
        except DataSourceError:
            return []
        results: List[DataSourceItem] = []
        for row in (data or {}).get("results", [])[:limit]:
            title = self._extract_title(row) or "(无标题)"
            page_id = row.get("id", "")
            url = row.get("url", "")
            obj = row.get("object", "page")
            snippet = self._snippet_from_row(row)
            extra = {"object": obj}
            if row.get("last_edited_time"):
                extra["edited_at"] = row["last_edited_time"]
            results.append(DataSourceItem(
                id=f"notion:{page_id}", title=title, source="notion",
                url=url, snippet=snippet, extra=extra,
            ))
        return results

    def fetch(self, target: str) -> FetchResult:
        if not target.startswith("notion:"):
            raise DataSourceError("Notion target 必须以 'notion:' 开头")
        page_id = target.split(":", 1)[1]
        try:
            data = self._request("GET", f"/blocks/{page_id}/children",
                                 body=None)
        except DataSourceError as err:
            return FetchResult(target=target, body=str(err),
                               format="markdown", meta={"error": True})
        lines = []
        for block in (data or {}).get("results", []):
            text = self._block_to_markdown(block)
            if text:
                lines.append(text)
        return FetchResult(target=target, body="\n\n".join(lines),
                           format="markdown", meta={"source": "notion"})

    def health(self) -> Dict[str, Any]:
        started = time.monotonic()
        if not self.token:
            return {"ok": False, "latency_ms": 0,
                    "error": "缺少 integration_token"}
        try:
            self._request("POST", "/search", body={"page_size": 1})
            return {"ok": True, "latency_ms": round(
                (time.monotonic() - started) * 1000, 1)}
        except DataSourceError as err:
            return {"ok": False, "latency_ms": round(
                (time.monotonic() - started) * 1000, 1), "error": str(err)}

    # ---- helpers ----
    @staticmethod
    def _extract_title(row: Dict[str, Any]) -> Optional[str]:
        props = row.get("properties") or row.get("title")
        if isinstance(props, list):
            for t in props:
                if t.get("type") == "text" and t.get("text"):
                    return t["text"].get("content", "")
        if isinstance(props, dict):
            for value in props.values():
                if isinstance(value, dict):
                    if value.get("type") == "title":
                        arr = value.get("title") or []
                        if arr and isinstance(arr, list):
                            return "".join(
                                t.get("text", {}).get("content", "")
                                for t in arr)
        return None

    @staticmethod
    def _snippet_from_row(row: Dict[str, Any]) -> str:
        # 尝试 properties.description
        props = row.get("properties", {})
        if isinstance(props, dict):
            for value in props.values():
                if isinstance(value, dict) and value.get("type") == "rich_text":
                    arr = value.get("rich_text") or []
                    if arr and isinstance(arr, list):
                        text = "".join(t.get("text", {}).get("content", "")
                                       for t in arr)
                        if text:
                            return text[:280]
        return ""

    @staticmethod
    def _block_to_markdown(block: Dict[str, Any]) -> str:
        btype = block.get("type", "")
        payload = block.get(btype) or {}
        if btype in {"paragraph", "heading_1", "heading_2", "heading_3",
                     "bulleted_list_item", "numbered_list_item",
                     "quote", "callout", "toggle"}:
            prefix = {"heading_1": "# ", "heading_2": "## ",
                      "heading_3": "### ", "quote": "> ",
                      "bulleted_list_item": "- ",
                      "numbered_list_item": "1. ",
                      "callout": "> ", "toggle": "▸ "}.get(btype, "")
            arr = payload.get("rich_text") or []
            text = "".join(t.get("text", {}).get("content", "") for t in arr)
            return prefix + text if text else ""
        if btype == "code":
            lang = payload.get("language", "")
            arr = payload.get("rich_text") or []
            text = "".join(t.get("text", {}).get("content", "") for t in arr)
            return f"```{lang}\n{text}\n```" if text else ""
        if btype == "to_do":
            mark = "x" if payload.get("checked") else " "
            arr = payload.get("rich_text") or []
            text = "".join(t.get("text", {}).get("content", "") for t in arr)
            return f"- [{mark}] {text}" if text else ""
        return ""
