"""V3.0+ 深度研究闭环（Research Loop）。

多轮迭代研究：
    检索 → 摘要 → 跨文献分析 → 用盲点建议关键词触发下一轮检索
直至达到预算上限、不再产生新盲点或查询去重后无新内容，
最终合并全部轮次产出为一份深度研究报告。

V4.0 增强：
- 研究记忆持久化：跨会话跳过已检索查询，复用历史盲点延续研究
- 引用网络分析：识别核心被引文献与语料内互引关系

预算控制：
    max_rounds   最大轮数（默认 3）
    branching    每轮最多衍生几个盲点查询（默认 2）
    max_queries  总查询数上限（默认 7，防止 2^n 爆炸）
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from ..skills import CitationAnalysisSkill, ReportWriteSkill
from ..skills.metadata import Paper
from ..skills.search_manager import _normalize_title
from .agent import ResearchAgent
from .citation_analyzer import CitationAnalyzer
from .memory import ResearchMemory
from .reporter import Reporter


class ResearchLoop:
    """多轮自主研究驱动器（含记忆与引用分析）。"""

    def __init__(self, agent: Optional[ResearchAgent] = None,
                 reporter: Optional[Reporter] = None,
                 memory: Optional[ResearchMemory] = None,
                 citation_analyzer: Optional[CitationAnalyzer] = None,
                 max_rounds: int = 3,
                 branching: int = 2,
                 max_queries: int = 7,
                 use_memory: bool = True,
                 analyze_citations: bool = True) -> None:
        self.agent = agent or ResearchAgent()
        self.reporter = reporter or ReportWriteSkill()
        self.memory = memory or (ResearchMemory() if use_memory else None)
        self.citation_analyzer = (
            citation_analyzer or CitationAnalysisSkill())
        self.max_rounds = max(1, max_rounds)
        self.branching = max(1, branching)
        self.max_queries = max(1, max_queries)
        self.use_memory = use_memory
        self.analyze_citations = analyze_citations

    # ------------------------------------------------------------------
    def run(self, user_input: str, *, max_results: int = 5,
            checkpoint: Optional[Callable[[], None]] = None,
            event_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
            state_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
            resume_state: Optional[Dict[str, Any]] = None,
            **overrides: Any) -> Dict[str, Any]:
        """执行深度研究。

        Args:
            user_input: 初始研究主题。
            max_results: 每轮每查询的结果上限。
            **overrides: 透传给每轮 ResearchAgent.run 的参数
                （download / sources / year_from 等）。

        Returns:
            {"rounds": [...], "all_papers": [...], "citations": ...,
             "report_path": ..., "stats": {...}}
        """
        # A checkpoint is intentionally made only at safe orchestration
        # boundaries: before a query and after an entire query result has been
        # normalized.  A process can therefore resume after a crash/restart
        # without pretending an in-flight HTTP/model request was completed.
        restored = self._restore_state(resume_state)
        rounds: List[Dict[str, Any]] = restored["rounds"]
        visited: set = restored["visited"]
        total_queries = restored["total_queries"]
        memory_hits = 0
        memory_reuse: List[Dict[str, Any]] = []
        # App 显式开启下载时，先完成多轮检索和全局去重，再统一下载。
        # 这样 max_downloads 是整个研究任务的上限，而不是每个派生查询的上限。
        deferred_download = overrides.get("download") is True
        round_overrides = dict(overrides)
        excluded_titles = {
            _normalize_title(str(title)) for title in
            (overrides.get("exclude_titles") or [])
            if _normalize_title(str(title))
        }
        if deferred_download:
            round_overrides["download"] = False

        # 待执行查询队列：(query, origin, gap_info)
        queue: List[tuple] = restored["current_queue"] or [
            (user_input.strip(), "user", None)]
        root_query = restored["root_query"] or user_input.strip()
        start_round = restored["round_number"]
        carry_next: List[tuple] = restored["next_queue"]
        # 精确命中仍沿用原有的“跳过重复检索”策略；对于相近主题，则把已
        # 沉淀结论作为需要交叉验证的背景证据注入本次研究，不减少新检索。
        if self.use_memory and self.memory and root_query:
            reuse = self.memory.prepare_reuse(root_query, limit=3,
                                               exclude_query=root_query)
            memory_reuse = list(reuse.get("matches") or [])
            context = str(reuse.get("context") or "")
            if memory_reuse:
                names = "、".join(f"《{item.get('query', '')}》"
                                  for item in memory_reuse)
                print(f"[知识库] 复用历史研究：{names}")
                self._emit(event_callback, "memory_reuse", "复用历史研究", {
                    "items": memory_reuse,
                    "mode": "semantic",
                })
                prior_context = str(round_overrides.get("historical_context") or "").strip()
                round_overrides["historical_context"] = "\n\n".join(
                    item for item in (prior_context, context) if item)
        self._save_state(state_callback, root_query, rounds, visited,
                         total_queries, start_round, queue, carry_next,
                         phase="resume" if resume_state else "queued")

        for r in range(start_round, self.max_rounds + 1):
            self._checkpoint(checkpoint)
            if not queue:
                print("[闭环] 无待执行查询，研究结束")
                break

            next_queue: List[tuple] = list(carry_next)
            carry_next = []
            round_processed = 0

            for position, (query, origin, gap_info) in enumerate(queue):
                self._checkpoint(checkpoint)
                self._save_state(state_callback, root_query, rounds, visited,
                                 total_queries, r, queue[position:], next_queue,
                                 phase="before_query")
                if total_queries >= self.max_queries:
                    print(f"[闭环] 达到查询预算上限 ({self.max_queries})，停止")
                    break
                norm = query.lower().strip()
                if not norm or norm in visited:
                    continue
                visited.add(norm)
                round_processed += 1

                # 记忆命中：跳过重复检索，复用历史产出
                if (self.use_memory and self.memory
                        and self.memory.has_query(query)):
                    hist = self.memory.get_round(query)
                    self.memory.mark_reused(query)
                    print(f"[记忆] 命中历史查询 {query!r} "
                          f"（{hist['timestamp']}），复用 {len(hist['papers'])} 篇")
                    # 旧版记忆可能保存了空方法/贡献/局限或失败摘要；读取时
                    # 即时迁移为当前完整结构，避免重复查询继续生成旧报告。
                    historical_papers = list(hist["papers"])
                    historical_summaries = list(hist.get("summaries") or [])
                    if excluded_titles:
                        kept = [idx for idx, paper in enumerate(historical_papers)
                                if _normalize_title(paper.title) not in excluded_titles]
                        removed = len(historical_papers) - len(kept)
                        historical_papers = [historical_papers[idx] for idx in kept]
                        historical_summaries = [historical_summaries[idx]
                                                for idx in kept
                                                if idx < len(historical_summaries)]
                        if removed:
                            print(f"[人工介入] 已从研究记忆排除 {removed} 篇指定文献")
                    hist["summaries"] = self.agent.complete_summary_records(
                        historical_papers, historical_summaries)
                    rounds.append({
                        "round": r,
                        "query": query,
                        "origin": "memory",
                        "papers": historical_papers,
                        "summaries": hist["summaries"],
                        "analysis": hist["analysis"],
                    })
                    memory_reuse.append({
                        "query": hist.get("query", query),
                        "timestamp": hist.get("timestamp", ""),
                        "score": 1.0,
                        "matched_terms": [query],
                        "mode": "exact",
                        "paper_titles": [paper.title for paper in historical_papers[:3]],
                    })
                    self._emit(event_callback, "memory_reuse", "复用历史研究", {
                        "items": [memory_reuse[-1]], "mode": "exact",
                    })
                    memory_hits += 1
                    next_queue.extend(self._derive_queries(
                        hist.get("analysis"), visited, self.branching))
                    self._save_state(
                        state_callback, root_query, rounds, visited,
                        total_queries, r, queue[position + 1:], next_queue,
                        phase="query_completed")
                    continue

                total_queries += 1

                print(f"\n[Round {r}] 查询: {query!r} "
                      f"(来源: {origin})")
                result = self.agent.run(
                    query,
                    max_results=max_results,
                    summarize=True,
                    analyze=True,
                    report=False,  # 由闭环统一生成深度报告
                    checkpoint=checkpoint,
                    event_callback=event_callback,
                    **round_overrides,
                )

                record: Dict[str, Any] = {
                    "round": r,
                    "query": query,
                    "origin": origin,
                    "papers": result["papers"],
                    "summaries": result["summaries"] or [],
                    "analysis": result["analysis"],
                }
                rounds.append(record)

                # 记忆持久化本轮成果
                if self.use_memory and self.memory:
                    self.memory.add_round(
                        query, result["papers"],
                        summaries=result.get("summaries"),
                        analysis=result.get("analysis"))

                # 从本轮分析中收集盲点建议，作为下一轮查询
                next_queue.extend(self._derive_queries(
                    result.get("analysis"), visited, self.branching))
                self._save_state(
                    state_callback, root_query, rounds, visited,
                    total_queries, r, queue[position + 1:], next_queue,
                    phase="query_completed")

            if round_processed == 0:
                print("[闭环] 本轮无新查询，研究结束")
                break

            queue = next_queue
            self._save_state(state_callback, root_query, rounds, visited,
                             total_queries, r + 1, queue, [],
                             phase="round_completed")

        # 全局去重（跨轮合并，保留首现）
        all_papers = self._merge_all(rounds)

        # 深度模式只对全局去重后的论文下载一次，避免同文重复请求和站点限流。
        acquisition: Optional[Dict[str, Any]] = None
        if deferred_download and all_papers:
            self._checkpoint(checkpoint)
            print(f"\n[下载] 全局去重后共 {len(all_papers)} 篇，开始限速下载 ...")
            acquisition = self.agent.acquisition_plugin.run(
                all_papers,
                max_downloads=overrides.get("max_downloads"),
                delay_seconds=float(overrides.get("download_interval", 1.5)),
                checkpoint=checkpoint)
            self._emit(event_callback, "download_result", "文献下载结果", {
                "stats": (acquisition or {}).get("stats", {}),
                "items": (acquisition or {}).get("items", []),
            })

        # 引用网络分析
        citations: Optional[Dict[str, Any]] = None
        if self.analyze_citations and all_papers:
            self._checkpoint(checkpoint)
            print(f"\n[引用] 分析 {len(all_papers)} 篇语料的引用网络 ...")
            citations = self.citation_analyzer.analyze(all_papers)
            print(f"[引用] 完成：覆盖 {citations['coverage']:.0%} | "
                  f"核心文献 {len(citations.get('top_cited', []))} | "
                  f"互引 {len(citations.get('intra_citations', []))}")
            error_stats = citations.get("error_stats") or {}
            if citations.get("recovered_papers"):
                print(f"[引用] 自动恢复 "
                      f"{citations['recovered_papers']} 篇临时失败文献")
            if citations.get("errors"):
                print("[引用] 未完成分类："
                      f"缺少ID {error_stats.get('missing_id', 0)} | "
                      f"未收录 {error_stats.get('not_found', 0)} | "
                      f"限流 {error_stats.get('rate_limited', 0)} | "
                      f"网络/服务异常 {error_stats.get('request_failed', 0)}")
            self._emit(event_callback, "citation_output", "引用网络分析输出", {
                "citations": citations,
            })

        # 生成深度研究报告
        meta = {
            "root_query": root_query,
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "rounds": len(rounds),
            "queries": total_queries,
            "memory_hits": memory_hits,
            "memory_reuse": memory_reuse,
            "papers_raw": sum(len(rec["papers"]) for rec in rounds),
            "papers_dedup": len(all_papers),
        }
        self._checkpoint(checkpoint)
        report_path = self.reporter.write_deep(
            meta, rounds, all_papers, citations=citations,
            acquisition=acquisition)
        self._emit(event_callback, "report", "报告已生成", {
            "report_path": str(report_path),
        })

        stats = dict(meta)
        stats["report_path"] = str(report_path)
        if acquisition:
            stats["downloads"] = acquisition.get("stats", {})
        print(f"\n=== 深度研究完成 ===")
        print(f"轮次 {meta['rounds']} | 新查询 {meta['queries']} | "
              f"记忆命中 {meta['memory_hits']} | "
              f"历史复用 {len(memory_reuse)} | "
              f"文献 {meta['papers_raw']} → 去重 {meta['papers_dedup']}")
        print(f"报告: {report_path}")
        self._save_state(state_callback, root_query, rounds, visited,
                         total_queries, self.max_rounds + 1, [], [],
                         phase="done")

        return {
            "rounds": rounds,
            "all_papers": all_papers,
            "citations": citations,
            "acquisition": acquisition,
            "report_path": str(report_path),
            "stats": stats,
        }

    @staticmethod
    def _checkpoint(checkpoint: Optional[Callable[[], None]]) -> None:
        if checkpoint is not None:
            checkpoint()

    @staticmethod
    def _emit(callback: Optional[Callable[[Dict[str, Any]], None]], kind: str,
              title: str, data: Dict[str, Any]) -> None:
        if callback is None:
            return
        try:
            callback({"kind": kind, "title": title, "data": data})
        except Exception:
            pass

    @classmethod
    def _restore_state(cls, raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Restore only a validated safe checkpoint; corrupt state is ignored."""
        from ..skills.metadata import Paper

        state = raw if isinstance(raw, dict) else {}
        restored_rounds: List[Dict[str, Any]] = []
        for item in state.get("rounds") or []:
            if not isinstance(item, dict):
                continue
            papers = []
            for raw_paper in item.get("papers") or []:
                if isinstance(raw_paper, Paper):
                    papers.append(raw_paper)
                elif isinstance(raw_paper, dict):
                    try:
                        papers.append(Paper.from_dict(raw_paper))
                    except (TypeError, ValueError):
                        continue
            restored_rounds.append({
                "round": int(item.get("round") or 1),
                "query": str(item.get("query") or ""),
                "origin": str(item.get("origin") or "user"),
                "papers": papers,
                "summaries": list(item.get("summaries") or []),
                "analysis": (item.get("analysis") if isinstance(
                    item.get("analysis"), dict) else None),
            })

        def queue(value: Any) -> List[tuple]:
            result: List[tuple] = []
            if not isinstance(value, list):
                return result
            for item in value:
                if not isinstance(item, dict):
                    continue
                query = str(item.get("query") or "").strip()
                if query:
                    gap = item.get("gap")
                    result.append((query, str(item.get("origin") or "user"),
                                   gap if isinstance(gap, dict) else None))
            return result

        return {
            "rounds": restored_rounds,
            "visited": {str(item).strip().lower() for item in
                        (state.get("visited") or []) if str(item).strip()},
            "total_queries": max(0, int(state.get("total_queries") or 0)),
            "root_query": str(state.get("root_query") or "").strip(),
            "round_number": max(1, int(state.get("round_number") or 1)),
            "current_queue": queue(state.get("current_queue")),
            "next_queue": queue(state.get("next_queue")),
        }

    @classmethod
    def _save_state(cls, callback: Optional[Callable[[Dict[str, Any]], None]],
                    root_query: str, rounds: List[Dict[str, Any]], visited: set,
                    total_queries: int, round_number: int,
                    current_queue: List[tuple], next_queue: List[tuple], *,
                    phase: str) -> None:
        if callback is None:
            return
        try:
            serial_rounds = []
            for record in rounds:
                serial_rounds.append({
                    "round": record.get("round"), "query": record.get("query"),
                    "origin": record.get("origin"),
                    "papers": [paper.to_dict() for paper in
                               record.get("papers") or []],
                    "summaries": record.get("summaries") or [],
                    "analysis": record.get("analysis"),
                })

            def serial_queue(items: List[tuple]) -> List[Dict[str, Any]]:
                return [{"query": query, "origin": origin, "gap": gap}
                        for query, origin, gap in items]

            callback({
                "version": 1, "mode": "deep", "phase": phase,
                "root_query": root_query, "round_number": round_number,
                "rounds": serial_rounds, "visited": sorted(visited),
                "total_queries": total_queries,
                "current_queue": serial_queue(current_queue),
                "next_queue": serial_queue(next_queue),
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
        except Exception:
            pass

    # ------------------------------------------------------------------
    @staticmethod
    def _derive_queries(analysis: Optional[Dict[str, Any]],
                        visited: set, branching: int) -> List[tuple]:
        """从分析结果中提取盲点建议查询。

        注意：不把派生查询加入 visited —— visited 表示"已执行"，
        派生查询只是"待执行"，会在下一轮循环中执行并登记。
        """
        if not analysis or analysis.get("_fallback"):
            return []
        gaps = analysis.get("gaps") or []
        out: List[tuple] = []
        for g in gaps:
            if len(out) >= branching:
                break
            q = (g.get("suggested_query") or "").strip()
            if q and q.lower().strip() not in visited:
                out.append((q, "gap", g))
        if out:
            print(f"[闭环] 从盲点衍生 {len(out)} 个新查询")
        return out

    @staticmethod
    def _merge_all(rounds: List[Dict[str, Any]]) -> List[Paper]:
        """跨轮去重合并论文（保留首现，按年份倒序）。"""
        seen: Dict[str, Paper] = {}
        for rec in rounds:
            for p in rec["papers"]:
                key = _normalize_title(p.title)
                if key and key not in seen:
                    seen[key] = p
        merged = list(seen.values())
        merged.sort(key=lambda p: -(p.year or 0))
        return merged
