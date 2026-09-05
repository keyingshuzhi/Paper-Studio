"""研究记忆的读取、写入和管理 Skills。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import BaseSkill, SkillError, SkillPermission
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
    description = "离线语义搜索研究主题、论文、方法、结论与知识盲点。"
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


class MemoryPinSkill(_MemorySkill):
    """固定或取消固定一条研究主题。"""

    name = "memory_pin"
    description = ("固定一条研究主题；固定项不会被清理过期自动归档,"
                   "也不会在结果列表中下沉。")
    version = "1.0.0"
    input_schema = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "pinned": {"type": "boolean", "default": True},
        },
        "additionalProperties": False,
    }
    output_schema = {"anyOf": [MEMORY_ENTRY_SCHEMA, {"type": "null"}]}
    permissions = _WRITE_PERMISSIONS
    default_timeout_seconds = 30.0

    def execute(self, query: str, pinned: bool = True
                ) -> Optional[Dict[str, Any]]:
        self.report_progress(40, f"正在{'固定' if pinned else '取消固定'}主题",
                             stage="pin")
        entry = self.memory.set_pinned(query, pinned=bool(pinned))
        if entry is None:
            return None
        self.report_progress(95, "已更新固定状态", stage="pin")
        return entry


class MemoryArchiveSkill(_MemorySkill):
    """归档或恢复一条研究主题。"""

    name = "memory_archive"
    description = ("归档一条研究主题;归档项不会自动参与新研究的复用检索,"
                   "但仍可在界面和导出中查看。")
    version = "1.0.0"
    input_schema = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "archived": {"type": "boolean", "default": True},
        },
        "additionalProperties": False,
    }
    output_schema = {"anyOf": [MEMORY_ENTRY_SCHEMA, {"type": "null"}]}
    permissions = _WRITE_PERMISSIONS
    default_timeout_seconds = 30.0

    def execute(self, query: str, archived: bool = True
                ) -> Optional[Dict[str, Any]]:
        self.report_progress(40, f"正在{'归档' if archived else '恢复'}主题",
                             stage="archive")
        entry = self.memory.set_archived(query, archived=bool(archived))
        if entry is None:
            return None
        self.report_progress(95, "已更新归档状态", stage="archive")
        return entry


class MemoryExpirySkill(_MemorySkill):
    """设置一条研究主题的过期时间。"""

    name = "memory_expiry"
    description = ("为研究主题设置过期时间(单位:天);0 或 null 表示永不过期。"
                   "过期项会被 ``memory_cleanup`` 归档。")
    version = "1.0.0"
    input_schema = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "days": {"anyOf": [{"type": "integer", "minimum": 0,
                                "maximum": 36500},
                               {"type": "null"}]},
        },
        "additionalProperties": False,
    }
    output_schema = {"anyOf": [MEMORY_ENTRY_SCHEMA, {"type": "null"}]}
    permissions = _WRITE_PERMISSIONS
    default_timeout_seconds = 30.0

    def execute(self, query: str, days: Optional[int] = None
                ) -> Optional[Dict[str, Any]]:
        self.report_progress(40, "正在设置过期时间", stage="expiry")
        try:
            entry = self.memory.set_expiry(query, days)
        except ValueError as err:
            raise SkillError(str(err)) from err
        if entry is None:
            return None
        self.report_progress(95, "已更新过期时间", stage="expiry")
        return entry


class MemoryMergeSkill(_MemorySkill):
    """将若干来源主题合并到目标主题,来源被归档并保留追溯。"""

    name = "memory_merge"
    description = ("把若干相近主题合并到目标主题;来源主题会被自动归档,"
                   "并在目标主题的 ``merged_from`` 字段保留来源查询名。")
    version = "1.0.0"
    input_schema = {
        "type": "object",
        "required": ["target", "sources"],
        "properties": {
            "target": {"type": "string", "minLength": 1},
            "sources": {"type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "minItems": 1},
        },
        "additionalProperties": False,
    }
    output_schema = {"anyOf": [MEMORY_ENTRY_SCHEMA, {"type": "null"}]}
    permissions = _WRITE_PERMISSIONS
    default_timeout_seconds = 60.0

    def execute(self, target: str, sources: Sequence[str]
                ) -> Optional[Dict[str, Any]]:
        source_list = [str(item).strip() for item in sources if str(item).strip()]
        if not source_list:
            raise SkillError("至少需要一条来源主题")
        self.report_progress(20, f"正在合并 {len(source_list)} 条主题到 {target}",
                             stage="merge", total=len(source_list))
        try:
            entry = self.memory.merge(target, source_list)
        except ValueError as err:
            raise SkillError(str(err)) from err
        if entry is None:
            return None
        self.report_progress(95, "合并完成", stage="merge")
        return entry


class MemoryCleanupSkill(_MemorySkill):
    """清理过期或长期未复用的主题(实际为安全归档,不删除数据)。"""

    name = "memory_cleanup"
    description = ("将过期或长期未复用的主题安全归档(不会真正删除)。"
                   "固定或已归档的主题会被跳过；执行前需显式 ``confirmed=true``。")
    version = "1.0.0"
    input_schema = {
        "type": "object",
        "required": ["confirmed"],
        "properties": {
            "max_age_days": {"type": "integer", "minimum": 1,
                             "maximum": 36500, "default": 180},
            "confirmed": {"type": "boolean", "const": True},
        },
        "additionalProperties": False,
    }
    output_schema = {
        "type": "object",
        "required": ["count", "archived_queries", "max_age_days", "action"],
        "properties": {
            "count": {"type": "integer", "minimum": 0},
            "archived_queries": {"type": "array", "items": {"type": "string"}},
            "max_age_days": {"type": "integer", "minimum": 1},
            "action": {"type": "string"},
        },
        "additionalProperties": True,
    }
    permissions = _DELETE_PERMISSIONS
    default_timeout_seconds = 60.0

    def execute(self, max_age_days: int = 180, *, confirmed: bool = False
                ) -> Dict[str, Any]:
        if not confirmed:
            raise SkillError("清理记忆需要显式 confirmed=true 以避免误操作")
        self.report_progress(30, f"正在扫描超过 {max_age_days} 天未复用/已过期的记忆",
                             stage="cleanup")
        try:
            result = self.memory.cleanup_expired(int(max_age_days))
        except ValueError as err:
            raise SkillError(str(err)) from err
        self.report_progress(95, f"已归档 {result.get('count', 0)} 条记忆",
                             stage="cleanup")
        return result


class MemoryGraphSkill(_MemorySkill):
    """从当前记忆派生的主题知识图谱。"""

    name = "memory_graph"
    description = ("从研究记忆即时派生主题知识图谱(节点: topic / paper / "
                   "author / method / conclusion / gap;边: 包含 / 作者 / "
                   "方法 / 贡献 / 结论 / 盲点)。返回的图不包含敏感字段。")
    version = "1.0.0"
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_nodes": {"type": "integer", "minimum": 20, "maximum": 800,
                          "default": 240},
            "include_archived": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
    }
    output_schema = {
        "type": "object",
        "required": ["nodes", "edges", "stats"],
        "properties": {
            "query": {"type": "string"},
            "nodes": {"type": "array", "items": {"type": "object"}},
            "edges": {"type": "array", "items": {"type": "object"}},
            "stats": {"type": "object"},
        },
        "additionalProperties": True,
    }
    permissions = _READ_PERMISSIONS
    default_timeout_seconds = 60.0

    def execute(self, query: str = "", max_nodes: int = 240,
                include_archived: bool = False) -> Dict[str, Any]:
        self.report_progress(35, "正在构建主题知识图谱", stage="graph")
        graph = self.memory.knowledge_graph(
            query=query or None,
            include_archived=bool(include_archived),
            max_nodes=int(max_nodes))
        stats = graph.get("stats") or {}
        self.report_progress(95, f"已派生 {stats.get('nodes', 0)} 个节点, "
                                  f"{stats.get('edges', 0)} 条边", stage="graph")
        return graph


class MemoryExportSkill(_MemorySkill):
    """导出全部研究记忆(JSON 或 Markdown)。"""

    name = "memory_export"
    description = ("把全部研究记忆导出为 JSON 字典或 Markdown 字符串;"
                   "可选择是否包含已归档的条目，便于离线备份。")
    version = "1.0.0"
    input_schema = {
        "type": "object",
        "properties": {
            "include_archived": {"type": "boolean", "default": True},
            "format": {"type": "string", "enum": ["json", "markdown"],
                       "default": "markdown"},
        },
        "additionalProperties": False,
    }
    output_schema = {
        "anyOf": [
            {"type": "object",
             "required": ["schema_version", "exported_at", "entries"],
             "properties": {
                 "schema_version": {"type": "integer"},
                 "exported_at": {"type": "string"},
                 "entries": {"type": "array", "items": {"type": "object"}},
             },
             "additionalProperties": True},
            {"type": "string", "minLength": 1},
        ],
    }
    permissions = _READ_PERMISSIONS
    default_timeout_seconds = 60.0

    def execute(self, include_archived: bool = True,
                format: str = "markdown") -> Any:
        self.report_progress(40, f"正在以 {format} 格式导出研究记忆", stage="export")
        if format == "json":
            payload = self.memory.export_payload(
                include_archived=bool(include_archived))
        elif format == "markdown":
            payload = self.memory.export_markdown(
                include_archived=bool(include_archived))
        else:
            raise SkillError(f"不支持的导出格式: {format!r}")
        self.report_progress(95, "导出完成", stage="export")
        return payload





def _coerce_paper(value: Paper | Dict[str, Any]) -> Paper:
    if isinstance(value, Paper):
        return value
    if isinstance(value, dict):
        return Paper.from_dict(value)
    raise TypeError(f"无效论文对象: {type(value).__name__}")
