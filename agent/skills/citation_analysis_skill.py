"""论文语料引用网络分析 Skill。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import BaseSkill, SkillPermission
from .contracts import CITATION_ANALYSIS_SCHEMA
from .metadata import PAPER_SCHEMA, Paper


class CitationAnalysisSkill(BaseSkill):
    """聚合逐篇引用数据，识别核心被引文献和语料内部互引。"""

    name = "citation_analyze"
    description = "分析论文语料的引用网络、覆盖率、核心被引文献与失败原因。"
    version = "1.0.0"
    input_schema = {
        "type": "object",
        "required": ["papers"],
        "properties": {
            "papers": {"type": "array", "items": PAPER_SCHEMA},
        },
        "additionalProperties": False,
    }
    output_schema = CITATION_ANALYSIS_SCHEMA
    permissions = frozenset({SkillPermission.NETWORK})
    default_timeout_seconds = 900.0

    def __init__(self, analyzer: Optional[Any] = None, *,
                 citation_skill: Optional[Any] = None,
                 max_refs_per_paper: int = 30,
                 max_fail_streak: int = 3,
                 recovery_retries: int = 1,
                 recovery_delay: float = 5.0) -> None:
        if analyzer is None:
            from ..core.citation_analyzer import CitationAnalyzer
            analyzer = CitationAnalyzer(
                skill=citation_skill,
                max_refs_per_paper=max_refs_per_paper,
                max_fail_streak=max_fail_streak,
                recovery_retries=recovery_retries,
                recovery_delay=recovery_delay,
            )
        self.analyzer = analyzer

    def execute(self, papers: List[Paper]) -> Dict[str, Any]:
        normalized = [_coerce_paper(paper) for paper in papers]
        self.report_progress(
            10, f"准备分析 {len(normalized)} 篇论文的引用网络",
            stage="prepare", current=0, total=len(normalized))
        result = self.analyzer.analyze(normalized)
        analyzed = int(result.get("analyzed_papers") or 0)
        self.report_progress(
            95,
            f"引用分析完成，覆盖 {result.get('coverage', 0):.0%}",
            stage="aggregate", current=analyzed, total=len(normalized))
        return result

    def analyze(self, papers: List[Paper]) -> Dict[str, Any]:
        return self.analyzer.analyze([_coerce_paper(paper) for paper in papers])


def _coerce_paper(value: Paper | Dict[str, Any]) -> Paper:
    if isinstance(value, Paper):
        return value
    if isinstance(value, dict):
        return Paper.from_dict(value)
    raise TypeError(f"无效论文对象: {type(value).__name__}")
