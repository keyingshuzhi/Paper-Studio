"""研究记忆持久化（V4.0）。

把每次研究的查询、论文、摘要、分析写入本地 JSON 记忆库。
后续研究会话可：
- 跳过已检索过的查询（避免重复工作）
- 复用历史分析中的盲点建议（跨会话延续研究闭环）
- 增量式深度研究：新会话从记忆继续而非从零开始
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..skills.metadata import Paper


class ResearchMemory:
    """基于本地 JSON 文件的研究记忆库。"""

    def __init__(self, path: str = "downloads/research_memory.json") -> None:
        self.path = Path(path)
        self._data: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._load()

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def add_round(self, query: str, papers: List[Paper],
                  summaries: Optional[List[Dict[str, Any]]] = None,
                  analysis: Optional[Dict[str, Any]] = None) -> None:
        """记录一轮研究（按归一化查询 upsert）。"""
        key = self._norm(query)
        with self._lock:
            self._data[key] = {
                "query": query.strip(),
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "papers": [p.to_dict() for p in papers],
                "summaries": summaries or [],
                "analysis": analysis or None,
            }
            self._persist()

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def has_query(self, query: str) -> bool:
        with self._lock:
            return self._norm(query) in self._data

    def get_round(self, query: str) -> Optional[Dict[str, Any]]:
        """取回历史研究记录（papers 反序列化为 Paper 对象）。"""
        with self._lock:
            entry = self._data.get(self._norm(query))
            if not entry:
                return None
            return {
                "query": entry["query"],
                "timestamp": entry["timestamp"],
                "papers": [Paper.from_dict(d) for d in entry.get("papers", [])],
                "summaries": entry.get("summaries") or [],
                "analysis": entry.get("analysis") or None,
            }

    def all_queries(self) -> List[str]:
        """所有历史查询（按时间倒序）。"""
        with self._lock:
            return [e["query"] for e in
                    sorted(self._data.values(),
                           key=lambda e: e.get("timestamp", ""),
                           reverse=True)]

    def list_entries(self, keyword: str = "", limit: int = 100) -> List[Dict[str, Any]]:
        """返回用于界面管理的轻量索引，不暴露冗长的全文数据。"""
        keyword = self._norm(keyword)
        with self._lock:
            entries = sorted(self._data.values(),
                             key=lambda e: e.get("timestamp", ""), reverse=True)
            records = []
            for entry in entries:
                query = str(entry.get("query") or "")
                titles = [str(p.get("title") or "") for p in entry.get("papers", [])]
                haystack = " ".join([query] + titles).lower()
                if keyword and keyword not in haystack:
                    continue
                analysis = entry.get("analysis") or {}
                records.append({
                    "query": query,
                    "timestamp": entry.get("timestamp", ""),
                    "paper_count": len(entry.get("papers", [])),
                    "summary_count": len(entry.get("summaries", [])),
                    "gap_count": len(analysis.get("gaps", []))
                    if isinstance(analysis, dict) else 0,
                    "paper_titles": [title for title in titles if title][:3],
                })
                if len(records) >= max(1, min(500, int(limit))):
                    break
            return records

    def get_entry(self, query: str) -> Optional[Dict[str, Any]]:
        """返回一条可展示的记忆明细（保留原始论文与分析数据）。"""
        with self._lock:
            entry = self._data.get(self._norm(query))
            if entry is None:
                return None
            # JSON 往返复制，防止界面层意外修改内存中的源数据。
            return json.loads(json.dumps(entry, ensure_ascii=False))

    def delete(self, query: str) -> bool:
        """删除指定查询的记忆；找不到时返回 False。"""
        with self._lock:
            key = self._norm(query)
            if key not in self._data:
                return False
            del self._data[key]
            self._persist()
            return True

    def clear(self) -> int:
        """清空所有研究记忆，返回移除条目数。"""
        with self._lock:
            count = len(self._data)
            if count:
                self._data = {}
                self._persist()
            return count

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            total_papers = sum(len(e.get("papers", [])) for e in self._data.values())
            return {
                "entries": len(self._data),
                "queries": list(self._data.keys()),
                "total_papers": total_papers,
                "path": str(self.path),
            }

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def _load(self) -> None:
        with self._lock:
            if not self.path.exists():
                return
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._data = data
            except (json.JSONDecodeError, OSError):
                # 记忆库损坏时从空开始，不阻塞主流程
                self._data = {}

    def _persist(self) -> None:
        """原子写入：先写临时文件再替换，防止写一半损坏。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent),
                                   suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    @staticmethod
    def _norm(query: str) -> str:
        """查询归一化（与闭环去重保持一致）。"""
        return query.strip().lower()
