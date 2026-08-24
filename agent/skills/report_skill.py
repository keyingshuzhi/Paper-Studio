"""研究报告渲染与落盘 Skills。"""

from __future__ import annotations

from pathlib import Path, PureWindowsPath
from typing import Any, Dict, List, Optional

from .base import BaseSkill, SkillPermission
from .contracts import REPORT_KIND_SCHEMA, RESEARCH_PLAN_SCHEMA
from .metadata import PAPER_SCHEMA, Paper


_REPORT_PROPERTIES: Dict[str, Any] = {
    "kind": REPORT_KIND_SCHEMA,
    "plan": {"anyOf": [RESEARCH_PLAN_SCHEMA, {"type": "null"}]},
    "papers": {"type": "array", "items": PAPER_SCHEMA},
    "acquisition": {"type": ["object", "null"]},
    "summaries": {"type": ["array", "null"]},
    "analysis": {"type": ["object", "null"]},
    "meta": {"type": ["object", "null"]},
    "rounds": {"type": ["array", "null"]},
    "citations": {"type": ["object", "null"]},
    "topic_digests": {"type": ["object", "null"]},
    "comparison": {"type": ["object", "null"]},
}


class ReportRenderSkill(BaseSkill):
    """只在内存中生成 Markdown，不写入文件。"""

    name = "report_render"
    description = "渲染单轮、深度或多主题对比 Markdown 报告，不产生文件。"
    version = "1.0.0"
    input_schema = {
        "type": "object",
        "required": ["kind"],
        "properties": _REPORT_PROPERTIES,
        "additionalProperties": False,
    }
    output_schema = {"type": "string", "minLength": 1}
    permissions = frozenset()
    default_timeout_seconds = 60.0

    def __init__(self, reporter: Optional[Any] = None) -> None:
        if reporter is None:
            from ..core.reporter import Reporter
            reporter = Reporter()
        self.reporter = reporter

    def execute(
        self,
        kind: str,
        plan: Optional[Any] = None,
        papers: Optional[List[Paper]] = None,
        acquisition: Optional[Dict[str, Any]] = None,
        summaries: Optional[List[Dict[str, Any]]] = None,
        analysis: Optional[Dict[str, Any]] = None,
        meta: Optional[Dict[str, Any]] = None,
        rounds: Optional[List[Dict[str, Any]]] = None,
        citations: Optional[Dict[str, Any]] = None,
        topic_digests: Optional[Dict[str, Dict[str, Any]]] = None,
        comparison: Optional[Dict[str, Any]] = None,
    ) -> str:
        self.report_progress(20, "正在整理报告数据", stage="prepare")
        normalized_papers = [_coerce_paper(item) for item in (papers or [])]
        self.report_progress(55, "正在渲染 Markdown 报告", stage="render")
        if kind == "single":
            if plan is None:
                raise ValueError("单轮报告缺少 plan")
            content = self.reporter.render(
                _coerce_plan(plan), normalized_papers, acquisition,
                summaries, analysis)
        elif kind == "deep":
            if meta is None or rounds is None:
                raise ValueError("深度报告缺少 meta 或 rounds")
            content = self.reporter.render_deep(
                meta, rounds, normalized_papers, citations, acquisition)
        elif kind == "comparison":
            if meta is None or topic_digests is None or comparison is None:
                raise ValueError(
                    "多主题对比报告缺少 meta、topic_digests 或 comparison")
            content = self.reporter.render_comparison(
                meta, topic_digests, comparison)
        else:  # Schema 已阻止该路径，保留给直接 execute 调用。
            raise ValueError(f"不支持的报告类型: {kind}")
        self.report_progress(95, "报告渲染完成", stage="render")
        return content

    def render(self, plan: Any, papers: List[Paper],
               acquisition: Optional[Dict[str, Any]] = None,
               summaries: Optional[List[Dict[str, Any]]] = None,
               analysis: Optional[Dict[str, Any]] = None) -> str:
        return self.reporter.render(
            plan, papers, acquisition, summaries, analysis)

    def render_deep(self, *args: Any, **kwargs: Any) -> str:
        return self.reporter.render_deep(*args, **kwargs)

    def render_comparison(self, *args: Any, **kwargs: Any) -> str:
        return self.reporter.render_comparison(*args, **kwargs)


class ReportWriteSkill(ReportRenderSkill):
    """生成 Markdown 并写入指定报告目录。"""

    name = "report_write"
    description = "生成单轮、深度或多主题对比报告并安全写入本地目录。"
    version = "1.0.0"
    input_schema = {
        "type": "object",
        "required": ["kind"],
        "properties": {
            **_REPORT_PROPERTIES,
            "base_dir": {"type": "string", "minLength": 1},
            "filename": {"type": ["string", "null"]},
        },
        "additionalProperties": False,
    }
    output_schema = {
        "type": "object",
        "required": ["kind", "path", "filename"],
        "properties": {
            "kind": REPORT_KIND_SCHEMA,
            "path": {"type": "string", "minLength": 1},
            "filename": {"type": "string", "minLength": 1},
        },
        "additionalProperties": False,
    }
    permissions = frozenset({SkillPermission.FILESYSTEM_WRITE})
    default_timeout_seconds = 120.0

    def __init__(self, reporter: Optional[Any] = None, *,
                 base_dir: str = "downloads") -> None:
        super().__init__(reporter=reporter)
        self.default_base_dir = str(base_dir)

    def execute(
        self,
        kind: str,
        plan: Optional[Any] = None,
        papers: Optional[List[Paper]] = None,
        acquisition: Optional[Dict[str, Any]] = None,
        summaries: Optional[List[Dict[str, Any]]] = None,
        analysis: Optional[Dict[str, Any]] = None,
        meta: Optional[Dict[str, Any]] = None,
        rounds: Optional[List[Dict[str, Any]]] = None,
        citations: Optional[Dict[str, Any]] = None,
        topic_digests: Optional[Dict[str, Dict[str, Any]]] = None,
        comparison: Optional[Dict[str, Any]] = None,
        base_dir: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> Dict[str, str]:
        normalized_papers = [_coerce_paper(item) for item in (papers or [])]
        filename = _safe_filename(filename)
        target_dir = base_dir or self.default_base_dir
        self.report_progress(20, "正在准备报告文件", stage="prepare")
        if kind == "single":
            if plan is None:
                raise ValueError("单轮报告缺少 plan")
            path = self.reporter.write(
                _coerce_plan(plan), normalized_papers, acquisition,
                summaries, analysis, base_dir=target_dir, filename=filename)
        elif kind == "deep":
            if meta is None or rounds is None:
                raise ValueError("深度报告缺少 meta 或 rounds")
            path = self.reporter.write_deep(
                meta, rounds, normalized_papers, citations=citations,
                acquisition=acquisition, base_dir=target_dir,
                filename=filename)
        elif kind == "comparison":
            if meta is None or topic_digests is None or comparison is None:
                raise ValueError(
                    "多主题对比报告缺少 meta、topic_digests 或 comparison")
            path = self.reporter.write_comparison(
                meta, topic_digests, comparison, base_dir=target_dir,
                filename=filename)
        else:
            raise ValueError(f"不支持的报告类型: {kind}")
        result_path = Path(path)
        self.report_progress(95, "报告文件已保存", stage="write")
        return {
            "kind": kind,
            "path": str(result_path),
            "filename": result_path.name,
        }

    # 兼容核心编排器当前使用的 Reporter 方法。
    def write(self, *args: Any, **kwargs: Any) -> Path:
        kwargs.setdefault("base_dir", self.default_base_dir)
        return self.reporter.write(*args, **kwargs)

    def write_deep(self, *args: Any, **kwargs: Any) -> Path:
        kwargs.setdefault("base_dir", self.default_base_dir)
        return self.reporter.write_deep(*args, **kwargs)

    def write_comparison(self, *args: Any, **kwargs: Any) -> Path:
        kwargs.setdefault("base_dir", self.default_base_dir)
        return self.reporter.write_comparison(*args, **kwargs)


def _coerce_paper(value: Paper | Dict[str, Any]) -> Paper:
    if isinstance(value, Paper):
        return value
    if isinstance(value, dict):
        return Paper.from_dict(value)
    raise TypeError(f"无效论文对象: {type(value).__name__}")


def _coerce_plan(value: Any) -> Any:
    if hasattr(value, "query") and hasattr(value, "original_query"):
        return value
    if not isinstance(value, dict):
        raise TypeError(f"无效研究计划: {type(value).__name__}")
    from ..core.planner import ResearchPlan
    fields = {
        "query", "original_query", "max_results", "sources", "download",
        "max_downloads", "report", "year_from", "extra",
    }
    return ResearchPlan(**{key: item for key, item in value.items()
                           if key in fields})


def _safe_filename(filename: Optional[str]) -> Optional[str]:
    """外部 Skill 调用只能提供当前报告目录内的 Markdown 文件名。"""
    if filename is None:
        return None
    candidate = Path(filename)
    if (candidate.name != filename or PureWindowsPath(filename).name != filename
            or filename in {".", ".."}
            or candidate.suffix.lower() != ".md"):
        raise ValueError("报告文件名必须是不含目录的 .md 文件名")
    return filename
