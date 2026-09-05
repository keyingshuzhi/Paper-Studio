"""论文结构化总结 Skills。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import BaseSkill, SkillPermission
from .contracts import SUMMARY_INPUT_SCHEMA, SUMMARY_RECORD_SCHEMA, SUMMARY_SCHEMA


_MODEL_PERMISSIONS = frozenset({
    SkillPermission.NETWORK,
    SkillPermission.PAID_API,
})


class PaperSummarizeSkill(BaseSkill):
    """把单篇论文文本整理为问题、方法、贡献、局限和关键词。"""

    name = "paper_summarize"
    description = "生成单篇论文的完整结构化总结，模型不可用时自动本地降级。"
    version = "1.0.0"
    input_schema = {
        "type": "object",
        "properties": {
            **SUMMARY_INPUT_SCHEMA["properties"],
            "temperature": {"type": "number", "minimum": 0, "maximum": 2},
        },
        "additionalProperties": False,
    }
    output_schema = SUMMARY_SCHEMA
    permissions = _MODEL_PERMISSIONS
    default_timeout_seconds = 180.0

    def __init__(self, summarizer: Optional[Any] = None, *,
                 llm: Optional[Any] = None, max_chars: int = 16000) -> None:
        if summarizer is None:
            # 延迟导入，避免 skills 包与 core 门面产生循环依赖。
            from ..core.summarizer import PaperSummarizer
            summarizer = PaperSummarizer(llm=llm, max_chars=max_chars)
        self.summarizer = summarizer

    @property
    def available(self) -> bool:
        return bool(self.summarizer.available)

    def execute(self, text: str = "", title: Optional[str] = None,
                abstract: Optional[str] = None,
                temperature: float = 0.2) -> Dict[str, Any]:
        self.report_progress(10, "正在准备论文内容", stage="prepare")
        if not any(str(value or "").strip() for value in (text, abstract)):
            raise ValueError("论文正文和摘要不能同时为空")
        self.report_progress(
            25,
            "正在生成结构化总结" if self.available else "正在生成本地结构化总结",
            stage="summarize",
        )
        summary = self.summarizer.summarize(
            text=text, title=title, abstract=abstract,
            temperature=temperature)
        self.report_progress(95, "结构化总结已完成", stage="normalize")
        return summary

    # 保留与核心引擎相同的便捷接口，方便逐步迁移既有编排代码。
    def summarize(self, text: str, title: Optional[str] = None,
                  abstract: Optional[str] = None,
                  temperature: float = 0.2) -> Dict[str, Any]:
        return self.summarizer.summarize(
            text=text, title=title, abstract=abstract,
            temperature=temperature)

    def summarize_many(self, items: List[Dict[str, Any]],
                       max_workers: int = 2) -> List[Dict[str, Any]]:
        return self.summarizer.summarize_many(items, max_workers=max_workers)

    def estimate_cost_chars(self, items: List[Dict[str, Any]]) -> int:
        return int(self.summarizer.estimate_cost_chars(items))

    def complete_existing(self, summary: Optional[Dict[str, Any]], **kwargs: Any
                          ) -> Dict[str, Any]:
        return self.summarizer.complete_existing(summary, **kwargs)


class PaperSummarizeBatchSkill(BaseSkill):
    """批量总结论文，单篇失败时保留本地降级结果。"""

    name = "paper_summarize_batch"
    description = "批量生成论文结构化总结，并保持输入顺序和逐篇错误信息。"
    version = "1.0.0"
    input_schema = {
        "type": "object",
        "required": ["items"],
        "properties": {
            "items": {"type": "array", "minItems": 1,
                      "items": SUMMARY_INPUT_SCHEMA},
            "max_workers": {"type": "integer", "minimum": 1, "maximum": 8},
        },
        "additionalProperties": False,
    }
    output_schema = {"type": "array", "items": SUMMARY_RECORD_SCHEMA}
    permissions = _MODEL_PERMISSIONS
    default_timeout_seconds = 900.0

    def __init__(self, summarizer: Optional[Any] = None, *,
                 llm: Optional[Any] = None, max_chars: int = 16000) -> None:
        if summarizer is None:
            from ..core.summarizer import PaperSummarizer
            summarizer = PaperSummarizer(llm=llm, max_chars=max_chars)
        self.summarizer = summarizer

    def execute(self, items: List[Dict[str, Any]],
                max_workers: int = 2) -> List[Dict[str, Any]]:
        self.report_progress(
            10, f"准备总结 {len(items)} 篇论文", stage="prepare",
            current=0, total=len(items))
        results = self.summarizer.summarize_many(
            items, max_workers=max_workers)
        completed = sum(1 for item in results if item.get("summary"))
        self.report_progress(
            95, f"已完成 {completed}/{len(items)} 篇总结", stage="summarize",
            current=completed, total=len(items))
        return results
