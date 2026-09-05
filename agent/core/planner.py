"""任务规划器（MCP 层：思考与决策）。

V1.0 采用「基于规则的意图解析」：
- 从用户输入中识别指令词（如"下载"）与纯净关键词。
- 生成可执行的 ResearchPlan。
- 接口设计为可替换：后续可无缝接入 LLM 做更复杂的规划。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, List, Optional

#: 触发下载行为的指令词
_DOWNLOAD_HINTS = ("下载", "download", "pdf", "原文", "全文")
#: 触发报告生成的指令词（默认总是生成）
_REPORT_HINTS = ("报告", "report", "总结", "总结报告")
_LEARNING_RESOURCE_HINTS = ("学习资料", "学习资源", "课程", "教材", "书籍", "学习路线")
#: 从查询中剥离的"命令词"（避免污染检索关键词）
_COMMAND_WORDS = (
    "请", "帮我", "搜", "搜索", "查找", "找", "查一下", "下载", "论文",
    "报告", "总结", "关于", "有关", "相关", "的研究", "的论文", "的资料",
    "download", "pdf", "report",
)


@dataclass
class ResearchPlan:
    """一次研究任务的完整执行计划。"""

    query: str                          # 纯净检索关键词
    original_query: str                 # 用户原始输入
    max_results: int = 10               # 每个来源的结果上限
    sources: Optional[List[str]] = None  # 来源白名单（None=全部）
    download: bool = False              # 是否下载原文
    max_downloads: Optional[int] = None  # 最多下载篇数
    report: bool = True                 # 是否生成报告
    year_from: Optional[int] = None     # 只检索该年份及之后的文献
    extra: dict = field(default_factory=dict)


class Planner:
    """把用户输入解析为 ResearchPlan。"""

    def make_plan(self, user_input: str, *, max_results: int = 10,
                  sources: Optional[List[str]] = None,
                  download: Optional[bool] = None,
                  max_downloads: Optional[int] = None,
                  report: Optional[bool] = None,
                  **_: Any) -> ResearchPlan:
        """解析用户输入，生成执行计划。

        Args:
            user_input: 用户原始输入，如 "帮我下载关于transformer的论文"。
            download: 显式覆盖是否下载；None 则自动从指令词推断。
            report: 显式覆盖是否生成报告；None 则自动推断（默认生成）。
        """
        text = user_input.strip()
        if not text:
            raise ValueError("输入不能为空")

        if download is None:
            download = any(h in text.lower() for h in _DOWNLOAD_HINTS)

        if report is None:
            report = True  # V1.0 默认总是产出报告

        query = self._extract_keywords(text)

        plan = ResearchPlan(
            query=query,
            original_query=text,
            max_results=max_results,
            sources=sources,
            download=download,
            max_downloads=max_downloads,
            report=report,
        )
        if any(hint in text.lower() for hint in _LEARNING_RESOURCE_HINTS):
            plan.extra["skill"] = "学习资料汇总"
        return plan

    # ------------------------------------------------------------------
    @staticmethod
    def _extract_keywords(text: str) -> str:
        """从用户输入中提取纯净关键词。

        策略：小写化 → 剥离命令词 → 折叠空白。
        注意：剥离是"尽力而为"，复杂句法需 LLM 规划器（V2.0）接管。
        """
        lowered = text.lower()
        for word in sorted(_COMMAND_WORDS, key=len, reverse=True):
            lowered = lowered.replace(word, " ")
        lowered = re.sub(r"[，。！？、；：,.!?;:()（）\"'\"'「」【】]", " ", lowered)
        lowered = re.sub(r"\s+", " ", lowered).strip()
        return lowered or text.strip()
