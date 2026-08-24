"""多源搜索聚合管理器。

职责：
1. 并发/串行调度多个搜索技能（arXiv、Scholar 等）。
2. 合并结果并按来源优先级 + 年份排序。
3. 基于标题归一化去重（同文不同源只保留最高优先级来源）。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .arxiv_skill import ArxivSkill
from .base import BaseSkill
from .metadata import Paper
from .scholar_skill import ScholarSkill

#: 来源优先级（数值越小越优先）
_SOURCE_PRIORITY: Dict[str, int] = {
    "arxiv_search": 0,
    "scholar_search": 1,
}

logger = logging.getLogger(__name__)


def _normalize_title(title: str) -> str:
    """标题归一化：小写、去标点、折叠空白，用于去重。"""
    return "".join(ch for ch in title.lower() if ch.isalnum())


class SearchManager:
    """聚合多个搜索技能，输出统一、去重、排序后的 Paper 列表。"""

    def __init__(self, skills: Optional[Sequence[BaseSkill]] = None,
                 max_workers: int = 4) -> None:
        self.skills: List[BaseSkill] = (
            list(skills) if skills else [ArxivSkill(), ScholarSkill()])
        self.max_workers = max_workers

    # ------------------------------------------------------------------
    def search(self, query: str, max_results: int = 10,
               sources: Optional[List[str]] = None,
               **skill_kwargs: object) -> List[Paper]:
        """并行执行所有启用的搜索技能并聚合结果。

        Args:
            query: 搜索关键词。
            max_results: 每个来源的结果上限。
            sources: 只启用指定来源（技能名），None 表示全部。
            skill_kwargs: 透传给技能的额外参数（如 year_from）。
        """
        papers, _warnings = self.search_with_diagnostics(
            query=query, max_results=max_results, sources=sources,
            **skill_kwargs)
        return papers

    def search_with_diagnostics(
            self, query: str, max_results: int = 10,
            sources: Optional[List[str]] = None,
            **skill_kwargs: object) -> Tuple[List[Paper], List[Dict[str, Any]]]:
        """执行搜索并返回可供 MCP/UI 呈现的单源失败诊断。"""
        enabled = self._filter_skills(sources)
        if not enabled:
            raise ValueError("没有可用的搜索技能")

        papers: List[Paper] = []
        warnings: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=min(self.max_workers,
                                                len(enabled))) as pool:
            futures = {
                pool.submit(skill.execute, query=query,
                            max_results=max_results, **skill_kwargs): skill
                for skill in enabled
            }
            for future in as_completed(futures):
                skill = futures[future]
                try:
                    result = future.result()
                except Exception as err:  # noqa: BLE001 - 单源失败不阻塞整体
                    warning = {"source": skill.name, "message": str(err)}
                    warnings.append(warning)
                    # stdio MCP 的 stdout 必须保持协议纯净，日志只走 stderr。
                    logger.warning("技能 %s 失败: %s", skill.name, err)
                    continue
                if result:
                    papers.extend(result)

        return self._merge(papers), warnings

    # ------------------------------------------------------------------
    def _filter_skills(self, sources: Optional[List[str]]) -> List[BaseSkill]:
        if not sources:
            return list(self.skills)
        return [s for s in self.skills if s.name in sources]

    def _merge(self, papers: List[Paper]) -> List[Paper]:
        """去重 + 排序。"""
        seen: Dict[str, Paper] = {}
        for p in papers:
            key = _normalize_title(p.title)
            if not key:
                continue
            existing = seen.get(key)
            if existing is None:
                seen[key] = p
            else:
                # 保留来源优先级更高的那条
                if (_SOURCE_PRIORITY.get(p.source, 9)
                        < _SOURCE_PRIORITY.get(existing.source, 9)):
                    seen[key] = p
        merged = list(seen.values())
        merged.sort(key=lambda p: (
            _SOURCE_PRIORITY.get(p.source, 9),
            -(p.year or 0),  # 年份新者在前
        ))
        return merged
