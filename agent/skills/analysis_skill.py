"""跨论文对比与研究空白分析 Skill。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import BaseSkill, SkillPermission
from .contracts import ANALYSIS_SCHEMA, PAPER_PROFILE_SCHEMA


class PaperCompareSkill(BaseSkill):
    """对多个论文画像生成共识、分歧、演进和研究空白。"""

    name = "paper_compare"
    description = "跨论文比较方法与结论，输出共识、分歧、演进路径和知识盲点。"
    version = "1.0.0"
    input_schema = {
        "type": "object",
        "required": ["profiles"],
        "properties": {
            "profiles": {"type": "array", "minItems": 1,
                         "items": PAPER_PROFILE_SCHEMA},
            "temperature": {"type": "number", "minimum": 0, "maximum": 2},
        },
        "additionalProperties": False,
    }
    output_schema = ANALYSIS_SCHEMA
    permissions = frozenset({
        SkillPermission.NETWORK,
        SkillPermission.PAID_API,
    })
    default_timeout_seconds = 300.0

    def __init__(self, analyzer: Optional[Any] = None, *,
                 llm: Optional[Any] = None, max_papers: int = 10) -> None:
        if analyzer is None:
            from ..core.analyzer import CrossPaperAnalyzer
            analyzer = CrossPaperAnalyzer(llm=llm, max_papers=max_papers)
        self.analyzer = analyzer

    @property
    def available(self) -> bool:
        return bool(self.analyzer.available)

    def execute(self, profiles: List[Dict[str, Any]],
                temperature: float = 0.3) -> Dict[str, Any]:
        self.report_progress(
            10, f"正在准备 {len(profiles)} 篇论文画像", stage="prepare",
            current=0, total=len(profiles))
        self.report_progress(
            30,
            "正在进行跨论文对比" if self.available else "正在进行本地跨论文对比",
            stage="compare",
        )
        result = self.analyzer.analyze(
            profiles=profiles, temperature=temperature)
        self.report_progress(
            95,
            f"对比完成：共识 {len(result.get('consensus', []))}，"
            f"分歧 {len(result.get('conflicts', []))}，"
            f"盲点 {len(result.get('gaps', []))}",
            stage="normalize",
        )
        return result

    def analyze(self, profiles: List[Dict[str, Any]],
                temperature: float = 0.3) -> Dict[str, Any]:
        return self.analyzer.analyze(
            profiles=profiles, temperature=temperature)
