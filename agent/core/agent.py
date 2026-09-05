"""研究助理 Agent（MCP 控制层门面）。

职责：接收用户输入 → 规划(Planner) → 调度插件(Plugins)
      → 摘要+跨文献分析(MCP 认知) → 生成报告(Reporter)。
这是整个智能体的总入口，把 Skills 与 Plugins 串联成完整业务闭环。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..plugins import (ComprehensiveSourceSearch, DataAcquisitionPipeline,
                       BasePlugin)
from ..skills import PaperCompareSkill, PaperSummarizeSkill, ReportWriteSkill
from ..skills.metadata import Paper
from .analyzer import CrossPaperAnalyzer
from .llm_planner import LLMPlanner
from .planner import Planner, ResearchPlan
from .reporter import Reporter
from .summarizer import PaperSummarizer


class ResearchAgent:
    """学术研究助理 Agent（V1.0 + LLM 规划 + 智能摘要 + 跨文献分析）。"""

    def __init__(self, planner: Optional[Planner] = None,
                 search_plugin: Optional[BasePlugin] = None,
                 acquisition_plugin: Optional[BasePlugin] = None,
                 reporter: Optional[Reporter] = None,
                 summarizer: Optional[PaperSummarizer] = None,
                 analyzer: Optional[CrossPaperAnalyzer] = None) -> None:
        # 默认使用 LLM 规划器（未配置 Key 时内部自动降级为规则规划）
        self.planner = planner or LLMPlanner()
        self.search_plugin = search_plugin or ComprehensiveSourceSearch()
        self.acquisition_plugin = (
            acquisition_plugin or DataAcquisitionPipeline())
        # 默认通过标准 Skill 适配层调用核心引擎；显式注入旧引擎仍保持兼容。
        self.reporter = reporter or ReportWriteSkill()
        self.summarizer = summarizer or PaperSummarizeSkill()
        self.analyzer = analyzer or PaperCompareSkill()

    # ------------------------------------------------------------------
    def run(self, user_input: str, *,
            summarize: bool = False,
            summarize_limit: Optional[int] = None,
            analyze: bool = False,
            checkpoint: Optional[Callable[[], None]] = None,
            event_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
            **overrides: Any) -> Dict[str, Any]:
        """执行一次完整的研究任务。

        Args:
            user_input: 用户输入，如 "帮我下载近三年关于transformer的论文"。
            summarize: 是否生成 LLM 智能摘要（问题/方法/贡献/局限）。
            summarize_limit: 最多摘要几篇（None 表示全部）。
            analyze: 是否进行跨文献分析（共识/分歧/演进/知识盲点）。
            **overrides: 覆盖计划参数的选项
                （max_results / sources / download / max_downloads /
                 report / year_from）。

        Returns:
            结构化结果：
            {plan, papers, acquisition, summaries, analysis, report_path}
        """
        self._checkpoint(checkpoint)
        self._event(event_callback, "research_input", "研究输入", {
            "query": user_input,
            "sources": overrides.get("sources"),
            "year_from": overrides.get("year_from"),
            "max_results": overrides.get("max_results"),
            "exclude_titles": list(overrides.get("exclude_titles") or []),
            "research_direction": str(overrides.get("research_direction") or ""),
        })
        direction = str(overrides.get("research_direction") or "").strip()
        historical_context = str(overrides.get("historical_context") or "").strip()
        effective_input = user_input
        if direction:
            effective_input = (
                f"{user_input}\n\n补充研究方向（需要纳入检索与分析）：{direction}")
            self._event(event_callback, "intervention", "已应用补充方向", {
                "research_direction": direction,
            })
        if historical_context:
            # 历史内容是可复用证据，而不是新检索结果；在规划阶段明确要求模型
            # 用本次文献交叉核验，可避免把旧结论误当作事实回声。
            effective_input += f"\n\n历史研究复用（需交叉验证）：\n{historical_context}"
            self._event(event_callback, "memory_reuse", "复用历史研究结论", {
                "context_chars": len(historical_context),
            })
        # ``existing_papers`` is deliberately an explicit opt-in.  It lets a
        # user continue a line of inquiry from their local library without
        # silently mixing fresh search results into the evidence set.
        raw_existing = overrides.get("existing_papers")
        existing_papers: List[Paper] = []
        if isinstance(raw_existing, list):
            for raw in raw_existing[:50]:
                try:
                    existing_papers.append(
                        raw if isinstance(raw, Paper) else Paper.from_dict(raw))
                except (TypeError, ValueError):
                    continue
        if raw_existing is not None and not existing_papers:
            raise ValueError("未找到可用于继续研究的本地文献")

        plan: ResearchPlan = self.planner.make_plan(effective_input, **overrides)
        if existing_papers:
            # Downloading here would be surprising: these records represent a
            # user-selected, already local collection.  Keep the source set
            # closed and only read/compare what the user selected.
            plan.download = False
        print(f"[规划] 模式={plan.extra.get('planner', '?')} "
              f"| 关键词={plan.query!r} | 下载={plan.download} "
              f"| 来源={plan.sources or '全部'} "
              f"| 年份>={plan.year_from or '不限'}")
        self._event(event_callback, "plan", "检索计划", {
            "query": plan.query,
            "original_query": plan.original_query,
            "sources": plan.sources,
            "max_results": plan.max_results,
            "year_from": plan.year_from,
            "download": plan.download,
            "planner": plan.extra.get("planner"),
        })

        # 1) 综合搜索，或由用户明确选中的本地文献继续研究。
        self._checkpoint(checkpoint)
        if existing_papers:
            papers = existing_papers
            print(f"[文献库] 复用 {len(papers)} 篇已选本地文献，不重新检索")
            self._event(event_callback, "library_evidence", "复用本地文献", {
                "total": len(papers),
                "papers": [paper.to_dict() for paper in papers],
            })
        else:
            papers = self.search_plugin.run(
                query=plan.query,
                max_results=plan.max_results,
                sources=plan.sources,
            )

        # 年份过滤（LLM 解析出的 year_from）
        if plan.year_from:
            papers = [p for p in papers if (p.year or 0) >= plan.year_from]
        excluded = {
            self._title_key(title) for title in overrides.get("exclude_titles", [])
            if self._title_key(title)
        }
        if excluded:
            before = len(papers)
            papers = [paper for paper in papers
                      if self._title_key(paper.title) not in excluded]
            removed = before - len(papers)
            if removed:
                print(f"[人工介入] 已排除 {removed} 篇指定文献")
                self._event(event_callback, "intervention", "已应用文献排除", {
                    "removed": removed,
                    "excluded_titles": list(overrides.get("exclude_titles") or []),
                })
        if not existing_papers:
            print(f"[搜索] 命中 {len(papers)} 篇去重文献")
            self._event(event_callback, "search_results", "检索结果", {
                "query": plan.query,
                "total": len(papers),
                "papers": [paper.to_dict() for paper in papers],
            })

        # 2) 数据获取（Plugins: data_acquisition，可选）
        acquisition: Optional[Dict[str, Any]] = None
        if plan.download and papers:
            self._checkpoint(checkpoint)
            print("[下载] 开始下载论文原文 ...")
            acquisition = self.acquisition_plugin.run(
                papers,
                max_downloads=plan.max_downloads,
                delay_seconds=float(overrides.get("download_interval", 1.5)),
                checkpoint=checkpoint)
            self._event(event_callback, "download_result", "文献下载结果", {
                "stats": (acquisition or {}).get("stats", {}),
                "items": (acquisition or {}).get("items", []),
            })

        # 3) 智能摘要（MCP 认知能力，可选）
        summaries: Optional[List[Dict[str, Any]]] = None
        if summarize and papers:
            self._checkpoint(checkpoint)
            summaries = self._run_summaries(papers, acquisition,
                                            summarize_limit,
                                            event_callback=event_callback)

        # 4) 跨文献分析（MCP 综述专家能力，可选）
        analysis: Optional[Dict[str, Any]] = None
        if analyze and papers:
            self._checkpoint(checkpoint)
            analysis = self._run_analysis(
                papers, summaries, event_callback=event_callback)

        # 给单轮报告也留下可审计的复用来源。深度报告由 ResearchLoop 在
        # meta 中单独呈现，二者共用相同的数据结构。
        historical_reuse = overrides.get("historical_reuse") or []
        if isinstance(historical_reuse, list) and historical_reuse:
            analysis = dict(analysis or {})
            analysis["historical_reuse"] = historical_reuse

        # 5) 报告生成（MCP 输出）
        report_path = None
        if plan.report:
            self._checkpoint(checkpoint)
            report_path = self.reporter.write(
                plan, papers, acquisition, summaries, analysis)
            print(f"[报告] 已生成: {report_path}")
            self._event(event_callback, "report", "报告已生成", {
                "report_path": str(report_path),
            })

        return {
            "plan": plan,
            "papers": papers,
            "acquisition": acquisition,
            "summaries": summaries,
            "analysis": analysis,
            "report_path": str(report_path) if report_path else None,
            "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    @staticmethod
    def _checkpoint(checkpoint: Optional[Callable[[], None]]) -> None:
        """给宿主提供安全的暂停/取消边界；未传入时保持原有行为。"""
        if checkpoint is not None:
            checkpoint()

    @staticmethod
    def _title_key(title: Any) -> str:
        return " ".join(str(title or "").casefold().split())

    @staticmethod
    def _event(callback: Optional[Callable[[Dict[str, Any]], None]],
               kind: str, title: str, data: Dict[str, Any]) -> None:
        """Emit inspectable workflow data without coupling core to Web UI."""
        if callback is None:
            return
        try:
            callback({"kind": kind, "title": title, "data": data})
        except Exception:
            # A telemetry consumer must not interrupt research execution.
            pass

    # ------------------------------------------------------------------
    def _run_summaries(self, papers: List[Paper],
                       acquisition: Optional[Dict[str, Any]],
                       limit: Optional[int],
                       event_callback: Optional[Callable[[Dict[str, Any]], None]] = None
                       ) -> List[Dict[str, Any]]:
        """为论文批量生成智能摘要。

        文本来源优先级：
        1. 已下载的抽取文本（acquisition.texts）
        2. 元数据中的摘要（abstract）
        """
        # 文本 → 论文序号 映射
        text_by_idx: Dict[int, str] = {}
        if acquisition:
            for it in acquisition.get("items", []):
                tp = it.get("text_path")
                if tp and it.get("status") == "ok":
                    try:
                        text_by_idx[it["index"]] = Path(tp).read_text(
                            encoding="utf-8")
                    except OSError:
                        pass

        targets = papers[:limit] if limit else papers
        items: List[Dict[str, Any]] = []
        for i, p in enumerate(targets, 1):
            items.append({
                "title": p.title,
                "abstract": p.abstract,
                "text": text_by_idx.get(i, ""),
            })

        chars = self.summarizer.estimate_cost_chars(items)
        print(f"[摘要] 计划摘要 {len(items)} 篇，"
              f"输入约 {chars / 1000:.0f}k 字符 ...")
        if self.summarizer.available:
            results = self.summarizer.summarize_many(items)
        else:
            print("[摘要] 模型当前不可用，使用摘要/正文生成本地结构化摘要")
            results = []
        results = self.complete_summary_records(targets, results, text_by_idx)
        ok = sum(1 for r in results if r.get("ok"))
        fallback = sum(1 for r in results if r.get("fallback"))
        suffix = f"（{fallback} 篇使用本地降级摘要）" if fallback else ""
        print(f"[摘要] 完成 {ok}/{len(results)} 篇{suffix}")
        self._event(event_callback, "summary_output", "结构化摘要输出", {
            "completed": ok,
            "total": len(results),
            "fallback": fallback,
            "summaries": results,
        })
        return results

    def complete_summary_records(
            self, papers: List[Paper], records: Optional[List[Dict[str, Any]]],
            text_by_idx: Optional[Dict[int, str]] = None
    ) -> List[Dict[str, Any]]:
        """保证每篇论文都有与云端一致的完整摘要结构。

        同时用于新任务结果和历史记忆结果，避免旧记录中的空字段、失败项
        或数组文本直接进入报告。
        """
        source_records = records or []
        completed: List[Dict[str, Any]] = []
        for idx, paper in enumerate(papers, 1):
            source = (source_records[idx - 1]
                      if idx - 1 < len(source_records)
                      and isinstance(source_records[idx - 1], dict) else {})
            original_summary = source.get("summary")
            summary = self.summarizer.complete_existing(
                original_summary if isinstance(original_summary, dict) else None,
                text=(text_by_idx or {}).get(idx, ""),
                title=paper.title,
                abstract=paper.abstract,
            )
            used_fallback = (not source.get("ok") or not original_summary
                             or bool(source.get("fallback")))
            record: Dict[str, Any] = {
                "ok": True,
                "summary": summary,
                "error": source.get("error"),
            }
            if used_fallback:
                record["fallback"] = True
                summary["_fallback"] = True
            completed.append(record)
        return completed

    # ------------------------------------------------------------------
    def _run_analysis(self, papers: List[Paper],
                      summaries: Optional[List[Dict[str, Any]]],
                      limit: Optional[int] = None,
                      event_callback: Optional[Callable[[Dict[str, Any]], None]] = None
                      ) -> Dict[str, Any]:
        """跨文献分析。

        输入画像来源优先级：
        1. 已生成的智能摘要（四要素画像，最理想）
        2. 元数据（标题 + 年份 + 摘要截断）
        """
        targets = papers[:limit] if limit else papers
        profiles: List[Dict[str, Any]] = []
        for i, p in enumerate(targets, 1):
            sm = None
            if summaries and i - 1 < len(summaries):
                cand = summaries[i - 1]
                if cand.get("ok") and cand.get("summary"):
                    sm = cand["summary"]
            profiles.append({
                "index": i,
                "title": p.title,
                "year": p.year,
                "source": p.source,
                "problem": (sm or {}).get("problem") or (p.abstract or "")[:500],
                "method": (sm or {}).get("method") or "",
                "contribution": (sm or {}).get("contribution") or "",
                "limitation": (sm or {}).get("limitation") or "",
                "keywords": (sm or {}).get("keywords") or [],
            })

        print(f"[分析] 对 {len(profiles)} 篇论文进行跨文献对比 ...")
        result = self.analyzer.analyze(profiles)
        if result.get("_fallback"):
            print("[分析] 模型综合不可用，已生成本地结构化分析")
            self._event(event_callback, "analysis_output", "跨文献分析输出", {
                "analysis": result, "fallback": True,
            })
            return result
        n_consensus = len(result.get("consensus", []))
        n_conflicts = len(result.get("conflicts", []))
        n_gaps = len(result.get("gaps", []))
        print(f"[分析] 完成：共识 {n_consensus} 条 | "
              f"分歧 {n_conflicts} 条 | 盲点 {n_gaps} 条")
        self._event(event_callback, "analysis_output", "跨文献分析输出", {
            "analysis": result, "fallback": False,
        })
        return result
