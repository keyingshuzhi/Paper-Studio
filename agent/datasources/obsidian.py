"""Obsidian 本地 Vault — 通过文件系统读取 .md 笔记。

适合在桌面端(Electron / 本机 Web)扫描用户选择的 Vault 根目录;
不需要 API key。配置项:
  - ``vault_path``: 绝对路径,指向 Vault 根目录(包含 .obsidian/)
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Dict, List

from .base import DataSource, DataSourceError, DataSourceItem, FetchResult, register

_WIKILINK = re.compile(r"\[\[([^\]\n|]+)(?:\|[^\]\n]+)?\]\]")
_TAG = re.compile(r"(?:^|\s)#([\w/\-]+)")
_FRONTMATTER_END = re.compile(r"^---\s*$", re.MULTILINE)


@register
class ObsidianConnector(DataSource):
    id = "obsidian"
    name = "Obsidian 本地 Vault"
    blurb = "扫描本地 Vault 根目录的 Markdown 笔记;支持标签与双向链接。"
    auth_kind = "file_path"
    config_keys = ["vault_path"]

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self.vault_path = (self.config.get("vault_path") or "").strip()

    def _root(self) -> Path:
        if not self.vault_path:
            raise DataSourceError(
                "Obsidian connector 缺少 vault_path 配置")
        path = Path(self.vault_path).expanduser().resolve()
        if not path.is_dir():
            raise DataSourceError(f"Vault 目录不存在: {path}")
        return path

    def list_targets(self) -> List[Dict[str, Any]]:
        try:
            root = self._root()
        except DataSourceError:
            return []
        # 列出顶层文件夹(不含隐藏)
        items = []
        for child in sorted(root.iterdir()):
            if child.name.startswith("."):
                continue
            items.append({
                "id": f"folder:{child.name}",
                "title": child.name,
                "kind": "folder" if child.is_dir() else "file",
            })
        return items[:50]

    def search(self, query: str, limit: int = 10) -> List[DataSourceItem]:
        if not query:
            return []
        try:
            root = self._root()
        except DataSourceError as err:
            return []
        q = query.lower()
        results: List[DataSourceItem] = []
        for path in root.rglob("*.md"):
            try:
                content = path.read_text(encoding="utf-8",
                                         errors="ignore")
            except OSError:
                continue
            if q in content.lower():
                rel = path.relative_to(root)
                title = path.stem
                # 尝试从 frontmatter 提取 title
                m = re.search(r"^title:\s*['\"]?(.+?)['\"]?\s*$",
                              content[:400], re.MULTILINE)
                if m:
                    title = m.group(1)
                snippet = self._make_snippet(content, q)
                tags = _TAG.findall(content)
                results.append(DataSourceItem(
                    id=f"obsidian:{rel}",
                    title=title, source="obsidian",
                    url=str(path), snippet=snippet,
                    year=self._guess_year(path), extra={"tags": tags[:8]},
                ))
                if len(results) >= limit:
                    break
        return results

    def fetch(self, target: str) -> FetchResult:
        if not target.startswith("obsidian:"):
            raise DataSourceError("Obsidian target 必须以 'obsidian:' 开头")
        rel = target.split(":", 1)[1]
        try:
            root = self._root()
        except DataSourceError as err:
            return FetchResult(target=target, body=str(err),
                               format="markdown", meta={"error": True})
        path = (root / rel).resolve()
        # 防越界:必须仍在 root 内
        try:
            path.relative_to(root)
        except ValueError as err:
            raise DataSourceError(f"非 Vault 路径,拒绝读取: {rel}") from err
        if not path.is_file():
            return FetchResult(target=target,
                               body=f"# 笔记不存在\n{rel}",
                               format="markdown", meta={"missing": True})
        content = path.read_text(encoding="utf-8", errors="ignore")
        # 简单 frontmatter 过滤
        body = _FRONTMATTER_END.sub("", content, count=2)
        return FetchResult(target=target, body=body,
                           format="markdown", meta={"size": len(content)})

    @staticmethod
    def _make_snippet(text: str, needle: str, window: int = 80) -> str:
        idx = text.lower().find(needle)
        if idx < 0:
            return text[:160].replace("\n", " ")
        start = max(0, idx - window)
        end = min(len(text), idx + len(needle) + window)
        return text[start:end].replace("\n", " ")

    @staticmethod
    def _guess_year(path: Path) -> Any:
        import datetime
        try:
            mtime = path.stat().st_mtime
            return datetime.datetime.fromtimestamp(mtime).year
        except OSError:
            return None

    def health(self) -> Dict[str, Any]:
        started = time.monotonic()
        try:
            root = self._root()
            count = sum(1 for _ in root.rglob("*.md"))
            return {"ok": True, "latency_ms": round(
                (time.monotonic() - started) * 1000, 1),
                "info": f"{count} 篇 .md 笔记"}
        except DataSourceError as err:
            return {"ok": False, "latency_ms": round(
                (time.monotonic() - started) * 1000, 1), "error": str(err)}
