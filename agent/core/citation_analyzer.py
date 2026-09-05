"""引用网络分析器（V4.0）。

输入：研究语料中的论文列表。
输出：
- top_cited:     语料参考文献中出现最多的核心文献（被引枢纽）
- intra_citations: 语料内部互引关系（哪些语料论文引用了语料内其他论文）
- coverage:      成功获取引用的论文数 / 总数
- errors:        失败的论文（限流/无 ID 等，尽力而为不阻塞）
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from ..skills.citation_skill import (CitationIdError, CitationNotFoundError,
                                     CitationRateLimitError, CitationSkill)
from ..skills.metadata import Paper
from ..skills.search_manager import _normalize_title


class CitationAnalyzer:
    """基于 Semantic Scholar 的引用网络分析。"""

    def __init__(self, skill: Optional[CitationSkill] = None,
                 max_refs_per_paper: int = 30,
                 max_fail_streak: int = 3,
                 recovery_retries: int = 1,
                 recovery_delay: float = 5.0) -> None:
        self.skill = skill or CitationSkill(max_per_paper=max_refs_per_paper)
        self.max_fail_streak = max_fail_streak
        self.recovery_retries = max(0, int(recovery_retries))
        self.recovery_delay = max(0.0, float(recovery_delay))

    # ------------------------------------------------------------------
    def analyze(self, papers: List[Paper]) -> Dict[str, Any]:
        """分析引用网络（尽力而为，限流/失败自动跳过）。"""
        if not papers:
            return {"top_cited": [], "intra_citations": [],
                    "coverage": 0, "errors": [], "_degraded": True}

        corpus_keys = {_normalize_title(p.title): p for p in papers}
        ref_counts: Dict[str, Dict[str, Any]] = {}
        intra: List[Dict[str, Any]] = []
        ok = 0
        errors: List[Dict[str, str]] = []
        pending: List[tuple[Paper, Exception]] = []
        fail_streak = 0
        recovered = 0

        for p in papers:
            try:
                refs = self.skill.get_references(p)
            except Exception as err:  # noqa: BLE001 - 尽力而为
                pending.append((p, err))
                fail_streak += 1
                if fail_streak == self.max_fail_streak:
                    print("[引用] 连续失败较多，失败项进入低速恢复队列；"
                          "继续检查其余文献")
                continue

            fail_streak = 0
            ok += 1
            self._collect(p, refs, corpus_keys, ref_counts, intra)

        # 单篇请求内部已经退避重试；这里再做低速恢复轮，应对整批处理中
        # 临时限流窗口刚好覆盖某几篇论文的情况。缺少可靠 ID 的论文不盲重试。
        for recovery_round in range(self.recovery_retries):
            retryable = [(p, err) for p, err in pending
                         if self._reason(err) not in {"missing_id", "not_found"}]
            permanent = [(p, err) for p, err in pending
                         if self._reason(err) in {"missing_id", "not_found"}]
            if not retryable:
                pending = permanent
                break
            if self.recovery_delay:
                time.sleep(self.recovery_delay * (recovery_round + 1))
            print(f"[引用] 恢复重试 {recovery_round + 1}/"
                  f"{self.recovery_retries}：{len(retryable)} 篇")
            still_failed: List[tuple[Paper, Exception]] = []
            for p, _old_err in retryable:
                try:
                    refs = self.skill.get_references(p)
                    ok += 1
                    recovered += 1
                    self._collect(p, refs, corpus_keys, ref_counts, intra)
                except Exception as err:  # noqa: BLE001
                    still_failed.append((p, err))
            pending = permanent + still_failed

        for paper, err in pending:
            errors.append({
                "title": paper.title[:80],
                "reason": self._reason(err),
                "message": str(err)[:240],
            })

        # 按被引次数排序，取核心文献
        ranked = sorted(ref_counts.values(),
                        key=lambda e: -e["cited_by"])
        top_cited = ranked[:10]
        error_stats = {
            reason: sum(1 for item in errors if item["reason"] == reason)
            for reason in ("missing_id", "not_found", "rate_limited",
                           "request_failed")
        }

        return {
            "top_cited": top_cited,
            "intra_citations": intra,
            "coverage": ok / len(papers) if papers else 0,
            "errors": errors,
            "analyzed_papers": ok,
            "total_papers": len(papers),
            "recovered_papers": recovered,
            "error_stats": error_stats,
            "_degraded": ok == 0,
        }

    @staticmethod
    def _reason(err: Exception) -> str:
        if isinstance(err, CitationIdError):
            return "missing_id"
        if isinstance(err, CitationNotFoundError):
            return "not_found"
        text = str(err).lower()
        if isinstance(err, CitationRateLimitError) or "429" in text \
                or "too many requests" in text:
            return "rate_limited"
        return "request_failed"

    @staticmethod
    def _collect(paper: Paper, refs: List[Paper],
                 corpus_keys: Dict[str, Paper],
                 ref_counts: Dict[str, Dict[str, Any]],
                 intra: List[Dict[str, Any]]) -> None:
        """合并一篇论文的引用结果。"""
        for ref in refs:
            key = _normalize_title(ref.title)
            if not key:
                continue
            entry = ref_counts.setdefault(key, {
                "title": ref.title,
                "year": ref.year,
                "venue": ref.venue,
                "url": ref.url,
                "cited_by": 0,
                "citing_papers": [],
            })
            entry["cited_by"] += 1
            entry["citing_papers"].append(paper.title)
            if key in corpus_keys:
                intra.append({
                    "citing": paper.title,
                    "cited": ref.title,
                    "type": "reference",
                })
