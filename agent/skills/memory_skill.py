"""研究记忆的读取、写入和管理 Skills。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import BaseSkill, SkillPermission
from .contracts import MEMORY_ENTRY_SCHEMA
from .metadata import PAPER_SCHEMA, Paper


_READ_PERMISSIONS = frozenset({SkillPermission.FILESYSTEM_READ})
_WRITE_PERMISSIONS = frozenset({
    SkillPermission.FILESYSTEM_READ,
    SkillPermission.FILESYSTEM_WRITE,
})
_DELETE_PERMISSIONS = frozenset({
    *_WRITE_PERMISSIONS,
    SkillPermission.DESTRUCTIVE,
})

_MEMORY_INDEX_SCHEMA = {
    "type": "object",
    "required": ["query", "timestamp", "paper_count", "summary_count",
                 "gap_count", "paper_titles"],
    "properties": {
        "query": {"type": "string"},
        "timestamp": {"type": "string"},
        "paper_count": {"type": "integer", "minimum": 0},
        "summary_count": {"type": "integer", "minimum": 0},
        "gap_count": {"type": "integer", "minimum": 0},
        "paper_titles": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": True,
}


class _MemorySkill(BaseSkill):
    """共享记忆存储依赖；未声明 name，不会注册为可调用 Skill。"""

    def __init__(self, memory: Optional[Any] = None, *,
                 path: str = "downloads/research_memory.json") -> None:
        if memory is None:
            from ..core.memory import ResearchMemory
            memory = ResearchMemory(path=path)
        self.memory = memory


class MemorySearchSkill(_MemorySkill):
    """搜索记忆索引，不返回冗长论文内容。"""

    name = "memory_search"
    description = "按研究主题或论文标题搜索记忆索引。"
    version = "1.0.0"
    input_schema = {
        "type": "object",
        "properties": {
            "keyword": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
        },
        "additionalProperties": False,
    }
    output_schema = {"type": "array", "items": _MEMORY_INDEX_SCHEMA}
    permissions = _READ_PERMISSIONS
    default_timeout_seconds = 30.0

    def execute(self, keyword: str = "", limit: int = 100
                ) -> List[Dict[str, Any]]:
        self.report_progress(30, "正在搜索研究记忆", stage="search")
        records = self.memory.list_entries(keyword=keyword, limit=limit)
        self.report_progress(
            95, f"找到 {len(records)} 条记忆", stage="search",
            current=len(records), total=len(records))
        return records


class MemoryReadSkill(_MemorySkill):
    """读取一条完整研究记忆。"""

    name = "memory_read"
    description = "按查询读取论文、总结和分析组成的完整研究记忆。"
    version = "1.0.0"
    input_schema = {
        "type": "object",
        "required": ["query"],
        "properties": {"query": {"type": "string", "minLength": 1}},
        "additionalProperties": False,
    }
    output_schema = {"anyOf": [MEMORY_ENTRY_SCHEMA, {"type": "null"}]}
    permissions = _READ_PERMISSIONS
    default_timeout_seconds = 30.0

    def execute(self, query: str) -> Optional[Dict[str, Any]]:
        self.report_progress(40, "正在读取研究记忆", stage="read")
        return self.memory.get_entry(query)


class MemoryWriteSkill(_MemorySkill):
    """新增或更新一轮研究记忆。"""

    name = "memory_write"
    description = "保存一轮研究的论文、结构化总结和跨论文分析。"
    version = "1.0.0"
    input_schema = {
        "type": "object",
        "required": ["query", "papers"],
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "papers": {"type": "array", "items": PAPER_SCHEMA},
            "summaries": {"type": ["array", "null"]},
            "analysis": {"type": ["object", "null"]},
        },
        "additionalProperties": False,
    }
    output_schema = MEMORY_ENTRY_SCHEMA
    permissions = _WRITE_PERMISSIONS
    default_timeout_seconds = 60.0

    def execute(self, query: str, papers: List[Paper],
                summaries: Optional[List[Dict[str, Any]]] = None,
                analysis: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        normalized = [_coerce_paper(paper) for paper in papers]
        self.report_progress(30, "正在写入研究记忆", stage="write")
        self.memory.add_round(
            query, normalized, summaries=summaries, analysis=analysis)
        entry = self.memory.get_entry(query)
        if entry is None:  # pragma: no cover - 防御持久化实现异常
            raise RuntimeError("研究记忆写入后无法读取")
        self.report_progress(95, "研究记忆已保存", stage="write")
        return entry


class MemoryDeleteSkill(_MemorySkill):
    """删除一条研究记忆。"""

    name = "memory_delete"
    description = "删除指定查询对应的研究记忆。"
    version = "1.0.0"
    input_schema = {
        "type": "object",
        "required": ["query"],
        "properties": {"query": {"type": "string", "minLength": 1}},
        "additionalProperties": False,
    }
    output_schema = {
        "type": "object",
        "required": ["query", "deleted"],
        "properties": {
            "query": {"type": "string"},
            "deleted": {"type": "boolean"},
        },
        "additionalProperties": False,
    }
    permissions = _DELETE_PERMISSIONS
    default_timeout_seconds = 30.0

    def execute(self, query: str) -> Dict[str, Any]:
        self.report_progress(35, "正在删除研究记忆", stage="delete")
        deleted = bool(self.memory.delete(query))
        self.report_progress(
            95, "研究记忆已删除" if deleted else "未找到对应研究记忆",
            stage="delete")
        return {"query": query, "deleted": deleted}


class MemoryClearSkill(_MemorySkill):
    """清空整个研究记忆库。"""

    name = "memory_clear"
    description = "清空全部研究记忆并返回删除数量。"
    version = "1.0.0"
    input_schema = {"type": "object", "additionalProperties": False}
    output_schema = {
        "type": "object",
        "required": ["deleted"],
        "properties": {"deleted": {"type": "integer", "minimum": 0}},
        "additionalProperties": False,
    }
    permissions = _DELETE_PERMISSIONS
    default_timeout_seconds = 60.0

    def execute(self) -> Dict[str, int]:
        self.report_progress(25, "正在清空研究记忆", stage="clear")
        deleted = int(self.memory.clear())
        self.report_progress(95, f"已清除 {deleted} 条研究记忆", stage="clear")
        return {"deleted": deleted}


class MemoryStatsSkill(_MemorySkill):
    """读取研究记忆统计。"""

    name = "memory_stats"
    description = "读取研究记忆条目数、论文数和存储位置。"
    version = "1.0.0"
    input_schema = {"type": "object", "additionalProperties": False}
    output_schema = {
        "type": "object",
        "required": ["entries", "queries", "total_papers", "path"],
        "properties": {
            "entries": {"type": "integer", "minimum": 0},
            "queries": {"type": "array", "items": {"type": "string"}},
            "total_papers": {"type": "integer", "minimum": 0},
            "path": {"type": "string"},
        },
        "additionalProperties": True,
    }
    permissions = _READ_PERMISSIONS
    default_timeout_seconds = 30.0

    def execute(self) -> Dict[str, Any]:
        return self.memory.stats()


def _coerce_paper(value: Paper | Dict[str, Any]) -> Paper:
    if isinstance(value, Paper):
        return value
    if isinstance(value, dict):
        return Paper.from_dict(value)
    raise TypeError(f"无效论文对象: {type(value).__name__}")
