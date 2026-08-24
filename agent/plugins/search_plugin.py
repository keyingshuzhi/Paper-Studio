"""插件一：多源综合搜索（Comprehensive Source Search）。

流程：用户关键词 → 并行调度多个搜索技能 → 去重排序 → 输出 Paper 列表。
"""

from __future__ import annotations

from typing import Any, List, Optional

from ..skills import SearchManager
from ..skills.metadata import Paper
from .base import BasePlugin


class ComprehensiveSourceSearch(BasePlugin):
    """跨来源综合搜索插件。"""

    name = "comprehensive_search"
    description = "跨 arXiv / Semantic Scholar / Crossref 综合检索学术文献。"

    def __init__(self, manager: Optional[SearchManager] = None) -> None:
        self.manager = manager or SearchManager()

    def run(self, query: str, max_results: int = 10,
            sources: Optional[List[str]] = None, **kwargs: Any) -> List[Paper]:
        """执行综合搜索。

        Args:
            query: 搜索关键词。
            max_results: 每个来源的结果上限。
            sources: 指定来源白名单（技能名），None 表示全部。
            **kwargs: 透传给搜索技能的参数（如 year_from）。
        """
        papers = self.manager.search(
            query, max_results=max_results, sources=sources, **kwargs)
        return papers
