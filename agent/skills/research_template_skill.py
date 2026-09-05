"""研究模板 Skills（v0.2.0）。

把过去只能通过「模式 = single / deep」+ 高级参数选择的研究能力，
重新组织为 5 个面向业务的模板，让普通用户也能一眼选对入口：

* ``research_template_survey`` — 综述（多轮深度闭环 + 大结果集 + 完整报告）
* ``research_template_compare`` — 技术对比（多主题并行 + 横向综合）
* ``research_template_opening`` — 开题调研（轻量级 + 不下载 + 简要报告）
* ``research_template_competitor`` — 竞品论文分析（已知论文列表，跳过检索）
* ``research_template_daily`` — 每日文献追踪（少量最新 + 摘要 + 简短日报）

每个模板是一个 ``BaseSkill``，遵循标准 Skill 契约：

* ``name`` / ``description`` / ``version`` / ``tags`` / ``examples`` 完整；
* 输入 schema 暴露给 MCP / Web；
* 输出 schema 是带 ``plan`` 与 ``report`` 字段的结构化对象；
* 内部委托给现有 ``ResearchAgent`` / ``MultiTopicComparator``，
  不重写任何调度逻辑。

依赖：``BaseSkill`` / ``ResearchAgent`` / ``MultiTopicComparator``。
模板本身不直接调 LLM——所有"智能"行为复用现有 Agent 与 Skill。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .base import BaseSkill, SkillError, SkillPermission
from .contracts import PAPER_SCHEMA
from .metadata import Paper


# ============================== 共享 helper ================================


def _to_paper(raw: Any) -> Paper:
    if isinstance(raw, Paper):
        return raw
    if isinstance(raw, dict):
        return Paper.from_dict(raw)
    raise SkillError(f"无效论文对象: {type(raw).__name__}")


def _plan_dict(plan: Any) -> Dict[str, Any]:
    """把 ResearchPlan/dataclass 转成 dict 用于返回与 schema 校验。"""
    if plan is None:
        return {}
    if isinstance(plan, dict):
        return plan
    if hasattr(plan, "__dataclass_fields__"):
        from dataclasses import asdict
        return asdict(plan)
    return dict(plan) if hasattr(plan, "__dict__") else {}


def _build_agent() -> Any:
    """构造一个可复用的 ResearchAgent 实例。"""
    from ..core.agent import ResearchAgent
    return ResearchAgent()


def _build_comparator() -> Any:
    from ..core.multi_topic import MultiTopicComparator
    return MultiTopicComparator()


# ============================== 模板 1: 综述 ===============================


_RESEARCH_PLAN_DICT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "original_query": {"type": "string"},
        "max_results": {"type": "integer", "minimum": 1},
        "sources": {"type": ["array", "null"], "items": {"type": "string"}},
        "download": {"type": "boolean"},
        "max_downloads": {"type": ["integer", "null"], "minimum": 1},
        "report": {"type": "boolean"},
        "year_from": {"type": ["integer", "null"]},
    },
    "additionalProperties": True,
}


class ResearchTemplateSurveySkill(BaseSkill):
    """综述模板：多轮深度闭环 + 大量结果 + 完整报告。"""

    name = "research_template_survey"
    description = ("综述类研究:自动多轮检索、下载、批量摘要、跨文献分析,"
                   "产出完整深度报告;适合作为某一领域的入门到精通综述。")
    version = "0.2.0"
    tags = ("template", "research", "survey", "deep", "long_form")
    examples = (
        {"query": "long-context LLM",
         "max_results": 20, "max_downloads": 12,
         "year_from": 2023, "sources": ["arxiv_search", "scholar_search"]},
        {"query": "diffusion language model", "max_results": 15,
         "year_from": 2024, "download": True},
    )
    permissions = frozenset({
        SkillPermission.NETWORK,
        SkillPermission.PAID_API,
        SkillPermission.FILESYSTEM_READ,
        SkillPermission.FILESYSTEM_WRITE,
    })
    default_timeout_seconds = 1800.0  # 30 分钟,深度闭环
    input_schema = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 100,
                            "default": 20},
            "max_downloads": {"type": "integer", "minimum": 1, "maximum": 100,
                              "default": 12},
            "year_from": {"type": ["integer", "null"]},
            "sources": {"type": ["array", "null"],
                        "items": {"type": "string"}},
            "download": {"type": "boolean", "default": True},
            "report": {"type": "boolean", "default": True},
            "research_direction": {"type": "string", "maxLength": 2000,
                                   "default": ""},
        },
        "additionalProperties": True,
    }
    output_schema = {
        "type": "object",
        "required": ["template", "query"],
        "properties": {
            "template": {"type": "string"},
            "query": {"type": "string"},
            "plan": {"type": "object"},
            "report_path": {"type": ["string", "null"]},
            "paper_count": {"type": "integer", "minimum": 0},
            "summary_count": {"type": "integer", "minimum": 0},
            "analysis": {"type": ["object", "null"]},
        },
        "additionalProperties": True,
    }

    def execute(self, query: str, *, max_results: int = 20,
                max_downloads: int = 12,
                year_from: Optional[int] = None,
                sources: Optional[Sequence[str]] = None,
                download: bool = True,
                report: bool = True,
                research_direction: str = "",
                **overrides: Any) -> Dict[str, Any]:
        if not (query or "").strip():
            raise SkillError("query 不能为空")
        self.report_progress(5, f"综述模板: {query}", stage="plan")
        agent = _build_agent()
        result = agent.run(
            user_input=query,
            summarize=True, analyze=True,
            summarize_limit=max_results,
            max_results=max_results,
            max_downloads=max_downloads if download else 0,
            sources=list(sources) if sources else None,
            year_from=year_from,
            download=download,
            report=report,
            research_direction=research_direction,
            **overrides,
        )
        self.report_progress(95, "综述研究完成", stage="done")
        return {
            "template": "survey",
            "query": query,
            "plan": _plan_dict(result.get("plan")),
            "report_path": result.get("report_path"),
            "paper_count": len(result.get("papers") or []),
            "summary_count": len(result.get("summaries") or []),
            "analysis": result.get("analysis"),
        }


# ============================== 模板 2: 技术对比 ==========================


class ResearchTemplateCompareSkill(BaseSkill):
    """技术对比：多主题并行 + 横向综合。"""

    name = "research_template_compare"
    description = ("技术对比类研究:对 2-5 个并列技术/方法分别做检索-摘要-"
                   "分析,再用 LLM 横向综合,产出多主题对比报告。")
    version = "0.2.0"
    tags = ("template", "research", "comparison", "multi_topic")
    examples = (
        {"topics": ["Mamba", "Transformer"], "max_results": 8,
         "year_from": 2023},
        {"topics": ["RAG", "fine-tuning", "prompt-engineering"],
         "max_results": 6, "year_from": 2024},
    )
    permissions = frozenset({
        SkillPermission.NETWORK,
        SkillPermission.PAID_API,
        SkillPermission.FILESYSTEM_READ,
        SkillPermission.FILESYSTEM_WRITE,
    })
    default_timeout_seconds = 1800.0
    input_schema = {
        "type": "object",
        "required": ["topics"],
        "properties": {
            "topics": {"type": "array",
                       "items": {"type": "string", "minLength": 1},
                       "minItems": 2, "maxItems": 5},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 50,
                            "default": 6},
            "year_from": {"type": ["integer", "null"]},
            "sources": {"type": ["array", "null"],
                        "items": {"type": "string"}},
            "research_direction": {"type": "string", "maxLength": 2000,
                                   "default": ""},
        },
        "additionalProperties": True,
    }
    output_schema = {
        "type": "object",
        "required": ["template", "topics"],
        "properties": {
            "template": {"type": "string"},
            "topics": {"type": "array", "items": {"type": "string"}},
            "report_path": {"type": ["string", "null"]},
            "comparison": {"type": ["object", "null"]},
            "topic_digests": {"type": ["object", "null"]},
        },
        "additionalProperties": True,
    }

    def execute(self, topics: Sequence[str], *,
                max_results: int = 6,
                year_from: Optional[int] = None,
                sources: Optional[Sequence[str]] = None,
                research_direction: str = "",
                **overrides: Any) -> Dict[str, Any]:
        topic_list = [str(t).strip() for t in (topics or []) if str(t).strip()]
        if len(topic_list) < 2:
            raise SkillError("技术对比至少需要 2 个主题")
        if len(topic_list) > 5:
            raise SkillError("技术对比最多 5 个主题,避免横向综合过稀")
        self.report_progress(5,
                             f"技术对比: {' vs '.join(topic_list)}", stage="plan")
        comparator = _build_comparator()
        result = comparator.compare(
            topic_list,
            max_results=max_results,
            year_from=year_from,
            sources=list(sources) if sources else None,
            research_direction=research_direction,
            **overrides,
        )
        self.report_progress(95, "技术对比完成", stage="done")
        return {
            "template": "compare",
            "topics": topic_list,
            "report_path": result.get("report_path"),
            "comparison": result.get("comparison"),
            "topic_digests": result.get("topic_digests"),
        }


# ============================== 模板 3: 开题调研 ===========================


class ResearchTemplateOpeningSkill(BaseSkill):
    """开题调研：轻量级,不下载原文,只做摘要 + 简要单轮报告。"""

    name = "research_template_opening"
    description = ("开题/选题阶段:快速检索近 3 年相关文献,生成简短摘要与"
                   "单轮报告,不下载原文;帮助判断选题价值与方向。")
    version = "0.2.0"
    tags = ("template", "research", "opening", "lightweight")
    examples = (
        {"query": "graph neural network for drug discovery",
         "year_from": 2022, "max_results": 8},
        {"query": "小样本学习综述", "year_from": 2023, "max_results": 6},
    )
    permissions = frozenset({
        SkillPermission.NETWORK,
        SkillPermission.PAID_API,
        SkillPermission.FILESYSTEM_READ,
    })
    default_timeout_seconds = 600.0
    input_schema = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 50,
                            "default": 8},
            "year_from": {"type": "integer", "default": 2022},
            "sources": {"type": ["array", "null"],
                        "items": {"type": "string"}},
            "report": {"type": "boolean", "default": True},
        },
        "additionalProperties": True,
    }
    output_schema = {
        "type": "object",
        "required": ["template", "query"],
        "properties": {
            "template": {"type": "string"},
            "query": {"type": "string"},
            "plan": {"type": "object"},
            "report_path": {"type": ["string", "null"]},
            "paper_count": {"type": "integer", "minimum": 0},
            "summary_count": {"type": "integer", "minimum": 0},
        },
        "additionalProperties": True,
    }

    def execute(self, query: str, *, max_results: int = 8,
                year_from: int = 2022,
                sources: Optional[Sequence[str]] = None,
                report: bool = True,
                **overrides: Any) -> Dict[str, Any]:
        if not (query or "").strip():
            raise SkillError("query 不能为空")
        self.report_progress(5, f"开题调研: {query}", stage="plan")
        agent = _build_agent()
        result = agent.run(
            user_input=query,
            summarize=True, analyze=False,  # 开题阶段不跑跨文献分析
            summarize_limit=max_results,
            max_results=max_results,
            sources=list(sources) if sources else None,
            year_from=year_from,
            download=False,  # 开题不下载
            report=report,
            **overrides,
        )
        self.report_progress(95, "开题调研完成", stage="done")
        return {
            "template": "opening",
            "query": query,
            "plan": _plan_dict(result.get("plan")),
            "report_path": result.get("report_path"),
            "paper_count": len(result.get("papers") or []),
            "summary_count": len(result.get("summaries") or []),
        }


# ============================== 模板 4: 竞品论文分析 =======================


class ResearchTemplateCompetitorSkill(BaseSkill):
    """竞品论文分析:对一组已知论文做对比。"""

    name = "research_template_competitor"
    description = ("竞品/竞品论文分析:对一组已知论文做摘要 + 横向对比,"
                   "不重新检索;适合做工具/系统论文对比、产品级 benchmark。")
    version = "0.2.0"
    tags = ("template", "research", "competitor", "comparison")
    examples = (
        {"papers": [{"title": "Toolformer", "url": "..."},
                     {"title": "ReAct", "url": "..."}],
         "compare_dimensions": ["工具调用能力", "推理方法", "局限"]},
    )
    permissions = frozenset({
        SkillPermission.NETWORK,
        SkillPermission.PAID_API,
        SkillPermission.FILESYSTEM_READ,
    })
    default_timeout_seconds = 900.0
    input_schema = {
        "type": "object",
        "required": ["papers"],
        "properties": {
            "papers": {"type": "array",
                       "items": PAPER_SCHEMA,
                       "minItems": 2, "maxItems": 8},
            "compare_dimensions": {"type": ["array", "null"],
                                    "items": {"type": "string"}},
            "report": {"type": "boolean", "default": True},
            "research_direction": {"type": "string", "maxLength": 2000,
                                   "default": ""},
        },
        "additionalProperties": True,
    }
    output_schema = {
        "type": "object",
        "required": ["template", "paper_titles"],
        "properties": {
            "template": {"type": "string"},
            "paper_titles": {"type": "array", "items": {"type": "string"}},
            "compare_dimensions": {"type": "array", "items": {"type": "string"}},
            "plan": {"type": "object"},
            "report_path": {"type": ["string", "null"]},
            "summaries": {"type": ["array", "null"]},
            "analysis": {"type": ["object", "null"]},
        },
        "additionalProperties": True,
    }

    def execute(self, papers: Sequence[Any], *,
                compare_dimensions: Optional[Sequence[str]] = None,
                report: bool = True,
                research_direction: str = "",
                **overrides: Any) -> Dict[str, Any]:
        if not papers:
            raise SkillError("papers 列表不能为空")
        if len(papers) < 2:
            raise SkillError("竞品分析至少需要 2 篇论文")
        if len(papers) > 8:
            raise SkillError("竞品分析最多 8 篇,避免对比维度失焦")
        self.report_progress(10, f"竞品分析: {len(papers)} 篇论文", stage="plan")
        normalized: List[Paper] = [_to_paper(p) for p in papers]

        # 直接走 ResearchAgent 的 existing_papers 路径(已知论文,不重新检索)
        agent = _build_agent()
        result = agent.run(
            user_input="competitor_paper_analysis",
            existing_papers=normalized,
            summarize=True,
            summarize_limit=len(normalized),
            analyze=True,
            download=False,
            report=report,
            research_direction=research_direction,
            **overrides,
        )
        self.report_progress(95, "竞品分析完成", stage="done")
        return {
            "template": "competitor",
            "paper_titles": [p.title for p in normalized],
            "compare_dimensions": list(compare_dimensions or []),
            "plan": _plan_dict(result.get("plan")),
            "report_path": result.get("report_path"),
            "summaries": result.get("summaries"),
            "analysis": result.get("analysis"),
        }


# ============================== 模板 5: 每日文献追踪 =======================


class ResearchTemplateDailySkill(BaseSkill):
    """每日文献追踪:浅搜索 + 摘要 + 简短日报。"""

    name = "research_template_daily"
    description = ("每日文献追踪:限定最近 N 天的少量新文献,"
                   "生成结构化摘要 + 极简日报;适合在定时计划里每日执行。")
    version = "0.2.0"
    tags = ("template", "research", "daily", "tracking", "lightweight")
    examples = (
        {"query": "Mamba", "days_back": 3, "max_results": 5},
        {"query": "agent harness", "days_back": 7, "max_results": 8},
    )
    permissions = frozenset({
        SkillPermission.NETWORK,
        SkillPermission.PAID_API,
        SkillPermission.FILESYSTEM_READ,
    })
    default_timeout_seconds = 600.0
    input_schema = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "days_back": {"type": "integer", "minimum": 1, "maximum": 365,
                          "default": 7},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 30,
                            "default": 5},
            "sources": {"type": ["array", "null"],
                        "items": {"type": "string"}},
            "report": {"type": "boolean", "default": True},
        },
        "additionalProperties": True,
    }
    output_schema = {
        "type": "object",
        "required": ["template", "query", "days_back"],
        "properties": {
            "template": {"type": "string"},
            "query": {"type": "string"},
            "days_back": {"type": "integer", "minimum": 1},
            "year_from": {"type": "integer"},
            "plan": {"type": "object"},
            "report_path": {"type": ["string", "null"]},
            "paper_count": {"type": "integer", "minimum": 0},
            "summary_count": {"type": "integer", "minimum": 0},
        },
        "additionalProperties": True,
    }

    def execute(self, query: str, *, days_back: int = 7,
                max_results: int = 5,
                sources: Optional[Sequence[str]] = None,
                report: bool = True,
                **overrides: Any) -> Dict[str, Any]:
        from datetime import datetime, timedelta
        if not (query or "").strip():
            raise SkillError("query 不能为空")
        self.report_progress(5, f"每日追踪({days_back}天): {query}", stage="plan")
        year_from = (datetime.now() - timedelta(days=int(days_back))).year
        # 把 days_back 透传到 agent,让搜索插件可按 mtime 进一步过滤
        agent = _build_agent()
        result = agent.run(
            user_input=query,
            summarize=True, analyze=False,
            summarize_limit=max_results,
            max_results=max_results,
            sources=list(sources) if sources else None,
            year_from=year_from,
            download=False,  # 每日追踪不下载
            report=report,
            days_back=days_back,
            **overrides,
        )
        self.report_progress(95, "每日追踪完成", stage="done")
        return {
            "template": "daily",
            "query": query,
            "days_back": days_back,
            "year_from": year_from,
            "plan": _plan_dict(result.get("plan")),
            "report_path": result.get("report_path"),
            "paper_count": len(result.get("papers") or []),
            "summary_count": len(result.get("summaries") or []),
        }
